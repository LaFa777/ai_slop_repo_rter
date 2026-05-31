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
  Фаза 1: Сбор HTML главной страницы через Crawl4AI (AsyncWebCrawler)
  Фаза 2: Парсинг HTML через BeautifulSoup — извлечение всех статей
  Фаза 3: Фильтрация по кооперативу — оставляем только игры с "fa-check"
  Фаза 4: Категоризация — "Новые релизы" vs "Обновления" по содержимому <div class="edit">
  Фаза 5: Сериализация в JSON (плоский формат)
  Фаза 6: Обогащение — для каждой новой статьи загружается полная страница,
           извлекаются game_info и store_url. Steam-ссылка раскрывается через
           Crawl4AI: клик по /ext/, переход на интерстициал, парсинг URL из HTML

Использование:
  cd repo_rter && uv run online-fix-json.py
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup, Tag

os.environ["CRAWL4AI_LOG_LEVEL"] = "ERROR"

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from utils.watermark import Watermark

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://online-fix.me/"
ADDITIONAL_URL = "https://online-fix.me/guides_upd.html"
SESSION_ID = "ofix"


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 1: Загрузка HTML через Crawl4AI
# ──────────────────────────────────────────────────────────────────────────────
async def fetch_html(crawler: AsyncWebCrawler, url: str = BASE_URL) -> str | None:
    config = CrawlerRunConfig(
        session_id=SESSION_ID,
        cache_mode=CacheMode.BYPASS,
        remove_overlay_elements=True,
        page_timeout=60000,
    )

    result = await crawler.arun(url=url, config=config)

    if not result.success:
        print(f"Ошибка загрузки: {result.error_message}", file=sys.stderr)
        return None

    return result.html


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 2: Парсинг HTML — извлечение статей
# ──────────────────────────────────────────────────────────────────────────────
def extract_articles(html: str) -> list[dict]:
    """
    Извлекает из HTML все <article class="news">.
    Для каждой статьи собирает:
      - url          (ссылка из заголовка)
      - title        (текст h2.title)
      - edit_text    (содержимое div.edit)
      - edit_is_nbsp (True, если edit содержит только пробелы/&nbsp;)
      - has_coop     (True, если Кооператив помечен fa-check)
    """
    soup = BeautifulSoup(html, "html.parser")
    articles: list[dict] = []

    for article_tag in soup.find_all(["article", "div"], class_="news"):
        # ---- заголовок + ссылка ----
        title_tag = article_tag.find(["h2", "span"], class_="title")
        if not title_tag:
            continue

        # Ссылка — родительский <a> вокруг h2
        link_tag = title_tag.find_parent("a")
        url = str(link_tag["href"]) if link_tag and link_tag.get("href") else ""
        if not url:
            continue

        raw_title = title_tag.get_text(strip=True)

        # ---- div.edit ----
        edit_tag = article_tag.find("div", class_="edit")
        edit_text = edit_tag.get_text(strip=True) if edit_tag else ""
        edit_is_nbsp = _is_nbsp_only(edit_tag)

        # ---- Кооператив fa-check / fa-times ----
        has_coop = False
        has_coop_times = False

        preview = article_tag.find("div", class_="preview-text")
        if preview:
            # Ищем текст "Кооператив" и берём следующий за ним span
            for text_node in preview.find_all(string=True):
                parent = text_node.parent
                if parent and "Кооператив" in parent.get_text():
                    # Ищем span.fa-check или span.fa-times внутри этого родителя
                    coop_span = parent.find_next("span", class_=re.compile(r"fa-"))
                    if coop_span:
                        classes = coop_span.get("class", [])
                        if "fa-check" in classes:
                            has_coop = True
                        elif "fa-times" in classes:
                            has_coop_times = True
                    break

        articles.append(
            {
                "url": url,
                "title": raw_title,
                "edit_text": edit_text,
                "edit_is_nbsp": edit_is_nbsp,
                "has_coop": has_coop,
                "has_coop_times": has_coop_times,
            }
        )

    return articles


def _is_nbsp_only(edit_tag: Tag | None) -> bool:
    """
    Проверяет, что div.edit содержит ТОЛЬКО &nbsp; или пробелы.
    Если есть хоть один настоящий символ (кириллица, латиница, цифра)
    — значит это обновление.
    """
    if edit_tag is None:
        return True

    text = edit_tag.get_text(strip=True)
    # После strip() &nbsp; превращается в пустую строку
    # Если есть хоть один буквенный или цифровой символ — это обновление
    return not bool(re.search(r"[a-zA-Zа-яА-ЯёЁ0-9]", text))


# ──────────────────────────────────────────────────────────────────────────────
# Фаза 3 + 4: Фильтрация по кооперативу и категоризация
# ──────────────────────────────────────────────────────────────────────────────
def categorize_articles(
    articles: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Фаза 3 — фильтр по кооперативу.
      has_coop=True (fa-check) → оставляем
      has_coop=False или has_coop_times=True → удаляем

    Фаза 4 — категоризация.
      edit_is_nbsp=True  → "Новые релизы"
      edit_is_nbsp=False → "Обновления"
    """
    # Фаза 3: только с кооперативом fa-check
    coop_only = [a for a in articles if a["has_coop"] and not a["has_coop_times"]]

    # Фаза 4: разделение
    new_releases = [a for a in coop_only if a["edit_is_nbsp"]]
    updates = [a for a in coop_only if not a["edit_is_nbsp"]]

    return new_releases, updates


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
            "edit_text": a["edit_text"],
            "edit_is_nbsp": a["edit_is_nbsp"],
            "has_coop": a["has_coop"],
            "has_coop_times": a["has_coop_times"],
            "game_info": a.get("game_info", ""),
            "store_url": a.get("store_url", "no-steam"),
        }

    output = {
        "source": [BASE_URL, ADDITIONAL_URL],
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
    browser_config = BrowserConfig(
        headless=True,
        viewport_width=1920,
        viewport_height=1080,
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        html1 = await fetch_html(crawler, BASE_URL)
        html2 = await fetch_html(crawler, ADDITIONAL_URL)
        if not html1 and not html2:
            print('{"error": "Не удалось загрузить ни одну страницу."}', file=sys.stderr)
            sys.exit(1)

        articles = []
        if html1:
            articles.extend(extract_articles(html1))
        if html2:
            articles.extend(extract_articles(html2))
        if not articles:
            print('{"error": "Не найдено ни одной статьи."}', file=sys.stderr)
            sys.exit(1)

        new_releases, updates = categorize_articles(articles)

        wm = Watermark.load("online-fix")
        all_articles = new_releases + updates
        new_articles = wm.filter_new(all_articles, id_key="url")

        if new_articles:
            new_articles = await enrich_articles(new_articles, crawler)
            new_release_urls = {a["url"] for a in new_releases}
            new_new = [a for a in new_articles if a["url"] in new_release_urls]
            new_upd = [a for a in new_articles if a["url"] not in new_release_urls]
            output = build_json_output(new_new, new_upd)
            print(output)

        wm.save()


if __name__ == "__main__":
    asyncio.run(main())
