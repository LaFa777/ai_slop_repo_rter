# online-fix-json.INSTRUCT.md

> **Прежде чем что-то менять — прочти сначала этот файл,
> потом INSTRUCT.md (общее про DLE).**

---

## ОГЛАВЛЕНИЕ

1. [Архитектура скрипта](#1-архитектура-скрипта)
2. [Запуск](#2-запуск)
3. [Watermark-система (дедупликация)](#3-watermark-система-дедупликация)
4. [Фаза 6 — Обогащение (самое сложное)](#4-фаза-6--обогащение-самое-сложное)
5. [Если что-то сломалось](#5-если-что-то-сломалось)
6. [Чеклист перед коммитом](#6-чеклист-перед-коммитом)

---

## 1. Архитектура скрипта

Скрипт — самодостаточный PEP 723 script. Все зависимости в `/// script`.

```
Фаза 1: fetch_html(crawler)       — Crawl4AI грузит главную online-fix.me
Фаза 2: extract_articles(html)    — BS4 парсит <article class="news">
Фаза 3: categorize_articles()     — фильтр: только fa-check (кооператив)
Фаза 4: (там же)                  — new_releases (edit пустой) vs updates
Фаза 5: build_json_output()       — JSON
Фаза 6: enrich_articles()         — для каждой новой статьи:
                                      a) requests → страница статьи
                                      b) game_info из <i> после <b>с "информаци"</b>
                                      c) store_url: клик в браузере → /ext/ → парсинг Steam URL
```

`AsyncWebCrawler` живёт всё время работы скрипта — создаётся в `main()`,
передаётся во все фазы. `SESSION_ID = "ofix"` — один на всё.

---

## 2. Запуск

```bash
cd repo_rter
uv run online-fix-json.py
```

Скрипт должен запускаться **из корня репозитория**: ему нужны папки
`utils/` (импорт Watermark) и `states/` (watermark-файлы).

### Первый запуск

Ничего не выводит. Это нормально — запоминает все URL в
`states/online-fix.json` (baseline).

### Повторные запуски

Выводят JSON только с новыми статьями. Если новых нет — stdout пуст.

### Принудительно увидеть вывод

```powershell
echo '{"first_run": false, "seen_ids": ["https://online-fix.me/games/vr/18123-zero-caliber-2-remastered-po-seti.html"]}' > states/online-fix.json
uv run online-fix-json.py
```

В `seen_ids` — URL статьи, которая уже выводилась. Все остальные будут
"новыми" и попадут в вывод.

---

## 3. Watermark-система (дедупликация)

Файл: `utils/watermark.py`. Состояние: `states/online-fix.json`.

```json
{
  "first_run": false,
  "seen_ids": [
    "https://online-fix.me/games/..."
  ]
}
```

Жизненный цикл в этом скрипте:

1. `Watermark.load("online-fix")` — читает файл
2. `wm.filter_new(all_articles, id_key="url")` — возвращает только новые URL.
   **На первом запуске** возвращает пустой список (baseline).
3. `wm.save()` — дописывает все текущие URL в `seen_ids`, обрезает до 500

`_state_dir()` в `watermark.py` возвращает `Path("states")` — папку рядом
со скриптом. Не перепутать с другими watcher-скриптами (watch_rss.py и др.)
— те импортируют `_watermark`, а не `utils.watermark`.

---

## 4. Фаза 6 — Обогащение (самое сложное)

### game_info

```python
info_label = body.find("b", string=lambda t: t and "информаци" in t.lower())
```

Ищет `<b>` с частичным совпадением "информаци" (покрывает и
"Информация об игре", и "Информация о игре"). Берёт следующий `<i>`.

### store_url

Для Steam: `<b>` с "steam" → `<a href*="/ext/"` → клик → парсинг
интерстициала.

Регулярка для Steam URL:
```python
r"https://store\.steampowered\.com/app/\d+[a-zA-Z0-9_/?&=.-]*"
```

Покрывает форматы:
- `https://store.steampowered.com/app/1805320/Romestead/`
- `https://store.steampowered.com/app/4117320/Drive_Together/?curator_clanid=4777282`
- `https://store.steampowered.com/app/264710/Subnautica/?curator_clanid=4777282`

`"no-steam"` возвращается если:
- `<b>` с "steam" не найден (Microsoft Store, LAN, и т.д.)
- Не удалось раскрыть /ext/ (ошибка, таймаут, изменилась структура)
- Произошло исключение

**Зачем `_crawler_lock`:** все enrich-задачи бегут параллельно через
`asyncio.gather`, но resolve_store_url использует один `SESSION_ID`.
Без лока две задачи попытаются кликнуть одновременно — сессия сломается.
Подробнее: INSTRUCT.md → раздел 5.

---

## 5. Если что-то сломалось

### store_url = "no-steam" для Steam-игры

1. **Проверь, изменился ли HTML.** Открой страницу статьи в браузере,
   DevTools → Elements:
   - Есть ли `<b>` с текстом, содержащим "steam" (регистр любой)?
   - Есть ли `<a href*="/ext/"` рядом?
   - В `/ext/...` странице — есть ли `<title>Переход на store.steampowered.com</title>`?

2. **Проверь регулярку.** Steam URL мог сменить формат. Сейчас:
   `https://store.steampowered.com/app/DIGITS/Name/`
   Если нет — обнови паттерн.

3. **Проверь, дошёл ли до интерстициала.** В `resolve_store_url` добавь
   временно `print(f"[debug] downpage={'downpage' in html}", file=sys.stderr)`.
   Если `downpage = False` — значит, клик не сработал (возможно, DLE
   поменял селектор ссылки).

4. **Проверь сессию.** Не сломался ли `SESSION_ID`? Может, кто-то
   переименовал его или убрал.

### game_info пустой

1. Фраза могла измениться на "Описание", "Информация о проекте" и т.д.
2. В `enrich_article` ищи:
   ```python
   info_label = body.find("b", string=lambda t: t and "информаци" in t.lower())
   ```
   Может, нужно расширить до "информац" или вообще сменить логику.

### Скрипт падает при запуске

1. **Не установлен Chromium:**
   ```powershell
   uv run --with playwright --with crawl4ai python -m playwright install chromium
   ```
2. **`CRAWL4AI_LOG_LEVEL` после импорта crawl4ai.** Переменная должна
   быть установлена ДО `from crawl4ai import ...`.
3. **Нет `states/` или `utils/__init__.py`.** Создать вручную:
   ```powershell
   New-Item -ItemType Directory -Force states
   New-Item -ItemType File -Force utils/__init__.py
   ```

### Watermark не работает

1. Проверь `states/online-fix.json` — не повреждён ли JSON.
2. Проверь `_state_dir()` в `watermark.py` — должна быть `Path("states")`.
3. Убедись, что скрипт запускается из корня `repo_rter`.

### `UnicodeEncodeError` на Windows

См. INSTRUCT.md → раздел 6 (Windows-specific проблемы).

---

## 6. Чеклист перед коммитом

- [ ] Удалены `debug_*.py` файлы
- [ ] `states/online-fix.json` в `.gitignore` (уже есть строка `states/`)
- [ ] Не осталось `print()` отладки (кроме штатных `print(output)` и `print(..., file=sys.stderr)`)
- [ ] `os.environ["CRAWL4AI_LOG_LEVEL"] = "ERROR"` на месте и ДО импорта crawl4ai
- [ ] Запуск `uv run online-fix-json.py` без ошибок
- [ ] При изменении `watermark.py` — проверить, что не сломал другие
      скрипты (watch_rss.py, watch_github.py, watch_http_json.py — они
      импортят `_watermark`, а не `utils.watermark`)

---

*Последнее обновление: 31.05.2026*
