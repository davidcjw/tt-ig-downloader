"""Unit tests for ttdl (no network required)."""
import os
import sys
import argparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ttdl  # noqa: E402


def make_args(**overrides):
    """Build a Namespace with all CLI defaults, overridable per test."""
    defaults = dict(
        urls=[], file=None, output_dir="downloads", template=ttdl.DEFAULT_TEMPLATE,
        jobs=1, audio=False, metadata=False, thumbnail=False, archive=None,
        force=False, cookies_from_browser=None, cookies=None, allow_any=False,
        simulate=False, quiet=False, extra=[],
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ----------------------------- URL validation ------------------------------ #
@pytest.mark.parametrize("url", [
    "https://www.tiktok.com/@user/video/123",
    "https://vm.tiktok.com/ZMabc/",
    "https://vt.tiktok.com/ZSxyz/",
    "https://www.TikTok.com/@user",  # case-insensitive
])
def test_is_tiktok_url_true(url):
    assert ttdl.is_tiktok_url(url) is True


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abc",
    "https://example.com/video",
    "not a url",
])
def test_is_tiktok_url_false(url):
    assert ttdl.is_tiktok_url(url) is False


# ----------------------------- URL file reading ---------------------------- #
def test_read_url_file_skips_blanks_and_comments(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text(
        "# a comment\n"
        "https://www.tiktok.com/@a/video/1\n"
        "\n"
        "   \n"
        "  https://www.tiktok.com/@b/video/2  \n"
        "# trailing comment\n"
    )
    urls = ttdl.read_url_file(str(f))
    assert urls == [
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@b/video/2",
    ]


# ----------------------------- collect_urls -------------------------------- #
def test_collect_urls_dedupes_preserving_order():
    args = make_args(urls=[
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@b/video/2",
        "https://www.tiktok.com/@a/video/1",  # dup
    ])
    assert ttdl.collect_urls(args) == [
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@b/video/2",
    ]


def test_collect_urls_merges_file_and_positionals(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("https://www.tiktok.com/@b/video/2\n")
    args = make_args(urls=["https://www.tiktok.com/@a/video/1"], file=str(f))
    assert ttdl.collect_urls(args) == [
        "https://www.tiktok.com/@a/video/1",
        "https://www.tiktok.com/@b/video/2",
    ]


def test_collect_urls_filters_non_tiktok(capsys):
    args = make_args(urls=[
        "https://www.tiktok.com/@a/video/1",
        "https://youtube.com/watch?v=x",
    ])
    assert ttdl.collect_urls(args) == ["https://www.tiktok.com/@a/video/1"]
    assert "skip" in capsys.readouterr().err


def test_collect_urls_allow_any_keeps_non_tiktok():
    args = make_args(urls=["https://youtube.com/watch?v=x"], allow_any=True)
    assert ttdl.collect_urls(args) == ["https://youtube.com/watch?v=x"]


def test_collect_urls_exits_when_empty():
    with pytest.raises(SystemExit):
        ttdl.collect_urls(make_args(urls=[]))


def test_collect_urls_exits_when_all_filtered():
    with pytest.raises(SystemExit):
        ttdl.collect_urls(make_args(urls=["https://youtube.com/x"]))


# --------------------------- build_base_command ---------------------------- #
def test_build_base_command_defaults():
    args = make_args()
    cmd = ttdl.build_base_command(args, ["yt-dlp"])
    assert cmd[0] == "yt-dlp"
    assert "-o" in cmd
    assert os.path.join("downloads", ttdl.DEFAULT_TEMPLATE) in cmd
    assert "--no-overwrites" in cmd
    assert "--ignore-errors" in cmd


def test_build_base_command_audio_adds_extraction():
    cmd = ttdl.build_base_command(make_args(audio=True), ["yt-dlp"])
    assert "-x" in cmd
    assert "mp3" in cmd


def test_build_base_command_force_omits_no_overwrites():
    cmd = ttdl.build_base_command(make_args(force=True), ["yt-dlp"])
    assert "--no-overwrites" not in cmd


def test_build_base_command_archive_and_metadata():
    cmd = ttdl.build_base_command(
        make_args(archive="arc.txt", metadata=True, thumbnail=True), ["yt-dlp"])
    assert "--download-archive" in cmd and "arc.txt" in cmd
    assert "--write-info-json" in cmd
    assert "--write-thumbnail" in cmd


def test_build_base_command_cookies_and_simulate():
    cmd = ttdl.build_base_command(
        make_args(cookies_from_browser="chrome", simulate=True), ["yt-dlp"])
    assert "--cookies-from-browser" in cmd and "chrome" in cmd
    assert "--simulate" in cmd


def test_build_base_command_extra_passthrough():
    args = make_args()
    args.extra = ["--max-filesize 50M"]
    cmd = ttdl.build_base_command(args, ["yt-dlp"])
    assert "--max-filesize" in cmd and "50M" in cmd


# ------------------------------ download_all ------------------------------- #
def test_download_all_concurrent_reports_results(monkeypatch):
    calls = []

    def fake_captured(base_cmd, url):
        calls.append(url)
        return ttdl.Result(url, ok=("good" in url), detail="" if "good" in url else "boom")

    monkeypatch.setattr(ttdl, "run_captured", fake_captured)
    urls = ["https://www.tiktok.com/good/1", "https://www.tiktok.com/bad/2"]
    results = ttdl.download_all(urls, ["yt-dlp"], jobs=2)
    ok = {r.url for r in results if r.ok}
    assert ok == {"https://www.tiktok.com/good/1"}
    assert set(calls) == set(urls)


def test_download_all_sequential_streams(monkeypatch):
    seen = []
    monkeypatch.setattr(ttdl, "run_streaming",
                        lambda base, url: seen.append(url) or ttdl.Result(url, True))
    urls = ["https://www.tiktok.com/@a/video/1", "https://www.tiktok.com/@a/video/2"]
    results = ttdl.download_all(urls, ["yt-dlp"], jobs=1)
    assert [r.url for r in results] == urls
    assert seen == urls  # preserves order in sequential mode


# Note: build_base_command reads args.extra; main() always sets it via argparse
# default, but the dataclass-free Namespace in tests needs it provided. Ensure
# the default-args helper includes it.
def test_make_args_has_extra_attr_for_command_building():
    cmd = ttdl.build_base_command(make_args(), ["yt-dlp"])
    assert isinstance(cmd, list)
