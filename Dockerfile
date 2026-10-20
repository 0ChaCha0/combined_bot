# ============================================================
# Dockerfile — 4 бота в одном контейнере
# ============================================================
# Базовый образ Python
# ------------------------------------------------------------

FROM python:3.11-slim

WORKDIR /app

# Системные пакеты: curl для healthcheck, tzdata для таймзоны
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Python-зависимости (одинаковые для всех 4 ботов)
COPY requirements.txt .
RUN pip install --no-cache-dir --progress-bar off -r requirements.txt \
    && rm -rf /root/.cache/pip

# Копируем launcher и код всех 4 ботов
COPY launcher.py ./
COPY bots/ ./bots/

# Таймзона по умолчанию — Москва
ENV TZ=Europe/Moscow
ENV PYTHONUNBUFFERED=1

# Render должен знать, что слушать на этом порту
EXPOSE 8080

# Healthcheck: проверяем /health каждые 5 минут
HEALTHCHECK --interval=5m --timeout=30s --retries=3 --start-period=120s \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Запускаем launcher (он поднимет 4 бота + health-сервер)
CMD ["python", "-u", "launcher.py"]
