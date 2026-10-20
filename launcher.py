#!/usr/bin/env python3
"""
 =============================================================================
  Combined Launcher — 4 Telegram-бота в одном Render free-сервисе
 =============================================================================

 Принцип:
  • Каждый бот запускается как отдельный subprocess (свои env-переменные)
  • Главный процесс держит tiny HTTP-сервер на PORT (8080) — для Render /health
  • Watchdog каждые 15 секунд проверяет subprocesses и рестартует упавшие
  • Все 4 бота работают 24/7, потребляя 1 Render free instance (720 ч/мес
    из 750 бесплатных — хватает с запасом)

 Почему subprocess, а не threading:
  • У каждого бота в main.py есть module-level чтение os.environ.get()
    (TELEGRAM_BOT_TOKEN и т.д.). При import в один процесс они бы
    перетёрлись. Subprocess даёт каждому боту свой os.environ.
  • Если один бот крашится, остальные продолжают работать.
 =============================================================================

 Env vars (все ставятся в Render → Environment):

  Общие (одинаковые для всех ботов):
    ADMIN_USER_ID     — твой Telegram user_id
    AI_API_KEY        — ключ Groq (gsk_...)
    AI_BASE_URL       — https://api.groq.com/openai/v1
    AI_MODEL          — openai/gpt-oss-20b

  По ботам (токены и каналы):
    BOT_A_TOKEN       — токен бота A (КрасNEWs)
    BOT_A_CHANNEL     — ID канала A
    BOT_B_TOKEN       — токен бота B (КРСК Вакансии)
    BOT_B_CHANNEL     — ID канала B
    BOT_C_TOKEN       — токен бота C (Тайны зодиака)
    BOT_C_CHANNEL     — ID канала C
    BOT_D_TOKEN       — токен бота D (ИИ даром)
    BOT_D_CHANNEL     — ID канала D

  Системные (Render проставляет автоматически):
    PORT              — 8080 (HTTP-порт health-сервера)
    TZ                — Europe/Moscow
    PYTHONUNBUFFERED  — 1
"""

import os
import sys
import time
import subprocess
import threading
import logging
import signal
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# --------------------------------------------------------------------------
# Логирование
# --------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("launcher")

# --------------------------------------------------------------------------
# Базовые пути
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
BOTS_DIR = BASE_DIR / "bots"

# --------------------------------------------------------------------------
# Конфигурация каждого бота
# --------------------------------------------------------------------------
# Per-bot env vars (параметры постинга, расписание, источник картинок)
# Токены и channel_id берём из общих env vars (BOT_X_TOKEN, BOT_X_CHANNEL)
PER_BOT_CONFIG = {
    "bot_a_krsk_news": {
        "name": "kras-news",
        "env": {
            "TELEGRAM_BOT_TOKEN": os.environ.get("BOT_A_TOKEN", ""),
            "TELEGRAM_CHANNEL_ID": os.environ.get("BOT_A_CHANNEL", ""),
            "POSTS_PER_DAY": "5",
            "WORKING_HOURS_START": "7",
            "WORKING_HOURS_END": "23",
            "IMAGE_SOURCE": "both",
            "ENABLE_PREVIEW": "true",
            "ENABLE_BREAKING_NEWS": "true",
            "BREAKING_NEWS_SCORE": "70",
            "BREAKING_NEWS_COOLDOWN": "180",
            "PORT": "8081",  # внутренний порт бота (не экспонируется Render)
        },
    },
    "bot_b_krsk_vacancies": {
        "name": "krsk-vacancies",
        "env": {
            "TELEGRAM_BOT_TOKEN": os.environ.get("BOT_B_TOKEN", ""),
            "TELEGRAM_CHANNEL_ID": os.environ.get("BOT_B_CHANNEL", ""),
            "POSTS_PER_DAY": "8",
            "WORKING_HOURS_START": "8",
            "WORKING_HOURS_END": "22",
            "IMAGE_SOURCE": "generated",
            "ENABLE_PREVIEW": "true",
            "PORT": "8082",
        },
    },
    "bot_c_horoscope": {
        "name": "horoscope",
        "env": {
            "TELEGRAM_BOT_TOKEN": os.environ.get("BOT_C_TOKEN", ""),
            "TELEGRAM_CHANNEL_ID": os.environ.get("BOT_C_CHANNEL", ""),
            "POSTS_PER_DAY": "3",
            "PORT": "8083",
        },
    },
    "bot_d_ai_free": {
        "name": "ai-free",
        "env": {
            "TELEGRAM_BOT_TOKEN": os.environ.get("BOT_D_TOKEN", ""),
            "TELEGRAM_CHANNEL_ID": os.environ.get("BOT_D_CHANNEL", ""),
            "POSTS_PER_DAY": "4",
            "WORKING_HOURS_START": "8",
            "WORKING_HOURS_END": "23",
            "IMAGE_SOURCE": "both",
            "ENABLE_PREVIEW": "true",
            "PORT": "8084",
        },
    },
    "bot_e_ai_digest": {
        "name": "ai-digest",
        "env": {
            "TELEGRAM_BOT_TOKEN": os.environ.get("BOT_E_TOKEN", ""),
            "TELEGRAM_CHANNEL_ID": os.environ.get("BOT_E_CHANNEL", ""),
            "POSTS_PER_DAY": "4",
            "WORKING_HOURS_START": "8",
            "WORKING_HOURS_END": "23",
            "POST_TIMES": "09:00,12:00,15:00,18:00,21:00",
            "IMAGE_SOURCE": "both",
            "ENABLE_PREVIEW": "true",
            "ENABLE_BREAKING_NEWS": "true",
            "BREAKING_NEWS_SCORE": "70",
            "BREAKING_NEWS_COOLDOWN": "360",
            "PORT": "8085",
        },
    },
}

# Общие env vars для всех ботов
COMMON_ENV = {
    "ADMIN_USER_ID": os.environ.get("ADMIN_USER_ID", ""),
    "AI_API_KEY": os.environ.get("AI_API_KEY", ""),
    "AI_BASE_URL": os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1"),
    "AI_MODEL": os.environ.get("AI_MODEL", "openai/gpt-oss-20b"),
    "ENABLE_IMAGES": "true",
    "IMAGE_WIDTH": "1024",
    "IMAGE_HEIGHT": "576",
    "PREVIEW_BEFORE_MINUTES": "15",
    "TZ": os.environ.get("TZ", "Europe/Moscow"),
    "PYTHONUNBUFFERED": "1",
}

# Словарь процессов: bot_dir -> subprocess.Popen
processes: dict = {}
# Метрики: bot_dir -> {"starts": N, "last_start": iso, "last_rc": int}
metrics: dict = {}


def start_bot(bot_dir: str) -> subprocess.Popen | None:
    """Запустить одного бота как subprocess с его окружением."""
    cfg = PER_BOT_CONFIG[bot_dir]
    name = cfg["name"]
    bot_path = BOTS_DIR / bot_dir
    if not bot_path.exists():
        log.error(f"[{name}] bot dir not found: {bot_path}")
        return None

    # Готовим окружение: копируем текущее + общие + per-bot
    env = os.environ.copy()
    env.update(COMMON_ENV)

    # Per-bot state files (чтобы боты не перетёрли друг другу posted.json)
    state_dir = bot_path / "data"
    state_dir.mkdir(exist_ok=True)
    env["POSTED_FILE"] = str(state_dir / "posted.json")
    env["LOG_FILE"] = str(state_dir / "bot.log")
    env.update(cfg["env"])

    log.info(f"[{name}] starting subprocess (dir={bot_dir})")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "main.py"],
            cwd=str(bot_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=-1,  # системный буфер (binary mode не поддерживает line buffering)
        )
    except Exception as e:
        log.exception(f"[{name}] failed to start: {e}")
        return None

    # Поток-читатель stdout → лог с префиксом
    threading.Thread(
        target=_pipe_output,
        args=(name, proc),
        daemon=True,
    ).start()

    metrics[bot_dir] = {
        "starts": metrics.get(bot_dir, {}).get("starts", 0) + 1,
        "last_start": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_rc": None,
    }
    return proc


def _pipe_output(name: str, proc: subprocess.Popen):
    """Читает stdout subprocess и пишет в наш лог с префиксом [name]."""
    try:
        for raw in iter(proc.stdout.readline, b""):
            try:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    print(f"[{name}] {line}", flush=True)
            except Exception:
                pass
    except Exception as e:
        log.debug(f"[{name}] output reader stopped: {e}")


def supervisor():
    """Watchdog: проверяет subprocesses каждые 15s и рестартует упавшие."""
    while True:
        time.sleep(15)
        for bot_dir, cfg in PER_BOT_CONFIG.items():
            name = cfg["name"]
            proc = processes.get(bot_dir)
            if proc is None:
                continue
            rc = proc.poll()
            if rc is not None:
                metrics.setdefault(bot_dir, {})["last_rc"] = rc
                log.warning(f"[{name}] exited rc={rc}. Restarting in 10s...")
                time.sleep(10)
                processes[bot_dir] = start_bot(bot_dir)


def start_health_server():
    """Tiny HTTP-сервер на PORT (Render health check)."""
    port = int(os.environ.get("PORT", "8080"))

    class H(BaseHTTPRequestHandler):
        def _send_json(self, code, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                alive = sum(
                    1 for p in processes.values() if p and p.poll() is None
                )
                total = len(PER_BOT_CONFIG)
                self._send_json(
                    200 if alive == total else 503,
                    {"status": "ok" if alive == total else "degraded",
                     "bots_running": alive,
                     "bots_total": total},
                )
            elif self.path in ("/", "/status"):
                self._render_status_page()
            else:
                self.send_response(404)
                self.end_headers()

        def _render_status_page(self):
            rows = []
            alive_count = 0
            for bot_dir, cfg in PER_BOT_CONFIG.items():
                name = cfg["name"]
                proc = processes.get(bot_dir)
                if proc and proc.poll() is None:
                    status = '<span style="color:#4dd964">✅ running</span>'
                    alive_count += 1
                else:
                    status = '<span style="color:#ff6b6b">❌ dead</span>'
                m = metrics.get(bot_dir, {})
                starts = m.get("starts", 0)
                last_start = m.get("last_start", "—")
                last_rc = m.get("last_rc", "—")
                rows.append(
                    f"<tr><td>{name}</td><td><code>{bot_dir}</code></td>"
                    f"<td>{status}</td><td>{starts}</td>"
                    f"<td>{last_start}</td><td>{last_rc}</td></tr>"
                )
            rows_html = "\n".join(rows)
            html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>5 Bots Combined — Status</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: linear-gradient(135deg, #0a1e3c 0%, #1a3a6c 100%);
       color: #fff; padding: 20px; margin: 0; min-height: 100vh; }}
.wrap {{ max-width: 800px; margin: 0 auto; }}
h1 {{ color: #ffd700; text-align: center; margin-bottom: 8px; }}
.sub {{ text-align: center; opacity: 0.8; margin-bottom: 24px; }}
table {{ border-collapse: collapse; width: 100%; background: rgba(255,255,255,0.05);
        border-radius: 12px; overflow: hidden; }}
th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.1); }}
th {{ background: rgba(255,215,0,0.15); color: #ffd700; font-weight: 600; }}
td code {{ background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
.summary {{ display: flex; gap: 16px; justify-content: center; margin: 24px 0; }}
.card {{ background: rgba(255,255,255,0.05); padding: 16px 24px; border-radius: 12px;
        border: 1px solid rgba(255,215,0,0.2); text-align: center; }}
.card .num {{ font-size: 32px; font-weight: 700; color: #4dd964; }}
.card .lbl {{ opacity: 0.7; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
a {{ color: #4dd964; }}
</style></head><body><div class="wrap">
<h1>🤖 5 Bots Combined</h1>
<p class="sub">Один Render-сервис → 5 независимых Telegram-ботов</p>
<div class="summary">
  <div class="card"><div class="num">{alive_count}/{len(PER_BOT_CONFIG)}</div><div class="lbl">Running</div></div>
  <div class="card"><div class="num">{sum(m.get('starts',0) for m in metrics.values())}</div><div class="lbl">Total starts</div></div>
</div>
<table>
  <tr><th>Bot</th><th>Dir</th><th>Status</th><th>Starts</th><th>Last start</th><th>Last rc</th></tr>
  {rows_html}
</table>
<p style="text-align:center; margin-top:24px;">
  <a href="/health">/health</a> · Render cron ping target
</p>
</div></body></html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args, **kwargs):
            pass  # тишина в логах

    log.info(f"🌐 Health server on :{port}")
    server = HTTPServer(("0.0.0.0", port), H)
    server.serve_forever()


def shutdown(signum, frame):
    """Graceful shutdown — terminates all subprocesses."""
    log.info(f"Received signal {signum}, shutting down all bots...")
    for bot_dir, cfg in PER_BOT_CONFIG.items():
        name = cfg["name"]
        proc = processes.get(bot_dir)
        if proc and proc.poll() is None:
            log.info(f"[{name}] terminating...")
            try:
                proc.terminate()
            except Exception:
                pass
    time.sleep(3)
    for bot_dir, cfg in PER_BOT_CONFIG.items():
        proc = processes.get(bot_dir)
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
    sys.exit(0)


def main():
    log.info("=" * 60)
    log.info("Combined launcher starting")
    log.info(f"BASE_DIR = {BASE_DIR}")
    log.info(f"BOTS_DIR = {BOTS_DIR}")
    log.info("=" * 60)

    # Проверяем обязательные env vars
    required = [
        "ADMIN_USER_ID", "AI_API_KEY",
        "BOT_A_TOKEN", "BOT_A_CHANNEL",
        "BOT_B_TOKEN", "BOT_B_CHANNEL",
        "BOT_C_TOKEN", "BOT_C_CHANNEL",
        "BOT_D_TOKEN", "BOT_D_CHANNEL",
        "BOT_E_TOKEN", "BOT_E_CHANNEL",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        log.error(f"❌ Missing required env vars: {missing}")
        log.error("   Set them in Render dashboard → Environment")
        log.error("   (continuing anyway — health server will report 0 running)")
    else:
        log.info("✅ All required env vars present")

    # Запускаем все 4 бота
    for bot_dir in PER_BOT_CONFIG:
        processes[bot_dir] = start_bot(bot_dir)
        time.sleep(1)  # небольшая пауза чтобы не перегружать CPU при старте

    # Watchdog в фоне
    threading.Thread(target=supervisor, daemon=True).start()

    # Обработчики сигналов (Render шлёт SIGTERM при редеплое)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Главный поток — health-сервер (блокирующий)
    try:
        start_health_server()
    except KeyboardInterrupt:
        shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
