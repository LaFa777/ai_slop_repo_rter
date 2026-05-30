# INSTRUCT — Общий справочник по разработке скрейперов

У каждого скрипта-скрейпера в этом проекте есть свой `*.INSTRUCT.md` —
там описание его архитектуры, запуска и специфических нюансов.

**Этот** INSTRUCT.md — общая шпаргалка по проблемам, которые могут
встретиться в любом скрейпере: хитрые редиректы, грязный HTML, Windows-баги,
инструменты и т.д. Если натыкаешься на грабли — добавляй сюда, чтобы
следующий агент не наступал.

---

## ОГЛАВЛЕНИЕ

1. [Снятие показаний: как изучать сайт](#1-снятие-показаний-как-изучать-сайт)
2. [Если URL не открывается через requests](#2-если-url-не-открывается-через-requests)
3. [Сайты на DataLife Engine (DLE)](#3-сайты-на-datalife-engine-dle)
4. [Windows-specific проблемы с crawl4ai](#4-windows-specific-проблемы-с-crawl4ai)
5. [Чистка и нормализация HTML](#5-чистка-и-нормализация-html)
6. [Правила для всех скрейперов](#6-правила-для-всех-скрейперов)
7. [Steam RSS Watcher (watch_steam_news.py)](#7-steam-rss-watcher-watch_steam_news)

---

## 1. Снятие показаний: как изучать сайт

Прежде чем писать код — изучи сайт в браузере с DevTools:

### 1.1. Заголовки ответа
Вкладка Network → клик на первый запрос → Headers:
- `Content-Type`: `text/html; charset=windows-1251`? Значит, нужна перекодировка.
- `Set-Cookie`: какие куки раздаёт? Может быть антибот.
- `Location` (код 3xx): редиректит ли?

### 1.2. HTML-структура
Вкладка Elements:
- В каком селекторе лежат нужные данные?
- Есть ли комментарий `<!-- DataLife Engine Copyright... -->`? Это DLE — см. раздел 3.
- Есть ли `target="_blank"` на ссылках? Помешает клику в crawl4ai.
- Есть ли `<div itemprop="articleBody">`? Удобный контейнер.

### 1.3. Динамика
- Появляется ли контент через JS? Нужен браузер (crawl4ai) или хватит `requests`?
- Есть ли `data-src` вместо `src`? Это lazy loading — нужно доставать из `data-src`.
- Есть ли `<!--dle_media_begin-->` / `<!--dle_media_end-->`? DLE-вставки.

### 1.4. Кодировка
Если в ответе `charset=windows-1251`, а `requests` не перекодирует:
```python
resp.encoding = "cp1251"  # или "windows-1251"
text = resp.text
```

---

## 2. Если URL не открывается через requests

Таблица симптомов → решений:

| Симптом | Вероятная причина | Что делать |
|---------|-------------------|------------|
| 403 Forbidden | Антибот, User-Agent | Добавить `headers={"User-Agent": "Mozilla/5.0 ..."}` |
| 302 → index.php | Нет кук/сессии | Загрузить главную страницу сначала, переиспользовать сессию |
| 302 → index.php даже с сессией | Сайт проверяет Referer или клик | Использовать crawl4ai с кликом по ссылке (см. раздел 3) |
| 404 всё время | URL обфусцирован или требует JS | Открыть в браузере, проверить Network |
| Пустой ответ или мусор | Неправильная кодировка | `resp.encoding = "cp1251"` (или `"utf-8"`) |
| Таймаут | Сайт падает / блокирует | Увеличить `timeout`, добавить `headers` |

### 2.1. Редиректы через requests

```python
# Просто разрешить редиректы
resp = requests.get(url, allow_redirects=True, timeout=15)
print(f"Начальный URL: {url}")
print(f"Конечный URL: {resp.url}")
print(f"Цепочка: {[r.url for r in resp.history]}")
```

**Не работает если:** редирект происходит через JavaScript на стороне
клиента (самый частый случай для DLE и антибот-систем). Тогда — только
браузер (см. раздел 3).

### 2.2. Редиректы через браузер (crawl4ai)

```python
result = await crawler.arun(url)
print(f"redirected_url: {result.redirected_url}")
```

`redirected_url` — URL после всех HTTP-редиректов. Если редирект был
через JS — `redirected_url` может всё ещё указывать на исходную страницу.
В таком случае нужно выполнить JS-клик по ссылке и прочитать URL
из HTML целевой страницы (см. раздел 3.4).

---

## 3. Сайты на DataLife Engine (DLE)

Опознаётся по комментарию в HTML:
```html
<!-- DataLife Engine Copyright SoftNews Media Group (http://dle-news.ru) -->
```

### 3.1. Особенности DLE

- Кодировка `windows-1251` (обязательно `resp.encoding = "cp1251"`)
- Внешние ссылки обфусцированы через `/ext/TOKEN` — **не прямые URL**
- `/ext/` возвращает 302 → index.php без браузерной сессии
- На странице `/ext/` — **интерстициал** с таймером JS, после которого
  появляется `location.href='REAL_URL'`. Реальный URL внутри HTML.

### 3.2. Почему requests не хватает для /ext/

| Подход | Результат | Причина |
|--------|-----------|---------|
| `requests.get(/ext/XXX)` | 302 → index.php | Сервер проверяет Referer и куки |
| `requests.get()` c Referer | 302 → index.php | Всё равно мало |
| `crawl4ai.arun()` на /ext/ отдельно | 302 → index.php | Нет сессии с главной страницы |
| `crawl4ai.arun()` на /ext/ после главной | 302 → index.php | Куки есть, но клик не симулирован |

### 3.3. Финальное решение для /ext/

```
1. Загрузить главную страницу через crawl4ai (получаем сессию)
2. Загрузить страницу, где лежит /ext/ ссылка (та же сессия)
3. Выполнить JS:
     link.removeAttribute('target');   ← убрать target="_blank"
     link.click();                      ← эмулировать клик
4. Браузер переходит на /ext/ (интерстициал)
5. Подождать ~3 сек (JS таймер)
6. Прочитать HTML без перезагрузки: js_only=True, js_code="void(0)"
7. Извлечь URL из скрипта / разметки через regex
```

**Почему `removeAttribute('target')`:** все `/ext/` ссылки в DLE имеют
`target="_blank"`. Без удаления браузер открывает новую вкладку, и сессия
теряется.

**Почему `js_only=True`:** повторная загрузка страницы сотрёт интерстициал.
`js_only` просто читает текущее состояние страницы без навигации.

### 3.4. Шаблон функции для раскрытия ссылки

```python
async def resolve_obfuscated_url(crawler: AsyncWebCrawler, page_url: str) -> str:
    """
    Кликает по обфусцированной ссылке на странице, читает реальный URL
    из интерстициала DLE.
    """
    config = CrawlerRunConfig(
        session_id=SESSION_ID,
        cache_mode=CacheMode.BYPASS,
        page_timeout=20000,
        js_code=r"""
            (() => {
                // <- селектор может отличаться для разных сайтов
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
        await crawler.arun(url=page_url, config=config)
        await asyncio.sleep(3)

        r = await crawler.arun(url=page_url, config=CrawlerRunConfig(
            session_id=SESSION_ID, js_only=True, js_code="void(0)",
            cache_mode=CacheMode.BYPASS,
        ))
        html = r.html or ""

        # <- регулярка под нужный магазин/сайт
        m = re.search(r"https://store\.steampowered\.com/app/\d+[a-zA-Z0-9_/?&=.-]*", html)
        if m:
            return m.group(0)
        return "not-found"
    except Exception:
        return "not-found"
```

**Что менять для другого сайта:**
- Селектор ссылки (`a[href*="/ext/"]`) — если сайт использует другой паттерн
- Регулярку для извлечения URL (`re.search(...)`)
- Значение по умолчанию (строка-маркер, например `"no-steam"`)

### 3.5. `_crawler_lock` для параллельных кликов

```python
_crawler_lock = asyncio.Lock()

async with _crawler_lock:
    url = await resolve_obfuscated_url(crawler, page_url)
```

Если enrich-задачи бегут параллельно (`asyncio.gather`) и используют
один `SESSION_ID` — без лока две задачи попытаются кликнуть одновременно,
и сессия сломается.

---

## 4. Windows-specific проблемы с crawl4ai

### 4.1. Rich console краш

**Симптом:**
```
UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'
```

**Причина:** crawl4ai использует библиотеку `rich`. На Windows консоль
имеет кодировку cp1251, а `rich` пытается вывести unicode-символы.

**Решение — в коде (ДО импорта crawl4ai):**
```python
import os
os.environ["CRAWL4AI_LOG_LEVEL"] = "ERROR"

import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```

Если для отладки нужны логи — временно закомментировать `os.environ[...]`
и запускать с `2>$null` на PowerShell.

### 4.2. Playwright не находит Chromium

**Симптом:**
```
Executable doesn't exist at ...\ms-playwright\chromium-...
```

**Решение:**
```powershell
uv run --with playwright --with crawl4ai python -m playwright install chromium
```

Без `--with playwright` модуль playwright не будет найден — он транзитивная
зависимость crawl4ai, а uv run видит только прямые из PEP 723 скрипта.

### 4.3. Перенаправление stdout/stderr

Обязательно в начале скрипта:
```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
```

Иначе `print()` упадёт при попытке вывести не-cp1251 символ.

---

## 5. Чистка и нормализация HTML

Типовые проблемы при парсинге:

### 5.1. `&nbsp;` как пустой контент

```python
def is_empty(text: str) -> bool:
    """True, если текст состоит только из пробелов и &nbsp;."""
    return not bool(re.search(r"[a-zA-Zа-яА-ЯёЁ0-9]", text))
```

### 5.2. Lazy loading (`data-src` вместо `src`)

```python
img_url = img_tag.get("data-src") or img_tag.get("src")
```

### 5.3. Относительные URL

```python
def abs_url(url: str, base: str) -> str:
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url
```

### 5.4. Разная капитализация / падежи в тексте

Ищи не точное совпадение, а частичное:
```python
label = soup.find("b", string=lambda t: t and "информаци" in t.lower())
# покроет: "Информация об игре", "Информация о игре", "информация" и т.д.
```

---

## 6. Правила для всех скрейперов

1. **Каждый скрейпер — отдельный PEP 723 скрипт.** Все зависимости
   внутри `/// script`. Запуск: `uv run script.py`.

2. **У каждого скрейпера свой `*.INSTRUCT.md`.** Там — архитектура,
   запуск, специфические нюансы. Этот файл — только общие вещи.

3. **Первый запуск — baseline (если есть дедупликация).** Ничего не
   выводит, только запоминает состояние.

4. **stdout — только JSON/данные.** Всё отладочное — в `stderr`:
   ```python
   print("дебаг", file=sys.stderr)
   ```

5. **Один `AsyncWebCrawler` на весь скрипт** — создаётся в `main()`,
   передаётся во все фазы. Один `SESSION_ID`.

6. **Если Windows — кодировка.** Добавляй:
   ```python
   if sys.platform == "win32":
       sys.stdout.reconfigure(encoding="utf-8", errors="replace")
       sys.stderr.reconfigure(encoding="utf-8", errors="replace")
   ```

7. **Перед коммитом:**
   - Удалить `debug_*.py`
   - Проверить что нет `print()` отладки
   - Проверить что `CRAWL4AI_LOG_LEVEL` на месте

---

## 7. Steam RSS Watcher (`watch_steam_news.py`)

Следит за RSS-лентами новостей Steam игр. Не требует crawl4ai — использует
только `urllib.request` и `xml.etree.ElementTree` (stdlib).

### 7.1. Конфиг (`steam-rss-config.json`)

```json
{
  "feeds": [
    {
      "app_id": 1868140,
      "ignore_patterns": []
    }
  ],
  "ignore_patterns": ["обновление", "update", "patch", "hotfix"]
}
```

Поля:
- `feeds[].app_id` — числовой ID игры в Steam
- `feeds[].ignore_patterns` — локальные паттерны для конкретной игры (добавляются к глобальным)
- `ignore_patterns` — глобальные паттерны (применяются ко всем играм)

### 7.2. Алгоритм работы

1. Загружает конфиг в датаклассы `FeedConfig` / `SteamRSSConfig`
2. Для каждого `app_id` вычисляет RSS URL: `https://store.steampowered.com/feeds/news/app/{id}/?cc=US&l=english`
3. GET RSS → парсит XML (ElementTree) → для каждого `<item>` собирает **все поля** в плоский dict
4. Извлекает store URL из `<atom:link rel="self">` (убирает `/news` из пути)
5. GET store URL → регулярка `<title>(.+?) on Steam</title>` → имя игры
6. Название игры кешируется в watermark (`app_titles` в `states/steam-news.json`), повторный GET не нужен
7. Фильтр игнорирования: `global_ignore_patterns + feed.ignore_patterns` проверяются по `title + description`
8. Дедупликация через `Watermark.load("steam-news")` по `guid` (извлекается числовой ID из `/view/NNNNN`)
9. Вывод JSON с именем игры как ключом:
   ```json
   {
     "DAVE THE DIVER": [
       {
         "title": "...",
         "description": "...",
         "link": "https://store.steampowered.com/news/app/1868140/view/...",
         "pubDate": "Fri, 22 May 2026 08:56:12 +0000",
         "guid": "663862479542026653",
         "image": "https://clan.fastly.steamstatic.com/..."
       }
     ]
   }
   ```

### 7.3. Первый запуск

Как и все вотчеры — первый запуск записывает все GUID в watermark, но
ничего не выводит. Повторные запуски выводят только новые новости.

### 7.4. Извлечение картинок

Приоритет: `<enclosure>` → `<media:content medium="image">` → `<media:thumbnail>`.
В output добавляется поле `image`.

### 7.5. Игнор-паттерны

Паттерны — regex (регистронезависимые). Проверяются по склеенной строке
`title + " " + description`. Если совпало — новость удаляется из вывода.

---

*Последнее обновление: 31.05.2026*
