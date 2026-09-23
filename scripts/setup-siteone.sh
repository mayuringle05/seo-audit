#!/usr/bin/env bash
set -euo pipefail
if command -v siteone-crawler >/dev/null 2>&1; then
  command -v siteone-crawler
  exit 0
fi
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Install a native binary from https://github.com/janreges/siteone-crawler/releases and put siteone-crawler on PATH.' >&2
  exit 2
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is required for this installer. Install it from https://brew.sh, or use the official SiteOne binary.' >&2
  exit 2
fi
brew install janreges/tap/siteone-crawler
command -v siteone-crawler
