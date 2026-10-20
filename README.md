# combined_bot/ — 5 ботов в одном Render-сервисе (полностью бесплатно)

## Что это

Один Docker-контейнер, который запускает **5 Telegram-ботов** как независимые subprocesses + даёт один общий `/health` эндпоинт для Render и cron-job.org.

**Результат:** 1 Render free-сервис × 24 ч × 30 дн = **720 часов/мес из 750 бесплатных** → помещается с запасом.

## Боты внутри

| Бот | Папка | Что делает |
|-----|-------|------------|
| 🅰️ КрасNEWs | `bots/bot_a_krsk_news/` | Новости Красноярска (RSS парсинг) |
| 🅱️ КРСК Вакансии | `bots/bot_b_krsk_vacancies/` | Вакансии Красноярска (hh.ru API) |
| © Тайны зодиака | `bots/bot_c_horoscope/` | Ежедневные гороскопы |
| 🅳 ИИ даром | `bots/bot_d_ai_free/` | Бесплатные AI-инструменты |
| 🅴 AI Дайджест | `bots/bot_e_ai_digest/` | Новости AI (Хабр, VC.ru, TechCrunch, HN) |

## Структура

```
combined_bot/
├── launcher.py          ← главный супервизор: запускает 5 subprocess + /health сервер
├── requirements.txt     ← общий requirements (feedparser + requests)
├── Dockerfile           ← Docker-образ
├── Procfile             ← команда запуска
├── render.yaml          ← конфиг для Render Blueprint
├── .env                 ← ЛОКАЛЬНЫЙ референс всех переменных (НЕ в git!)
├── .gitignore
├── .dockerignore
└── bots/
    ├── bot_a_krsk_news/      ← оригинальный код бота A
    ├── bot_b_krsk_vacancies/ ← оригинальный код бота B
    ├── bot_c_horoscope/      ← оригинальный код бота C
    ├── bot_d_ai_free/        ← оригинальный код бота D
    └── bot_e_ai_digest/      ← оригинальный код бота E (AI Дайджест)
```

## Как работает launcher.py

1. При старте читает env vars (`BOT_A_TOKEN`, `BOT_A_CHANNEL`, ... `BOT_E_TOKEN`, `BOT_E_CHANNEL`, `ADMIN_USER_ID`, `AI_API_KEY`)
2. Для каждого бота:
   - Запускает `python -u main.py` в подпапке как subprocess
   - Подставляет в subprocess свой `os.environ` (токен, channel, расписание, порт)
   - Каждый бот пишет свой `posted.json` в `bots/<dir>/data/` (не конфликтуют)
3. Запускает watchdog-поток, который каждые 15 секунд проверяет subprocesses и рестартует упавшие
4. Главный поток держит HTTP-сервер на `PORT` (8080):
   - `/health` → JSON `{"status":"ok","bots_running":5,"bots_total":5}`
   - `/` или `/status` → красивая HTML-страница со статусом всех 5 ботов

## Расчёт ресурсов Render free-плана

- 1 Web Service (free) = **750 instance hours/мес**
- 1 сервис × 24 ч × 30 дн = **720 часов/мес** → помещается!
- Память: 512 MB RAM
  - launcher.py ≈ 30 MB
  - 5 subprocess × ~80 MB = 400 MB
  - Итого ≈ 430 MB → ~80 MB запас
- CPU: 0.1 vCPU shared — боты в основном idle, хватает

⚠️ **Если 5-й бот не помещается по памяти** — у бота E можно отключить картинки:
в `launcher.py` в блоке `bot_e_ai_digest` добавь `"ENABLE_IMAGES": "false"`.
Это сэкономит ~30-50 MB RAM.

## Что нужно настроить

В Render → Environment ввести **6 секретов** (5 токенов ботов + 1 ключ Groq):
- `BOT_A_TOKEN`, `BOT_B_TOKEN`, `BOT_C_TOKEN`, `BOT_D_TOKEN`, `BOT_E_TOKEN`
- `AI_API_KEY`

Channel IDs для A, B, C, D уже зашиты в `render.yaml`.
**Для бота E нужно ввести `BOT_E_CHANNEL` вручную** (т.к. ты не прислал его в чате).

Все остальные env vars (расписание, AI_BASE_URL и т.д.) уже зашиты в `render.yaml` со значениями.
