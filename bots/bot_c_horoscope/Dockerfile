FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --progress-bar off -r requirements.txt && rm -rf /root/.cache/pip
COPY main.py ./
RUN mkdir -p /app/data
ENV TZ=Europe/Moscow PYTHONUNBUFFERED=1 POSTED_FILE=/app/data/posted_urls.json LOG_FILE=/app/data/bot.log
EXPOSE 8080
HEALTHCHECK --interval=5m --timeout=30s --retries=3 --start-period=60s CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["python", "-u", "main.py"]
