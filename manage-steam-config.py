#!/usr/bin/env python3
"""Manage steam-rss-config.json: list, add, edit, delete feeds & ignore patterns.

Usage:
    uv run manage-steam-config.py
    uv run manage-steam-config.py --config my-feeds.json

Opens an interactive menu.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class FeedConfig:
    app_id: int
    ignore_patterns: list[str] = field(default_factory=list)


@dataclass
class SteamRSSConfig:
    feeds: list[FeedConfig] = field(default_factory=list)
    ignore_patterns: list[str] = field(default_factory=list)


def _load(path: Path) -> SteamRSSConfig:
    raw = json.loads(path.read_text("utf-8"))
    return SteamRSSConfig(
        feeds=[FeedConfig(**f) for f in raw["feeds"]],
        ignore_patterns=raw.get("ignore_patterns", []),
    )


def _save(path: Path, cfg: SteamRSSConfig) -> None:
    data = asdict(cfg)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK] Saved to {path}")


def _feed_by_id(cfg: SteamRSSConfig, app_id: int) -> FeedConfig | None:
    return next((f for f in cfg.feeds if f.app_id == app_id), None)


def _confirm(prompt: str) -> bool:
    return input(f"{prompt} (y/N) ").strip().lower() == "y"


def cmd_list(path: Path, cfg: SteamRSSConfig) -> None:
    print(f"\nGlobal ignore patterns: {cfg.ignore_patterns or '(none)'}")
    print(f"\nFeeds ({len(cfg.feeds)}):")
    for f in cfg.feeds:
        patterns = ", ".join(f"\"{p}\"" for p in f.ignore_patterns) or "(none)"
        print(f"  {f.app_id}  |  ignore: {patterns}")


def cmd_add(path: Path, cfg: SteamRSSConfig) -> None:
    raw = input("app_id: ").strip()
    if not raw.isdigit():
        print("  [!] app_id must be a number")
        return
    app_id = int(raw)
    if _feed_by_id(cfg, app_id):
        print(f"  [!] app_id {app_id} already exists")
        return

    patterns_raw = input("ignore_patterns (comma-separated, empty=none): ").strip()
    patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()] if patterns_raw else []

    cfg.feeds.append(FeedConfig(app_id=app_id, ignore_patterns=patterns))
    _save(path, cfg)


def cmd_edit(path: Path, cfg: SteamRSSConfig) -> None:
    raw = input("app_id to edit: ").strip()
    if not raw.isdigit():
        print("  [!] app_id must be a number")
        return
    app_id = int(raw)
    feed = _feed_by_id(cfg, app_id)
    if not feed:
        print(f"  [!] app_id {app_id} not found")
        return

    print(f"\nEditing {app_id}:")
    print(f"  Current ignore_patterns: {feed.ignore_patterns}")
    action = input("Action: (a)dd pattern, (r)emove pattern, (c)lear all, (s)kip: ").strip().lower()

    if action == "a":
        p = input("Pattern to add: ").strip()
        if p and p not in feed.ignore_patterns:
            feed.ignore_patterns.append(p)
            _save(path, cfg)
        elif p in feed.ignore_patterns:
            print("  [!] Pattern already exists")
    elif action == "r":
        if not feed.ignore_patterns:
            print("  [!] No patterns to remove")
            return
        print(f"  Patterns: {feed.ignore_patterns}")
        p = input("Pattern to remove: ").strip()
        if p in feed.ignore_patterns:
            feed.ignore_patterns.remove(p)
            _save(path, cfg)
        else:
            print("  [!] Pattern not found")
    elif action == "c":
        if _confirm("Clear all ignore_patterns?"):
            feed.ignore_patterns.clear()
            _save(path, cfg)


def cmd_remove(path: Path, cfg: SteamRSSConfig) -> None:
    raw = input("app_id to remove: ").strip()
    if not raw.isdigit():
        print("  [!] app_id must be a number")
        return
    app_id = int(raw)
    feed = _feed_by_id(cfg, app_id)
    if not feed:
        print(f"  [!] app_id {app_id} not found")
        return
    if _confirm(f"Remove app_id {app_id}?"):
        cfg.feeds = [f for f in cfg.feeds if f.app_id != app_id]
        _save(path, cfg)


def cmd_global(path: Path, cfg: SteamRSSConfig) -> None:
    print(f"\nCurrent global ignore_patterns: {cfg.ignore_patterns}")
    action = input("Action: (a)dd pattern, (r)emove pattern, (c)lear all, (s)kip: ").strip().lower()

    if action == "a":
        p = input("Pattern to add: ").strip()
        if p and p not in cfg.ignore_patterns:
            cfg.ignore_patterns.append(p)
            _save(path, cfg)
        elif p in cfg.ignore_patterns:
            print("  [!] Pattern already exists")
    elif action == "r":
        if not cfg.ignore_patterns:
            print("  [!] No patterns to remove")
            return
        p = input("Pattern to remove: ").strip()
        if p in cfg.ignore_patterns:
            cfg.ignore_patterns.remove(p)
            _save(path, cfg)
        else:
            print("  [!] Pattern not found")
    elif action == "c":
        if _confirm("Clear all global ignore_patterns?"):
            cfg.ignore_patterns.clear()
            _save(path, cfg)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Manage steam-rss-config.json")
    p.add_argument("--config", default="steam-rss-config.json", help="Config file")
    args = p.parse_args()

    path = Path(args.config)
    if not path.exists():
        print(f"Config not found: {path}", file=sys.stderr)
        return 1

    cfg = _load(path)

    actions = {
        "1": ("List feeds", cmd_list),
        "2": ("Add feed", cmd_add),
        "3": ("Edit feed (patterns)", cmd_edit),
        "4": ("Remove feed", cmd_remove),
        "5": ("Edit global ignore_patterns", cmd_global),
    }

    while True:
        print("\n:: Steam Config Manager ::")
        for key, (label, _) in actions.items():
            print(f"  {key}. {label}")
        print("  0. Exit")

        choice = input("\nChoice: ").strip()
        if choice == "0":
            break
        if choice in actions:
            actions[choice][1](path, cfg)
        else:
            print("  [!] Invalid choice")

    return 0


if __name__ == "__main__":
    sys.exit(main())
