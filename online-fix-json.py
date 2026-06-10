#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "beautifulsoup4",
#   "crawl4ai",
#   "requests",
# ]
# ///
"""  
ПРЕЖДЕ ЧЕМ ПЫТАТЬСЯ ЧТО-ТО МЕНЯТЬ — ПРОЧТИ online-fix-json.INSTRUCT.md !
(а потом INSTRUCT.md — про DLE в целом)
online-fix-json.py — Scraper для https://online-fix.me/ в формате JSON

Фазы:
  Фаза 1: Сбор RSS-лент через requests.get
  Фаза 2: Парсинг RSS XML через ElementTree + BeautifulSoup для извлечения item'ов
  Фаза 3: Фильтрация по кооперативу — оставляем только игры с "fa-check"
  Фаза 4: Категоризация по источнику: export_rookovodstva.xml → "Новые релизы",
          export_updrookovodstva.xml → "Обновления"
  Фаза 5: Сериализация в JSON (плоский формат)
  Фаза 6: Обогащение — для каждой новой статьи загружается полная страница,
           извлекаются game_info и store_url. Steam-ссылка раскрывается через
           Crawl4AI: клик по /ext/, переход на интерстициал, парсинг URL из HTML

Запуск:
  cd repo_rter && uv run online-fix-json.py

Отладка (повторный запуск):
  Watermark запоминает обработанные статьи и не выводит их повторно.
  Чтобы сбросить и прогнать всё заново:

    # PowerShell:
    @'
    {
      "first_run": false,
      "seen_ids": []
    }
    '@ | Set-Content states/online-fix.json -Encoding utf8

    # или просто удалить файл:
    Remove-Item states/online-fix.json
"""

import asyncio
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

os.environ["CRAWL4AI_LOG_LEVEL"] = "ERROR"

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from utils.watermark import Watermark

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

NEW_RELEASES_FEED = "https://online-fix.me/export_rookovodstva.xml"
UPDATES_FEED = "https://online-fix.me/export_updrookovodstva.xml"
BASE_URL = "https://online-fix.me/"
SESSION_ID = "ofix"


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 1: Загрузка RSS-ленты через requests
# ──────────────────────────────────────────────────────────────────────────────
async def fetch_rss(url: str) -> bytes | None:
    try:
        resp = await asyncio.to_thread(requests.get, url, timeout=15)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        print(f"Ошибка загрузки RSS {url}: {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 2: Парсинг RSS-ленты — извлечение статей
# ──────────────────────────────────────────────────────────────────────────────
def parse_rss_feed(xml_data: bytes, feed_type: str) -> list[dict]:
    """
    Парсит RSS-ленту и извлекает <item> элементы.
    feed_type: "new_release" — из export_rookovodstva.xml
               "update"     — из export_updrookovodstva.xml

    Для каждого item собирает:
      - url          (ссылка из <link>)
      - title        (текст <title>)
      - has_coop     (True, если Кооператив помечен fa-check)
      - has_coop_times
      - feed_type    (new_release / update)
    """
    root = ET.fromstring(xml_data)
    channel = root.find("channel")
    if channel is None:
        return []

    articles: list[dict] = []

    for item in channel.findall("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")

        if title_el is None or link_el is None:
            continue

        url = (link_el.text or "").strip()
        if not url:
            continue

        raw_title = (title_el.text or "").strip()

        # ---- Кооператив fa-check / fa-times из description CDATA ----
        has_coop = False
        has_coop_times = False

        if desc_el is not None and desc_el.text:
            desc_html = desc_el.text.strip()
            desc_soup = BeautifulSoup(desc_html, "html.parser")
            for b_tag in desc_soup.find_all("b"):
                if "режим" in b_tag.get_text().lower():
                    span = b_tag.find_next("span", class_=re.compile(r"fa-"))
                    if span:
                        classes = span.get("class", [])
                        if "fa-check" in classes:
                            has_coop = True
                        elif "fa-times" in classes:
                            has_coop_times = True
                    break

        articles.append({
            "url": url,
            "title": raw_title,
            "has_coop": has_coop,
            "has_coop_times": has_coop_times,
            "feed_type": feed_type,
        })

    return articles


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 3+4: Фильтрация по кооперативу + категоризация
# ──────────────────────────────────────────────────────────────────────────────
def filter_coop_only(articles: list[dict]) -> list[dict]:
    """
    Фильтр по кооперативу.
      has_coop=True (fa-check) → оставляем
      has_coop=False или has_coop_times=True → удаляем
    """
    return [a for a in articles if a["has_coop"] and not a["has_coop_times"]]


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 5: Сериализация в JSON
# ──────────────────────────────────────────────────────────────────────────────
def strip_po_seti(title: str) -> str:
    """Удаляет 'по сети' и 'Online' из заголовка."""
    title = re.sub(r"\s+по сети\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+Online\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+онлайн\s*$", "", title, flags=re.IGNORECASE)
    return title.strip()


def _abs_url(url: str) -> str:
    """Превращает относительный URL в абсолютный."""
    if url.startswith("/"):
        return BASE_URL.rstrip("/") + url
    return url


def build_json_output(new_releases: list[dict], updates: list[dict]) -> str:
    now = datetime.now(timezone.utc).astimezone().isoformat()

    def article_dict(a: dict) -> dict:
        return {
            "title": strip_po_seti(a["title"]),
            "url": _abs_url(a["url"]),
            "has_coop": a["has_coop"],
            "has_coop_times": a["has_coop_times"],
            "game_info": a.get("game_info", ""),
            "store_url": a.get("store_url", "no-steam"),
        }

    output = {
        "source": [NEW_RELEASES_FEED, UPDATES_FEED],
        "scraped_at": now,
        "new_releases": [article_dict(a) for a in new_releases],
        "updates": [article_dict(a) for a in updates],
    }

    return json.dumps(output, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 6: Обогащение — загрузка полной страницы, game_info + store_url
# ──────────────────────────────────────────────────────────────────────────────
_semaphore = asyncio.Semaphore(5)
_crawler_lock = asyncio.Lock()


def _fetch_page_sync(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        resp.encoding = "cp1251"
        return resp.text
    except requests.RequestException:
        return None


async def resolve_store_url(crawler: AsyncWebCrawler, article_url: str) -> str:
    click_cfg = CrawlerRunConfig(
        session_id=SESSION_ID,
        cache_mode=CacheMode.BYPASS,
        page_timeout=20000,
        js_code=r"""
            (() => {
                const links = document.querySelectorAll('a[href*="/ext/"]');
                for (const link of links) {
                    link.removeAttribute('target');
                    link.click();
                    return;
                }
            })();
        """,
    )
    try:
        await crawler.arun(url=article_url, config=click_cfg)
        await asyncio.sleep(3)

        r = await crawler.arun(url=article_url, config=CrawlerRunConfig(
            session_id=SESSION_ID, js_only=True, js_code="void(0)",
            cache_mode=CacheMode.BYPASS,
        ))
        html = r.html or ""
        m = re.search(r"https://store\.steampowered\.com/app/\d+[a-zA-Z0-9_/?&=.-]*", html)
        if m:
            return m.group(0)
        return "no-steam"
    except Exception:
        return "no-steam"


async def enrich_article(article: dict, crawler: AsyncWebCrawler) -> dict:
    full_url = _abs_url(article["url"])

    async with _semaphore:
        html = await asyncio.to_thread(_fetch_page_sync, full_url)

    if not html:
        return {**article, "game_info": "", "store_url": "no-steam"}

    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("div", itemprop="articleBody")
    if not body:
        return {**article, "game_info": "", "store_url": "no-steam"}

    # ---- game_info ----
    game_info = ""
    info_label = body.find("b", string=lambda t: t and "информаци" in t.lower())
    if info_label:
        italic = info_label.find_next("i")
        if italic:
            game_info = italic.get_text(strip=True)

    # ---- store_url ----
    store_url = "no-steam"
    store_label = body.find("b", string=lambda t: t and "steam" in t.lower())
    if store_label:
        link = store_label.find_next("a")
        if link and link.get("href"):
            ext_url = _abs_url(link["href"])
            if "/ext/" in ext_url:
                async with _crawler_lock:
                    store_url = await resolve_store_url(crawler, full_url)
            else:
                store_url = ext_url

    return {**article, "game_info": game_info, "store_url": store_url}


async def enrich_articles(articles: list[dict], crawler: AsyncWebCrawler) -> list[dict]:
    results = await asyncio.gather(*[enrich_article(a, crawler) for a in articles])
    return list(results)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
async def main():
    xml1 = await fetch_rss(NEW_RELEASES_FEED)
    xml2 = await fetch_rss(UPDATES_FEED)
    if not xml1 and not xml2:
        print('{"error": "Не удалось загрузить ни одну RSS-ленту."}', file=sys.stderr)
        sys.exit(1)

    new_releases = parse_rss_feed(xml1, "new_release") if xml1 else []
    updates = parse_rss_feed(xml2, "update") if xml2 else []

    new_releases = filter_coop_only(new_releases)
    updates = filter_coop_only(updates)

    if not new_releases and not updates:
        print('{"error": "Не найдено ни одной статьи с кооперативом."}', file=sys.stderr)
        sys.exit(1)

    wm = Watermark.load("online-fix")
    all_articles = new_releases + updates
    for a in all_articles:
        a["composite_id"] = f"{a['url']}|||{a['feed_type']}"
    new_articles = wm.filter_new(all_articles, id_key="composite_id")

    if new_articles:
        browser_config = BrowserConfig(
            headless=True,
            viewport_width=1920,
            viewport_height=1080,
        )
        async with AsyncWebCrawler(config=browser_config) as crawler:
            new_articles = await enrich_articles(new_articles, crawler)
            new_release_urls = {a["url"] for a in new_releases}
            new_new = [a for a in new_articles if a["url"] in new_release_urls]
            new_upd = [a for a in new_articles if a["url"] not in new_release_urls]
            output = build_json_output(new_new, new_upd)
            print(output)

    wm.save()


if __name__ == "__main__":
    asyncio.run(main())
