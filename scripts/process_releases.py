#!/usr/bin/env python3
"""
GL.iNet MT6000 Firmware Release Processor

Fetches firmware information from the GL.iNet API and publishes every build
it has not seen before as a GitHub release, with the firmware binary attached.

A build counts as "already published" when its SHA256 is in
release_history.json OR matches the digest of an asset already attached to a
release in the repository OR its filename is already attached to a release.
Using GitHub as a second source of truth lets the script heal itself if the
history file ever falls behind (for example when a run creates a release and
then fails before the history commit).

Environment variables:
    MODEL              Router model identifier (default: mt6000)
    GITHUB_REPOSITORY  Repository in owner/repo form (set by GitHub Actions)
    GH_TOKEN           Token used by the gh CLI and the GitHub REST API
    DRY_RUN            When "1"/"true", print the plan and change nothing
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from markdownify import markdownify
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configuration
MODEL = os.getenv("MODEL", "mt6000")
API_URL = f"https://firmware-api.gl-inet.com/cloud-api/model/info?model={MODEL}"
GITHUB_API = "https://api.github.com"
HISTORY_FILE = Path("release_history.json")
REQUEST_TIMEOUT = 60
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class Firmware:
    """One downloadable firmware build as reported by the GL.iNet API."""

    version: str
    stage: str
    compile_time: int
    filename: str
    link: str
    sha256: str
    size: int
    release_note: str | None

    @property
    def is_release(self) -> bool:
        return self.stage == "RELEASE"


@dataclass
class PublishedState:
    """What already exists on GitHub plus the local history file."""

    tags: set[str]
    asset_names: set[str]
    sha256s: set[str]


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")


def make_session() -> requests.Session:
    """HTTP session that retries transient network and server errors."""
    retry = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


# --------------------------------------------------------------------------
# History file
# --------------------------------------------------------------------------


def load_release_history() -> list[str]:
    """Load the list of previously released firmware SHA256 hashes."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load release history: %s", e)
        return []
    if not isinstance(data, list):
        logger.warning("Release history is not a JSON list, ignoring it")
        return []
    return [str(item) for item in data]


def save_release_history(history: list[str]) -> None:
    """Write the history file with a trailing newline."""
    with HISTORY_FILE.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(history, f, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------
# API parsing
# --------------------------------------------------------------------------


def infer_stage(stage: Any, filename: str) -> str:
    """
    Return an upper-case stage name.

    The API has been seen returning "stage": null (4.9.0 release5). In that
    case fall back to the marker GL.iNet puts in the filename.
    """
    if isinstance(stage, str) and stage.strip():
        return stage.strip().upper()

    name = filename.lower()
    if "_release" in name:
        inferred = "RELEASE"
    elif "_beta" in name or "_rc" in name:
        inferred = "TESTING"
    else:
        inferred = "UNKNOWN"
    logger.warning(
        "Firmware %s has no stage in the API response, using %s", filename, inferred
    )
    return inferred


def parse_firmware_list(entries: list[Any]) -> list[Firmware]:
    """Turn raw API entries into Firmware objects, skipping malformed ones."""
    builds: list[Firmware] = []
    for entry in entries:
        if not isinstance(entry, dict):
            logger.warning("Skipping non-object API entry: %r", entry)
            continue
        downloads = entry.get("download") or []
        version = entry.get("version")
        if not version or not downloads:
            logger.warning(
                "Skipping API entry without version or download: %r",
                entry.get("version"),
            )
            continue
        for dl in downloads:
            try:
                build = Firmware(
                    version=str(version),
                    stage=infer_stage(entry.get("stage"), str(dl["name"])),
                    compile_time=int(dl["compile_time"]),
                    filename=str(dl["name"]),
                    link=str(dl["link"]),
                    sha256=str(dl["sha256"]).lower(),
                    size=int(dl["size"]),
                    release_note=entry.get("release_note"),
                )
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed download for %s: %s", version, e)
                continue
            if "/" in build.filename or "\\" in build.filename:
                logger.warning(
                    "Skipping download with unsafe filename: %s", build.filename
                )
                continue
            builds.append(build)
    builds.sort(key=lambda b: b.compile_time)
    return builds


def fetch_firmware_data(session: requests.Session) -> dict[str, Any] | None:
    """Fetch firmware information from the GL.iNet API."""
    logger.info("Fetching firmware info for %s", MODEL)
    try:
        response = session.get(API_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("API request failed: %s", e)
        return None
    if not isinstance(data, dict):
        logger.error("Unexpected API response type: %s", type(data).__name__)
        return None
    return data


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def format_timestamp(timestamp: int | str) -> str:
    """Convert Unix timestamp to human-readable UTC datetime string."""
    try:
        ts = int(timestamp)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError, OverflowError):
        return "Unknown"


def convert_html_to_markdown(html_content: str | None) -> str:
    """Convert HTML release notes to clean Markdown format."""
    if not html_content:
        return "No release notes provided."

    markdown = markdownify(html_content, heading_style="ATX")
    result = "\n".join(line.rstrip() for line in markdown.strip().split("\n"))
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip()


def candidate_tags(build: Firmware) -> list[str]:
    """
    Tags to try, in order.

    Stable builds get the plain version tag. GL.iNet sometimes republishes a
    stable version with a new binary (for example 4.8.4 release1 then
    release2); those respins get the compile time appended.
    """
    if build.is_release:
        return [f"v{build.version}", f"v{build.version}-{build.compile_time}"]
    return [f"v{build.version}-{build.stage.lower()}-{build.compile_time}"]


def release_title(build: Firmware, tag: str) -> str:
    title = f"GL-MT6000 {build.stage.title()} {build.version}"
    if not build.is_release or tag != f"v{build.version}":
        title += f" ({format_timestamp(build.compile_time)})"
    return title


def generate_release_body(build: Firmware, title: str) -> str:
    """Generate the release body with firmware details."""
    size_mb = round(build.size / (1024 * 1024), 2)
    formatted_time = format_timestamp(build.compile_time)
    release_notes = convert_html_to_markdown(build.release_note)

    return f"""# {title}

{release_notes}

---

### Firmware Details

| Property | Value |
| :--- | :--- |
| **Model** | {MODEL} |
| **Version** | {build.version} |
| **Channel** | {build.stage} |
| **Compile Time** | {formatted_time} |
| **File Name** | `{build.filename}` |
| **File Size** | {size_mb} MB |
| **SHA256** | `{build.sha256}` |

### Verification

Verify the downloaded file integrity:

```bash
echo "{build.sha256}  {build.filename}" | sha256sum -c -
```
"""


# --------------------------------------------------------------------------
# GitHub state
# --------------------------------------------------------------------------


def add_release_to_state(state: PublishedState, release: dict[str, Any]) -> None:
    """
    Record one GitHub release.

    The tag always blocks reuse. Assets only count as published when the
    release is not a draft and the upload finished; otherwise a half-failed
    upload would mark the build as done and it would never be retried.
    """
    state.tags.add(release["tag_name"])
    if release.get("draft"):
        return
    for asset in release.get("assets", []):
        if asset.get("state", "uploaded") != "uploaded":
            continue
        state.asset_names.add(asset["name"])
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            state.sha256s.add(digest.split(":", 1)[1].lower())


def fetch_published_state(
    session: requests.Session, repo: str, token: str | None, history: list[str]
) -> PublishedState:
    """Collect existing tags, asset names and asset digests from GitHub."""
    state = PublishedState(tags=set(), asset_names=set(), sha256s=set(history))

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url: str | None = f"{GITHUB_API}/repos/{repo}/releases?per_page=100"
    while url:
        response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        for release in response.json():
            add_release_to_state(state, release)
        url = response.links.get("next", {}).get("url")

    # Releases can exist without a release object only in odd cases, but a
    # bare tag still blocks `gh release create` from reusing the name.
    url = f"{GITHUB_API}/repos/{repo}/tags?per_page=100"
    while url:
        response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        state.tags.update(tag["name"] for tag in response.json())
        url = response.links.get("next", {}).get("url")

    logger.info(
        "GitHub has %d tags and %d assets; %d known SHA256 values",
        len(state.tags),
        len(state.asset_names),
        len(state.sha256s),
    )
    return state


def is_published(build: Firmware, state: PublishedState) -> bool:
    return build.sha256 in state.sha256s or build.filename in state.asset_names


def choose_tag(build: Firmware, state: PublishedState) -> str | None:
    for tag in candidate_tags(build):
        if tag not in state.tags:
            return tag
    return None


# --------------------------------------------------------------------------
# Download and publish
# --------------------------------------------------------------------------


def download_firmware(session: requests.Session, build: Firmware, dest: Path) -> bool:
    """Download the build and check its size and SHA256 against the API."""
    logger.info("Downloading %s", build.filename)
    digest = hashlib.sha256()
    written = 0
    try:
        with session.get(build.link, stream=True, timeout=REQUEST_TIMEOUT) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
    except (requests.RequestException, OSError) as e:
        logger.error("Download failed for %s: %s", build.filename, e)
        return False

    actual = digest.hexdigest()
    if actual != build.sha256:
        logger.error(
            "Checksum mismatch for %s: expected %s, got %s",
            build.filename,
            build.sha256,
            actual,
        )
        return False
    if build.size and written != build.size:
        logger.error(
            "Size mismatch for %s: expected %d, got %d",
            build.filename,
            build.size,
            written,
        )
        return False
    return True


def create_github_release(
    build: Firmware, tag: str, make_latest: bool, repo: str, asset: Path
) -> bool:
    title = release_title(build, tag)
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        str(asset),
        "--title",
        title,
        "--notes",
        generate_release_body(build, title),
        "--repo",
        repo,
        f"--latest={'true' if make_latest else 'false'}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to create release %s: %s", tag, (e.stderr or "").strip())
        return False
    logger.info("Created release %s", tag)
    return True


def process_firmware_releases(
    builds: list[Firmware],
    state: PublishedState,
    history: list[str],
    repo: str,
    session: requests.Session,
    dry_run: bool,
) -> tuple[int, int]:
    """Publish unseen builds. Returns (created, failed)."""
    newest = max((b.compile_time for b in builds), default=None)
    created = failed = 0

    for build in builds:
        label = f"{build.version} {build.stage} ({build.filename})"

        if is_published(build, state):
            if build.sha256 not in history:
                logger.info("Recording %s, already on GitHub", label)
                history.append(build.sha256)
                if not dry_run:
                    save_release_history(history)
            else:
                logger.info("Skipping %s, already released", label)
            continue

        tag = choose_tag(build, state)
        if tag is None:
            logger.error("No free tag for %s, tried %s", label, candidate_tags(build))
            failed += 1
            continue

        make_latest = build.compile_time == newest
        if dry_run:
            logger.info(
                "DRY RUN: would create %s for %s (latest=%s, title=%r)",
                tag,
                label,
                make_latest,
                release_title(build, tag),
            )
            state.tags.add(tag)
            created += 1
            continue

        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / build.filename
            ok = download_firmware(session, build, asset) and create_github_release(
                build, tag, make_latest, repo, asset
            )
        if not ok:
            failed += 1
            continue

        state.tags.add(tag)
        state.asset_names.add(build.filename)
        state.sha256s.add(build.sha256)
        history.append(build.sha256)
        save_release_history(history)
        created += 1

    return created, failed


def main() -> int:
    dry_run = is_dry_run()
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        logger.error("GITHUB_REPOSITORY environment variable not set")
        return 1

    session = make_session()
    data = fetch_firmware_data(session)
    if not data:
        return 1
    entries = data.get("info")
    if not isinstance(entries, list):
        logger.error("No firmware info list in API response: %r", data.get("msg"))
        return 1

    builds = parse_firmware_list(entries)
    if not builds:
        logger.error("API response contained no usable firmware builds")
        return 1

    history = load_release_history()
    try:
        state = fetch_published_state(
            session,
            repo,
            os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
            history,
        )
    except requests.RequestException as e:
        logger.error("Could not read existing releases from GitHub: %s", e)
        return 1

    created, failed = process_firmware_releases(
        builds, state, history, repo, session, dry_run
    )

    verb = "Would create" if dry_run else "Created"
    logger.info("%s %d release(s), %d failure(s)", verb, created, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
