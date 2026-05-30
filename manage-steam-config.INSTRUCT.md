# manage-steam-config.py — управление конфигурацией Steam RSS Watcher

Утилита для редактирования `steam-rss-config.json` в интерактивном режиме.

## Запуск

```bash
uv run manage-steam-config.py
uv run manage-steam-config.py --config my-feeds.json
```

## Меню

| Клавиша | Действие | Описание |
|---------|----------|----------|
| `1` | List feeds | Показать все фиды и глобальные ignore_patterns |
| `2` | Add feed | Добавить новый app_id с опциональными паттернами |
| `3` | Edit feed | Добавить/удалить/очистить ignore_patterns у конкретного фида |
| `4` | Remove feed | Удалить фид по app_id |
| `5` | Edit global | Редактировать глобальные ignore_patterns |
| `0` | Exit | Выйти |

## Формат конфига (`steam-rss-config.json`)

```json
{
  "feeds": [
    {
      "app_id": 1868140,
      "ignore_patterns": []
    }
  ],
  "ignore_patterns": ["обновление", "update", "patch"]
}
```

- `feeds[].ignore_patterns` — локальные паттерны (применяются только к этой игре, добавляются к глобальным)
- `ignore_patterns` (корневой) — глобальные паттерны (применяются ко всем играм)

## Примеры

Добавить игру 123456 с паттернами `"hotfix", "patch"`:
```
2 → app_id: 123456 → patterns: hotfix, patch → сохраняется

```

Добавить паттерн `"Dev Diary"` к игре 255710:
```
3 → app_id: 255710 → a (add) → Dev Diary → сохраняется
```

Очистить все глобальные паттерны:
```
5 → c (clear) → y → сохраняется
```

## Примечания

- После каждого изменения файл перезаписывается.
- При добавлении дубликата app_id выдаётся ошибка.
- Паттерны — сырой regex, регистр не имеет значения.
