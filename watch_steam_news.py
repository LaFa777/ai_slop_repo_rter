#!/usr/bin/env -S uv run
"""Watch Steam RSS news feeds; print new items as JSON grouped by game title.

Usage:
    uv run watch_steam_news.py
    uv run watch_steam_news.py --config my-feeds.json

First run records a baseline (emits nothing). Subsequent runs emit only items
whose <guid> isn't in the watermark.

Config format (steam-rss-config.json):
    {
      "feeds": [
        {
          "app_id": 1868140,
          "ignore_patterns": []
        }
      ],
      "ignore_patterns": ["обновление", "update", "patch", "hotfix"]
    }
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent))
from utils.watermark import Watermark

ATOM_NS = "http://www.w3.org/2005/Atom"
MEDIA_NS = "http://search.yahoo.com/mrss/"
STEAM_TITLE_RE = re.compile(r"<title>([^<]+?)\s+on Steam</title>")


@dataclass
class FeedConfig:
    app_id: int
    ignore_patterns: list[str] = field(default_factory=list)

    @property
    def rss_url(self) -> str:
        return f"https://store.steampowered.com/feeds/news/app/{self.app_id}/?cc=US&l=english"

    @property
    def app_url(self) -> str:
        return f"https://store.steampowered.com/app/{self.app_id}/"


@dataclass
class SteamRSSConfig:
    global_ignore_patterns: list[str] = field(default_factory=list)
    feeds: list[FeedConfig] = field(default_factory=list)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _fetch(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Steam-Watcher/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_game_title(store_url: str, timeout: float = 20) -> str:
    html = _fetch(store_url, timeout).decode("utf-8", errors="replace")
    m = STEAM_TITLE_RE.search(html)
    if m:
        return m.group(1).strip()
    return Path(store_url.rstrip("/")).name


def _extract_store_url(root: ET.Element) -> str | None:
    """Extract steam store URL from <atom:link rel="self"> by stripping /news."""
    for link_el in root.iter(f"{{{ATOM_NS}}}link"):
        if link_el.attrib.get("rel") == "self":
            href = link_el.attrib.get("href", "")
            return re.sub(r"/news(?=/|$)", "", href).rstrip("/")
    return None


def _parse_rss_item(item_el: ET.Element) -> dict:
    """Convert RSS <item> to a flat dict with all child tags + image."""
    data = {}
    for child in item_el:
        tag = _strip_ns(child.tag)

        if tag == "enclosure":
            url = child.attrib.get("url", "")
            typ = child.attrib.get("type", "")
            if url and "image" in data.get("image", "") or "image" in typ:
                data["image"] = url

        elif tag in ("guid", "title"):
            text = (child.text or "").strip()
            if text:
                data[tag] = text

        elif tag == "link":
            href = child.attrib.get("href") or (child.text or "").strip()
            if href:
                data["link"] = href

        elif tag == "description":
            text = (child.text or "").strip()
            if text:
                data["description"] = text

        elif child.text and child.text.strip():
            data[tag] = child.text.strip()

    # media:content (image)
    for mc in item_el.iter(f"{{{MEDIA_NS}}}content"):
        medium = mc.attrib.get("medium", "")
        typ = mc.attrib.get("type", "")
        url = mc.attrib.get("url", "")
        if url and (medium == "image" or "image" in typ) and "image" not in data:
            data["image"] = url

    # media:thumbnail (fallback)
    for mt in item_el.iter(f"{{{MEDIA_NS}}}thumbnail"):
        url = mt.attrib.get("url", "")
        if url and "image" not in data:
            data["image"] = url

    # Normalize GUID: extract numeric from /view/NNNNN
    guid = data.get("guid", "")
    m = re.search(r"/view/(\d+)", guid)
    if m:
        data["guid"] = m.group(1)

    return data


def _should_ignore(item: dict, patterns: list[str]) -> bool:
    if not patterns:
        return False
    text = f"{item.get('title', '')} {item.get('description', '')}"
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ── Main ──


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Watch Steam news RSS feeds.")
    p.add_argument("--config", default="steam-rss-config.json", help="Config file")
    p.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout (s)")
    args = p.parse_args()

    # ── Load config ──
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    raw = json.loads(config_path.read_text("utf-8"))
    config = SteamRSSConfig(
        global_ignore_patterns=raw.get("ignore_patterns", []),
        feeds=[FeedConfig(**f) for f in raw["feeds"]],
    )

    # ── Watermark ──
    wm = Watermark.load("steam-news")
    app_titles: dict[str, str] = wm._data.get("app_titles", {})

    output: dict[str, list[dict]] = {}

    for feed in config.feeds:
        app_id_str = str(feed.app_id)

        # Merge global + per-app ignore patterns
        ignore_patterns = config.global_ignore_patterns + feed.ignore_patterns

        # 1. Fetch RSS XML
        try:
            xml_bytes = _fetch(feed.rss_url, args.timeout)
        except Exception as e:
            print(f"RSS fetch error (app {feed.app_id}): {e}", file=sys.stderr)
            continue

        # 2. Parse XML
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            print(f"XML parse error (app {feed.app_id}): {e}", file=sys.stderr)
            continue

        # 3. Get game title (cached in watermark)
        if app_id_str not in app_titles:
            store_url = _extract_store_url(root) or feed.app_url
            try:
                title = _get_game_title(store_url, args.timeout)
            except Exception as e:
                print(f"Title fetch error (app {feed.app_id}): {e}", file=sys.stderr)
                title = f"App {feed.app_id}"
            app_titles[app_id_str] = title

        game_title = app_titles[app_id_str]

        # 4. Parse items, filter by ignore patterns
        items = []
        for item_el in root.iter():
            tag = _strip_ns(item_el.tag)
            if tag != "item":
                continue
            item = _parse_rss_item(item_el)
            if not item.get("guid"):
                item["guid"] = item.get("link", "")
            if not item["guid"]:
                continue
            if _should_ignore(item, ignore_patterns):
                continue
            items.append(item)

        # 5. Dedup via watermark
        new_items = wm.filter_new(items, id_key="guid")

        if new_items:
            output.setdefault(game_title, []).extend(new_items)

    # Persist watermark with cached titles
    wm._data["app_titles"] = app_titles
    wm.save()

    # 6. Output JSON
    if output:
        print(json.dumps(output, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
