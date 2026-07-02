#!/usr/bin/env python3
"""ttigdl - a small, ergonomic TikTok & Instagram video downloader.

Wraps the `yt-dlp` binary (the most reliable engine for both sites) with sane
defaults: watermark-free downloads, clean filenames, batch handling, optional
audio extraction, QuickTime-compatible H.264, a resume archive, and a per-URL
summary.

Examples:
    ttigdl https://www.tiktok.com/@user/video/1234567890
    ttigdl https://www.instagram.com/p/SHORTCODE/ --h264
    ttigdl url1 url2 url3 -o ~/Videos
    ttigdl -f urls.txt -j 5 --archive
    ttigdl https://www.tiktok.com/@someuser --audio       # whole profile -> mp3
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

__version__ = "1.1.0"

# Default filename layout: group by uploader, name by stable video id.
DEFAULT_TEMPLATE = "%(uploader,creator,uploader_id|unknown)s/%(id)s.%(ext)s"


# --------------------------------------------------------------------------- #
# yt-dlp discovery
# --------------------------------------------------------------------------- #
def find_ytdlp() -> list[str]:
    """Return the command prefix used to invoke yt-dlp.

    Prefers `python -m yt_dlp` from the *current* interpreter when the module is
    importable -- this picks up a fresh `pip install yt-dlp` in the active
    (e.g. venv) environment rather than a possibly-stale system binary, which
    matters because TikTok breaks older extractors. Falls back to a `yt-dlp`
    binary on PATH. Exits with install instructions if neither is available.
    """
    try:
        import yt_dlp  # noqa: F401  (probe only)
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    sys.exit(
        "error: yt-dlp not found.\n"
        "  Install it with one of:\n"
        "    pipx install yt-dlp\n"
        "    python3 -m pip install -U yt-dlp\n"
        "    brew install yt-dlp\n"
    )


# --------------------------------------------------------------------------- #
# URL collection / validation
# --------------------------------------------------------------------------- #
def read_url_file(path: str) -> list[str]:
    """Read URLs from a file, one per line; ignore blanks and `#` comments."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        sys.exit(f"error: cannot read url file {path!r}: {exc}")
    urls = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


SUPPORTED_HOSTS = ("tiktok.com", "instagram.com")


def is_supported_url(url: str) -> bool:
    """True for a TikTok or Instagram URL (incl. short links like vm./vt.)."""
    low = url.lower()
    return any(host in low for host in SUPPORTED_HOSTS)


def collect_urls(args: argparse.Namespace) -> list[str]:
    """Merge URLs from positionals + --file, dedupe (order-preserving), validate."""
    raw: list[str] = list(args.urls)
    if args.file:
        raw.extend(read_url_file(args.file))

    seen: set[str] = set()
    ordered: list[str] = []
    for url in raw:
        if url not in seen:
            seen.add(url)
            ordered.append(url)

    if not ordered:
        sys.exit("error: no URLs given. Pass URLs as arguments or via -f/--file.")

    urls: list[str] = []
    for url in ordered:
        if is_supported_url(url) or args.allow_any:
            urls.append(url)
        else:
            print(f"  skip (not a tiktok/instagram url): {url}", file=sys.stderr)
    if not urls:
        sys.exit("error: no valid TikTok/Instagram URLs to download "
                 "(use --allow-any to override).")
    return urls


# --------------------------------------------------------------------------- #
# Command building
# --------------------------------------------------------------------------- #
def build_base_command(args: argparse.Namespace, ytdlp: list[str]) -> list[str]:
    """Build the shared yt-dlp argument list (everything except the URL)."""
    outtmpl = os.path.join(args.output_dir, args.template)
    cmd = list(ytdlp)
    cmd += ["-o", outtmpl]
    cmd += ["--retries", "10", "--fragment-retries", "10"]
    cmd += ["--ignore-errors"]  # keep going past a single failed item in a playlist

    if not args.force:
        cmd += ["--no-overwrites"]
    if args.audio:
        cmd += ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    if args.h264:
        # Prefer Apple/QuickTime-compatible codecs (H.264 video + AAC audio).
        # Some sites (e.g. Instagram) serve higher-res VP9 that QuickTime can't
        # decode; this trades a little resolution for universal playback.
        cmd += ["-S", "vcodec:h264,acodec:aac", "--merge-output-format", "mp4"]
    if args.metadata:
        cmd += ["--write-info-json"]
    if args.thumbnail:
        cmd += ["--write-thumbnail"]
    if args.archive:
        cmd += ["--download-archive", args.archive]
    if args.cookies_from_browser:
        cmd += ["--cookies-from-browser", args.cookies_from_browser]
    if args.cookies:
        cmd += ["--cookies", args.cookies]
    if args.simulate:
        cmd += ["--simulate"]
    if args.quiet:
        cmd += ["--quiet", "--no-warnings"]
    for extra in args.extra:
        cmd += extra.split()
    return cmd


# --------------------------------------------------------------------------- #
# Download execution
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    url: str
    ok: bool
    detail: str = ""


def run_streaming(base_cmd: list[str], url: str) -> Result:
    """Run one yt-dlp process, inheriting stdio so progress shows live."""
    # "--" marks the end of options so a URL beginning with "-" can never be
    # parsed by yt-dlp as an option (e.g. --exec=..., which would run a shell).
    proc = subprocess.run(base_cmd + ["--", url])
    return Result(url, proc.returncode == 0,
                  "" if proc.returncode == 0 else f"exit code {proc.returncode}")


def run_captured(base_cmd: list[str], url: str) -> Result:
    """Run one yt-dlp process quietly, capturing output for a one-line status."""
    # "--" marks the end of options (see run_streaming) to prevent a URL that
    # starts with "-" from being interpreted as a yt-dlp option flag.
    proc = subprocess.run(base_cmd + ["--", url], capture_output=True, text=True)
    if proc.returncode == 0:
        return Result(url, True)
    # Surface the last meaningful error line from yt-dlp.
    detail = f"exit code {proc.returncode}"
    for line in reversed((proc.stderr or "").splitlines()):
        if line.strip():
            detail = line.strip()
            break
    return Result(url, False, detail)


def download_all(urls: list[str], base_cmd: list[str], jobs: int) -> list[Result]:
    """Download every URL, sequentially (live output) or concurrently."""
    results: list[Result] = []
    if jobs <= 1:
        for i, url in enumerate(urls, 1):
            print(f"\n[{i}/{len(urls)}] {url}")
            results.append(run_streaming(base_cmd, url))
        return results

    print(f"Downloading {len(urls)} target(s) with {jobs} concurrent jobs...\n")
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_captured, base_cmd, url): url for url in urls}
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            mark = "ok " if res.ok else "FAIL"
            line = f"  [{done}/{len(urls)}] {mark} {res.url}"
            if not res.ok and res.detail:
                line += f"\n           -> {res.detail}"
            print(line)
            results.append(res)
    return results


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ttigdl",
        description="Download TikTok & Instagram videos (single or batch) via yt-dlp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  ttigdl https://www.tiktok.com/@user/video/123\n"
            "  ttigdl https://www.instagram.com/p/SHORTCODE/ --h264\n"
            "  ttigdl url1 url2 -o ~/Videos -j 4\n"
            "  ttigdl -f urls.txt --archive --metadata\n"
            "  ttigdl https://www.tiktok.com/@user --audio   # whole profile to mp3\n"
        ),
    )
    p.add_argument("urls", nargs="*",
                   help="one or more TikTok/Instagram video/profile URLs")
    p.add_argument("-f", "--file", help="text file of URLs (one per line, # comments)")
    p.add_argument("-o", "--output-dir", default="downloads",
                   help="directory to save into (default: ./downloads)")
    p.add_argument("-t", "--template", default=DEFAULT_TEMPLATE,
                   help="yt-dlp output filename template (advanced)")
    p.add_argument("-j", "--jobs", type=int, default=1,
                   help="concurrent downloads (default: 1 = live progress)")
    p.add_argument("-a", "--audio", action="store_true",
                   help="extract audio as mp3 instead of video")
    p.add_argument("--h264", action="store_true",
                   help="prefer QuickTime/Apple-compatible H.264 video + AAC "
                        "audio (avoids VP9 that QuickTime can't play)")
    p.add_argument("--metadata", action="store_true",
                   help="also write a .info.json sidecar per video")
    p.add_argument("--thumbnail", action="store_true",
                   help="also download the cover thumbnail")
    p.add_argument("--archive", nargs="?", const="ttigdl-archive.txt", default=None,
                   metavar="FILE",
                   help="record downloaded ids to skip them next run "
                        "(default file: ttigdl-archive.txt)")
    p.add_argument("--force", action="store_true",
                   help="re-download even if the file already exists")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="load cookies from a browser, e.g. chrome/safari/firefox "
                        "(for region-locked or private videos)")
    p.add_argument("--cookies", metavar="FILE",
                   help="load cookies from a Netscape-format cookies.txt file")
    p.add_argument("--allow-any", action="store_true",
                   help="allow URLs from other sites (yt-dlp supports many)")
    p.add_argument("-X", "--extra", action="append", default=[], metavar="ARG",
                   help="raw yt-dlp argument(s) to pass through; repeatable, "
                        "e.g. -X '--max-filesize 50M'")
    p.add_argument("-s", "--simulate", action="store_true",
                   help="resolve and validate without downloading")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="suppress yt-dlp output (implied when jobs > 1)")
    p.add_argument("-V", "--version", action="version",
                   version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.jobs > 1:
        args.quiet = True  # captured mode is implicitly quiet

    ytdlp = find_ytdlp()
    urls = collect_urls(args)
    os.makedirs(args.output_dir, exist_ok=True)

    base_cmd = build_base_command(args, ytdlp)
    results = download_all(urls, base_cmd, args.jobs)

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    print(f"\nDone: {len(ok)} ok, {len(failed)} failed (of {len(results)} target(s)).")
    if failed:
        print("Failed targets:")
        for r in failed:
            print(f"  - {r.url}" + (f"  ({r.detail})" if r.detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
