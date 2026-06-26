# AGENTS.md — ttigdl

Guidance for AI agents working in this repo.

## What this is

`ttigdl` is a single-file Python CLI (`ttigdl.py`) that downloads TikTok and
Instagram videos (single / batch / whole-profile) by shelling out to the
`yt-dlp` binary or module. It is a thin, well-tested wrapper — the heavy lifting
(extraction, watermark-free formats, impersonation) is yt-dlp's job.

## Architecture (one file)

`ttigdl.py`, top to bottom:

- `find_ytdlp()` — resolves how to call yt-dlp. **Prefers `python -m yt_dlp`
  from the current interpreter** (so a fresh `pip install` wins over a stale
  system binary), falls back to a `yt-dlp` binary on PATH.
- `read_url_file()` / `is_supported_url()` / `collect_urls()` — gather, dedupe
  (order-preserving), and validate input URLs (TikTok + Instagram; see
  `SUPPORTED_HOSTS`).
- `build_base_command()` — translate parsed args into a yt-dlp argument list.
  **This is the core mapping**; every CLI flag is wired here.
- `run_streaming()` / `run_captured()` — one yt-dlp subprocess per URL.
  Streaming (jobs==1) inherits stdio for live progress; captured (jobs>1)
  hides output and extracts the last error line.
- `download_all()` — sequential (live) or `ThreadPoolExecutor` (concurrent).
- `build_parser()` / `main()` — argparse + orchestration + summary.

There is no shared mutable yt-dlp state: each URL is an independent process,
which keeps concurrency simple and per-URL success/failure accurate.

## Key design decisions

- **Wrap the binary, not the library.** yt-dlp's internal Python API churns;
  its CLI flags are stable. We depend only on flags.
- **One process per URL** so the summary can report exactly which URLs failed.
  A profile URL is one process that yt-dlp expands into a playlist internally.
- **yt-dlp freshness is load-bearing.** TikTok breaks old extractors. If live
  downloads fail with "Unable to extract webpage video data", the fix is almost
  always `pip install -U "yt-dlp[default]"`, not a code change.

## Testing

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt pytest
./.venv/bin/python -m pytest tests/ -q
```

Tests in `tests/test_ttigdl.py` cover the **pure logic** (URL parsing/validation,
dedup, command building, and the download dispatcher with mocked subprocesses).
They do **not** hit the network. When adding a flag:

1. Add the `p.add_argument(...)` in `build_parser()`.
2. Wire it into `build_base_command()` (if it maps to a yt-dlp flag).
3. Add the field to `make_args()` defaults in the test file.
4. Add a `build_base_command` assertion test.

For a real end-to-end check, run a live `--simulate` (or a real download to a
`/tmp` dir) against a current public TikTok URL, then clean up.

## Gotchas

- `args.extra` is an `append` list (default `[]`); the test helper must include
  it or `build_base_command` raises `AttributeError`.
- `--audio` requires `ffmpeg` on PATH.
- `jobs > 1` forces quiet/captured mode in `main()`.
