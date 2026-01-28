#!/usr/bin/env python3
"""
GL.iNet MT6000 Firmware Release Processor

This script fetches firmware information from the GL.iNet API and creates
GitHub releases for new firmware versions. It tracks previously released
firmware using SHA256 checksums to avoid duplicate releases.

Requirements:
    - Python 3.8+
    - requests
    - markdownify

Environment Variables:
    - MODEL: Router model identifier (default: mt6000)
    - GITHUB_REPOSITORY: GitHub repository in owner/repo format
    - GH_TOKEN: GitHub token for authentication (used by gh CLI)
"""

import datetime
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from markdownify import markdownify

# Configuration
MODEL = os.getenv("MODEL", "mt6000")
API_URL = f"https://firmware-api.gl-inet.com/cloud-api/model/info?model={MODEL}"
HISTORY_FILE = Path("release_history.json")
REQUEST_TIMEOUT = 30
DOWNLOAD_CHUNK_SIZE = 8192

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_release_history() -> list[str]:
    """Load the list of previously released firmware SHA256 hashes."""
    if not HISTORY_FILE.exists():
        return []

    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load release history: %s", e)

    return []


def save_release_history(history: list[str]) -> None:
    """Save the list of released firmware SHA256 hashes."""
    try:
        with HISTORY_FILE.open("w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except OSError as e:
        logger.error("Failed to save release history: %s", e)
        raise


def format_timestamp(timestamp: int | str) -> str:
    """Convert Unix timestamp to human-readable UTC datetime string."""
    try:
        ts = int(timestamp)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError, OSError):
        return "Unknown"


def convert_html_to_markdown(html_content: str | None) -> str:
    """Convert HTML release notes to clean Markdown format."""
    if not html_content:
        return "No release notes provided."

    markdown = markdownify(html_content, heading_style="ATX")
    lines = markdown.strip().split("\n")
    cleaned_lines = [line.rstrip() for line in lines]
    result = "\n".join(cleaned_lines)

    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")

    return result.strip()


def generate_release_tag(version: str, stage: str, compile_time: int) -> str:
    """Generate a unique release tag based on version and stage."""
    stage_upper = stage.upper()

    if stage_upper == "RELEASE":
        return f"v{version}"

    return f"v{version}-{stage.lower()}-{compile_time}"


def generate_release_body(
    firmware: dict[str, Any],
    download_info: dict[str, Any],
) -> str:
    """Generate the release body with firmware details."""
    version = firmware["version"]
    stage = firmware["stage"].upper()
    compile_time = download_info["compile_time"]
    size_bytes = download_info["size"]
    sha256 = download_info["sha256"]
    filename = download_info["name"]

    size_mb = round(size_bytes / (1024 * 1024), 2)
    formatted_time = format_timestamp(compile_time)
    release_notes = convert_html_to_markdown(firmware.get("release_note"))

    title = f"GL-MT6000 {stage.title()} {version}"
    if stage != "RELEASE":
        title += f" ({formatted_time})"

    body = f"""# {title}

{release_notes}

---

### Firmware Details

| Property | Value |
| :--- | :--- |
| **Model** | {MODEL} |
| **Version** | {version} |
| **Channel** | {stage} |
| **Compile Time** | {formatted_time} |
| **File Size** | {size_mb} MB |
| **SHA256** | `{sha256}` |

### Verification

Verify the downloaded file integrity:

```bash
echo "{sha256}  {filename}" | sha256sum -c -
```
"""
    return body


def download_firmware(url: str, filename: str) -> bool:
    """Download firmware file from the specified URL."""
    logger.info("Downloading %s", filename)

    try:
        response = requests.get(url, stream=True, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        with open(filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)

        return True

    except requests.RequestException as e:
        logger.error("Download failed: %s", e)
        return False


def create_github_release(
    firmware: dict[str, Any],
    download_info: dict[str, Any],
) -> bool:
    """Create a GitHub release with the firmware file attached."""
    version = firmware["version"]
    stage = firmware["stage"].upper()
    compile_time = download_info["compile_time"]
    filename = download_info["name"]
    url = download_info["link"]

    tag_name = generate_release_tag(version, stage, compile_time)

    release_title = f"GL-MT6000 {stage.title()} {version}"
    if stage != "RELEASE":
        release_title += f" ({format_timestamp(compile_time)})"

    logger.info("Processing: %s", tag_name)

    if not download_firmware(url, filename):
        return False

    release_body = generate_release_body(firmware, download_info)

    github_repo = os.environ.get("GITHUB_REPOSITORY")
    if not github_repo:
        logger.error("GITHUB_REPOSITORY environment variable not set")
        return False

    cmd = [
        "gh",
        "release",
        "create",
        tag_name,
        filename,
        "--title",
        release_title,
        "--notes",
        release_body,
        "--repo",
        github_repo,
        "--latest",
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info("Successfully created release: %s", tag_name)

        Path(filename).unlink(missing_ok=True)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("Failed to create release %s: %s", tag_name, e.stderr)
        Path(filename).unlink(missing_ok=True)
        return False


def fetch_firmware_data() -> dict[str, Any] | None:
    """Fetch firmware information from the GL.iNet API."""
    logger.info("Fetching firmware info for %s", MODEL)

    try:
        response = requests.get(API_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        logger.error("API request failed: %s", e)
        return None

    except json.JSONDecodeError as e:
        logger.error("Failed to parse API response: %s", e)
        return None


def process_firmware_releases(firmware_list: list[dict[str, Any]]) -> int:
    """Process firmware list and create releases for new versions."""
    sorted_firmware = sorted(
        firmware_list,
        key=lambda x: x["download"][0]["compile_time"],
    )

    history = load_release_history()
    releases_created = 0

    for firmware in sorted_firmware:
        download_info = firmware["download"][0]
        sha256 = download_info["sha256"]

        if sha256 in history:
            logger.info(
                "Skipping %s (%s) - already released",
                firmware["version"],
                firmware["stage"],
            )
            continue

        if create_github_release(firmware, download_info):
            history.append(sha256)
            save_release_history(history)
            releases_created += 1

    return releases_created


def main() -> int:
    """Main entry point for the release processor."""
    data = fetch_firmware_data()
    if not data:
        return 1

    if "info" not in data:
        logger.error("No firmware info found in API response")
        return 1

    releases_created = process_firmware_releases(data["info"])

    if releases_created == 0:
        logger.info("No new firmware releases found")
    else:
        logger.info("Created %d new release(s)", releases_created)

    return 0


if __name__ == "__main__":
    sys.exit(main())
