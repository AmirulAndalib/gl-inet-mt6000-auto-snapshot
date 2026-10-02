"""Unit tests for scripts/process_releases.py. No network access."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import process_releases as pr


def api_entry(version, stage, name, compile_time, sha="a" * 64, size=10):
    return {
        "version": version,
        "stage": stage,
        "release_note": "<h1>Notes</h1><p>Fixes</p>",
        "download": [
            {
                "name": name,
                "link": f"https://fw.example/{name}",
                "sha256": sha,
                "size": size,
                "compile_time": compile_time,
            }
        ],
    }


def empty_state(**kw):
    return pr.PublishedState(
        tags=set(kw.get("tags", ())),
        asset_names=set(kw.get("assets", ())),
        sha256s=set(kw.get("shas", ())),
    )


class TestInferStage:
    def test_keeps_api_stage(self):
        assert pr.infer_stage("snapshot", "x.bin") == "SNAPSHOT"

    def test_null_stage_release_filename(self):
        # 4.9.0 release5 came back with "stage": null and broke the workflow.
        assert (
            pr.infer_stage(None, "mt6000-4.9.0_release5-1079-0729-1785298329.bin")
            == "RELEASE"
        )

    def test_null_stage_beta_filename(self):
        assert pr.infer_stage(None, "mt6000-4.11.1_beta1-1152-0929-1.bin") == "TESTING"

    def test_null_stage_unknown(self):
        assert pr.infer_stage("", "mt6000-4.9.1-1094-1002-1.bin") == "UNKNOWN"


class TestParse:
    def test_sorted_and_malformed_skipped(self):
        builds = pr.parse_firmware_list(
            [
                api_entry("2", "RELEASE", "b.bin", 200),
                api_entry("1", None, "a_release.bin", 100),
                {"version": "3", "stage": "SNAPSHOT", "download": []},
                {"version": "4", "download": [{"name": "bad.bin"}]},
                "garbage",
                api_entry("5", "RELEASE", "../evil.bin", 300),
            ]
        )
        assert [b.filename for b in builds] == ["a_release.bin", "b.bin"]
        assert builds[0].stage == "RELEASE"

    def test_sha_lowercased(self):
        (build,) = pr.parse_firmware_list(
            [api_entry("1", "RELEASE", "a.bin", 1, sha="AB" * 32)]
        )
        assert build.sha256 == "ab" * 32


class TestTags:
    def build(self, stage="RELEASE", ct=1774852402):
        (b,) = pr.parse_firmware_list([api_entry("4.8.4", stage, "f.bin", ct)])
        return b

    def test_release_uses_plain_tag(self):
        assert pr.choose_tag(self.build(), empty_state()) == "v4.8.4"

    def test_release_respin_gets_compile_time(self):
        state = empty_state(tags={"v4.8.4"})
        assert pr.choose_tag(self.build(), state) == "v4.8.4-1774852402"

    def test_no_free_tag(self):
        state = empty_state(tags={"v4.8.4", "v4.8.4-1774852402"})
        assert pr.choose_tag(self.build(), state) is None

    def test_snapshot_tag(self):
        assert (
            pr.choose_tag(self.build("SNAPSHOT", 5), empty_state())
            == "v4.8.4-snapshot-5"
        )

    def test_titles(self):
        b = self.build()
        assert pr.release_title(b, "v4.8.4") == "GL-MT6000 Release 4.8.4"
        assert pr.release_title(b, "v4.8.4-1774852402").startswith(
            "GL-MT6000 Release 4.8.4 (2026-"
        )


class TestPublished:
    def test_by_sha_or_name(self):
        (b,) = pr.parse_firmware_list([api_entry("1", "RELEASE", "a.bin", 1)])
        assert pr.is_published(b, empty_state(shas={b.sha256}))
        assert pr.is_published(b, empty_state(assets={"a.bin"}))
        assert not pr.is_published(b, empty_state())


class TestProcess:
    @pytest.fixture(autouse=True)
    def tmp_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pr, "HISTORY_FILE", tmp_path / "history.json")

    def test_dry_run_plan(self):
        builds = pr.parse_firmware_list(
            [
                api_entry("4.8.4", "RELEASE", "old.bin", 100, sha="1" * 64),
                api_entry("4.8.4", "RELEASE", "respin.bin", 200, sha="2" * 64),
                api_entry("4.9.0", None, "x_release5.bin", 300, sha="3" * 64),
                api_entry("4.9.1", "SNAPSHOT", "snap.bin", 400, sha="4" * 64),
            ]
        )
        state = empty_state(tags={"v4.8.4"}, assets={"old.bin"})
        history: list[str] = []
        created, failed = pr.process_firmware_releases(
            builds, state, history, "o/r", None, True
        )
        assert (created, failed) == (3, 0)
        assert {"v4.8.4-200", "v4.9.0", "v4.9.1-snapshot-400"} <= state.tags
        assert history == ["1" * 64]
        assert not pr.HISTORY_FILE.exists()

    def test_failure_isolated_and_history_saved(self, monkeypatch):
        builds = pr.parse_firmware_list(
            [
                api_entry("1", "RELEASE", "bad.bin", 1, sha="1" * 64),
                api_entry("2", "RELEASE", "good.bin", 2, sha="2" * 64),
            ]
        )
        calls = []

        def fake_download(session, build, dest):
            return build.filename != "bad.bin"

        def fake_create(build, tag, make_latest, repo, asset):
            calls.append((tag, make_latest))
            return True

        monkeypatch.setattr(pr, "download_firmware", fake_download)
        monkeypatch.setattr(pr, "create_github_release", fake_create)
        history: list[str] = []
        created, failed = pr.process_firmware_releases(
            builds, empty_state(), history, "o/r", None, False
        )
        assert (created, failed) == (1, 1)
        assert calls == [("v2", True)]
        assert pr.load_release_history() == ["2" * 64]


class TestDownload:
    class FakeResponse:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield self.data

    class FakeSession:
        def __init__(self, data):
            self.data = data

        def get(self, *a, **kw):
            return TestDownload.FakeResponse(self.data)

    def make(self, data, sha=None, size=None):
        (b,) = pr.parse_firmware_list(
            [
                api_entry(
                    "1",
                    "RELEASE",
                    "a.bin",
                    1,
                    sha=sha or hashlib.sha256(data).hexdigest(),
                    size=len(data) if size is None else size,
                )
            ]
        )
        return b

    def test_ok(self, tmp_path):
        data = b"firmware"
        assert pr.download_firmware(
            self.FakeSession(data), self.make(data), tmp_path / "f"
        )

    def test_checksum_mismatch(self, tmp_path):
        b = self.make(b"x", sha="0" * 64)
        assert not pr.download_firmware(self.FakeSession(b"x"), b, tmp_path / "f")

    def test_size_mismatch(self, tmp_path):
        b = self.make(b"x", size=99)
        assert not pr.download_firmware(self.FakeSession(b"x"), b, tmp_path / "f")


def test_markdown_conversion():
    assert pr.convert_html_to_markdown(None) == "No release notes provided."
    out = pr.convert_html_to_markdown("<h1>V1</h1><p>a</p><br><br><br><p>b</p>")
    assert out.startswith("# V1")
    assert "\n\n\n" not in out


def test_format_timestamp():
    assert pr.format_timestamp(0) == "1970-01-01 00:00:00 UTC"
    assert pr.format_timestamp("bad") == "Unknown"
