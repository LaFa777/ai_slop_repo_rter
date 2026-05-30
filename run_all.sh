#!/usr/bin/env bash
set -euo pipefail

uv run watch_steam_news.py || echo "[WARN] watch_steam_news.py exited with code $?" >&2
uv run online-fix-json.py || echo "[WARN] online-fix-json.py exited with code $?" >&2
