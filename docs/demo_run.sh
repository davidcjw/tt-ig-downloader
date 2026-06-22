#!/usr/bin/env bash
# Scripted, real ttdl session for the README demo recording.
# Honest: every command actually runs (one real download).
export PATH="$HOME/.local/bin:$PATH"
set -u

DEMODIR="$(mktemp -d)"
PS1_PROMPT="\033[36m$\033[0m"

run() {
  printf "%b %s\n" "$PS1_PROMPT" "$1"
  sleep 0.8
  eval "$1"
  echo
  sleep 1.0
}

printf "\033[1;36m  ttdl \033[0m\342\200\224 TikTok video downloader (single / batch / profile)\n\n"
sleep 1.2
run "ttdl --version"
run "ttdl --help | sed -n '1,6p'"
run "ttdl https://www.tiktok.com/@tiktok/video/7652870361331043614 -o $DEMODIR"
run "ls -lh $DEMODIR/tiktok"
sleep 1.0
rm -rf "$DEMODIR"
