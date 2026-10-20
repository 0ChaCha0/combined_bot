#!/usr/bin/env python3
"""
🤖 AI Дайджест — Полностью автономный бот-автопостинг
=====================================================

Что умеет (без вашего участия после настройки):
  ✓ Парсит 8 RSS-источников каждые 30 минут
  ✓ Фильтрует статьи по AI-тематики (умный фильтр)
  ✓ Делает саммари на русском через AI (Groq/OpenAI)
  ✓ Публикует 3-5 постов в день в Telegram-канал
  ✓ Автоматически перезапускается при сбоях
  ✓ Уведомляет админа об ошибках
  ✓ Чистит старые логи и кэш автоматически
  ✓ Web-панель статуса на http://localhost:8080
  ✓ Авто-выбор лучшего времени для постинга (пиковые часы)

Запуск:
  1. python setup.py          # мастeр настройки (один раз)
  2. python main.py           # запуск бота
  или
  docker compose up -d         # запуск в Docker (24/7)
"""

import os
import json
import time
import logging
import threading
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

# ============================================================================
# ИСПРАВЛЕНИЕ КОДИРОВКИ КИРИЛЛИЦЫ ДЛЯ WINDOWS CONSOLE
# ============================================================================
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.setdefault('PYTHONUTF8', '1')


# ============================================================================
# ЗАГРУЗКА .env ФАЙЛА (без внешних зависимостей)
# ============================================================================

def load_env_file(env_path: str = '.env') -> None:
    """Загрузить переменные из .env файла в os.environ.

    Не переопределяет переменные, уже заданные в системе
    (это позволяет Render/CI переопределять значения).
    """
    path = Path(env_path)
    if not path.exists():
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Пропускаем пустые строки и комментарии
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                # Убираем кавычки если есть
                if value and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                # Не переопределяем системные переменные
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"⚠️ Не удалось загрузить .env: {e}", file=sys.stderr)


# Загружаем .env ДО чтения конфигурации
load_env_file()

import feedparser
import requests

# ============================================================================
# КОНФИГУРАЦИЯ (читается из .env, который создаёт setup.py)
# ============================================================================

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')  # @username или -100...
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '')  # для уведомлений об ошибках

# Прокси (если VPN системный — оставьте пустым, requests подхватит HTTP_PROXY)
PROXY_URL = os.environ.get('PROXY_URL', '').strip()

# Картинки к постам
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
# Источник: 'article' (только из RSS), 'generated' (только AI), 'both' (оба)
IMAGE_SOURCE = os.environ.get('IMAGE_SOURCE', 'both').lower()
# Размер картинки (ширина, высота)
IMAGE_WIDTH = int(os.environ.get('IMAGE_WIDTH', '1024'))
IMAGE_HEIGHT = int(os.environ.get('IMAGE_HEIGHT', '576'))

# Предпросмотр и расписание (управляется через админ-команды)
PREVIEW_MINUTES = int(os.environ.get('PREVIEW_MINUTES', '15'))  # предпросмотр за 15 мин
IMPORTANT_NEWS_THRESHOLD = int(os.environ.get('IMPORTANT_NEWS_THRESHOLD', '70'))
IMPORTANT_NEWS_COOLDOWN = int(os.environ.get('IMPORTANT_NEWS_COOLDOWN', '360'))  # минут
SKIP_NEXT_POST = False  # флаг для /skip
NEXT_PREVIEW_SENT = False  # флаг: уже отправлен предпросмотр

# Расписание постинга (конкретные часы)
# Формат: "09:00,12:00,15:00,18:00,21:00"
# Пусто = использовать POSTS_PER_DAY с равными интервалами
POST_TIMES = os.environ.get('POST_TIMES', '').strip()

# Предпросмотр постов
# За сколько минут до публикации присылать предпросмотр админу
# (раньше было PREVIEW_BEFORE_HOURS=4, теперь 15 минут по умолчанию)
PREVIEW_BEFORE_MINUTES = int(os.environ.get('PREVIEW_BEFORE_MINUTES', '15'))
# Совместимость со старой переменной (часы)
PREVIEW_BEFORE_HOURS = float(os.environ.get('PREVIEW_BEFORE_HOURS', '0'))
if PREVIEW_BEFORE_HOURS > 0:
    PREVIEW_BEFORE_MINUTES = int(PREVIEW_BEFORE_HOURS * 60)
ENABLE_PREVIEW = os.environ.get('ENABLE_PREVIEW', 'true').lower() == 'true'

# Уведомления о важных новостях
ENABLE_BREAKING_NEWS = os.environ.get('ENABLE_BREAKING_NEWS', 'true').lower() == 'true'
# Минимальный балл важности (0-100), чтобы новость считалась "важной"
BREAKING_NEWS_SCORE = int(os.environ.get('BREAKING_NEWS_SCORE', '70'))
# Не присылать уведомления чаще, чем раз в N минут
BREAKING_NEWS_COOLDOWN = int(os.environ.get('BREAKING_NEWS_COOLDOWN', '360'))

# AI для саммаризации
AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.groq.com/openai/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'openai/gpt-oss-20b')  # дефолт — рабочая модель 2026

# Расписание
POSTS_PER_DAY = int(os.environ.get('POSTS_PER_DAY', '4'))
WORKING_HOURS_START = int(os.environ.get('WORKING_HOURS_START', '8'))
WORKING_HOURS_END = int(os.environ.get('WORKING_HOURS_END', '23'))
# Пиковые часы — публикуем чаще в это время
PEAK_HOURS = [9, 12, 15, 18, 21]

# Техническое
POSTED_FILE = os.environ.get('POSTED_FILE', 'posted_urls.json')
LOG_FILE = os.environ.get('LOG_FILE', 'bot.log')
PORT = int(os.environ.get('PORT', '8080'))
TIMEZONE = os.environ.get('TIMEZONE', 'Europe/Moscow')
MAX_POSTS_PER_CYCLE = int(os.environ.get('MAX_POSTS_PER_CYCLE', '1'))

# Авто-очистка
CLEANUP_DAYS = int(os.environ.get('CLEANUP_DAYS', '30'))  # храним posted только 30 дней

# Авто-восстановление
MAX_RESTART_DELAY = 600  # максимум 10 минут между рестартами
HEALTH_CHECK_INTERVAL = 3600  # час — проверка "завис ли бот"

# RSS-источники (можно расширять)
RSS_FEEDS = [
    'https://habr.com/ru/rss/articles/?tags=AI',
    'https://habr.com/ru/rss/articles/?tags=machine_learning',
    'https://habr.com/ru/rss/articles/?tags=neural_networks',
    'https://vc.ru/rss/all',
    'https://www.anthropic.com/news/rss.xml',
    'https://techcrunch.com/category/artificial-intelligence/feed/',
    'https://hnrss.org/newest?q=AI+OR+GPT+OR+LLM+OR+neural+network',
    'https://www.technologyreview.com/topic/artificial-intelligence/feed',
]

# === ПРОВЕРКА КЛЮЧЕВЫХ СЛОВ ===
# Сильные сигналы (одного достаточно, чтобы статья прошла фильтр)
STRONG_KEYWORDS = [
    'gpt', 'chatgpt', 'claude', 'gemini', 'midjourney', 'stable diffusion',
    'openai', 'anthropic', 'deepmind', 'llm', 'нейросет', 'промпт',
    'генеративн', 'машинн обучен', 'artificial intelligence',
]

# Слабые сигналы (нужно 2+, чтобы статья прошла фильтр)
WEAK_KEYWORDS = [
    'ai ', ' ии ', 'бот ', 'обучен', 'модель ', 'данные', 'трениров',
    'нейрон', 'алгоритм', 'автоматиз', 'компьютерн зрен', 'nlp',
    'machine learning', 'transformer', 'deep learning', 'dataset',
]


# ============================================================================
# ОПРЕДЕЛЕНИЕ ВАЖНЫХ НОВОСТЕЙ (BREAKING NEWS)
# ============================================================================

# Очень сильные сигналы (новость ТОЧНО важная) — каждый +30 баллов
BREAKING_STRONG = [
    'gpt-5', 'gpt 5', 'claude 4', 'claude4', 'gemini 3', 'gemini3',
    'midjourney 7', 'midjourney7', 'llama 4', 'llama4',
    'sora 2', 'sora2', 'ген 3', 'gen3',
    'agentic ai', 'agi ', 'a.g.i', 'superintelligence', 'суперинтеллект',
    'революц', 'прорыв', 'историческ', 'беспрецедент',
    'превзошл', 'превосходит', 'превзойд', 'обогнал',
    'обошел', 'вытеснил', 'заменил gpt', 'убил gpt',
    'выпустила gpt', 'представила gpt', 'анонсировала gpt',
    'openai выпустил', 'anthropic выпустил', 'google выпустил',
    'deepmind выпустил', 'meta выпустил',
    'leaked', 'утечка', 'утёк',
]

# Сильные сигналы (важная новость) — каждый +15 баллов
BREAKING_MEDIUM = [
    'новая модель', 'новый релиз', 'обновление модели',
    'выпущен', 'выпущена', 'релизнул', 'анонсировал', 'представил',
    'превзойдёт', 'превзойти', 'sota', 'state of the art',
    'benchmark', 'бенчмарк', 'побил рекорд',
    'бесплатно', 'open source', 'открыли код', 'опенсорс',
    'gpt-4.5', 'gpt 4.5', 'claude 3.5', 'gemini 2',
    'multimodal', 'мультимодальн',
    '500b', '1t', 'trillion parameters',
]

# Слабые сигналы (доп. контекст) — каждый +3 балла
BREAKING_WEAK = [
    'openai', 'anthropic', 'deepmind', 'google ai', 'meta ai',
    'новейш', 'последн', 'свеж',
    'большая модель', 'language model',
    'обучена на',
]


def score_news_significance(article: Dict) -> int:
    """Оценить важность новости по шкале 0-100."""
    text = (article.get('title', '') + ' ' + article.get('summary', '')).lower()
    score = 0

    # Очень сильные сигналы
    strong_hits = sum(1 for kw in BREAKING_STRONG if kw in text)
    score += strong_hits * 30

    # Средние сигналы
    medium_hits = sum(1 for kw in BREAKING_MEDIUM if kw in text)
    score += medium_hits * 15

    # Слабые сигналы
    weak_hits = sum(1 for kw in BREAKING_WEAK if kw in text)
    score += min(weak_hits, 5) * 3  # максимум 15 баллов

    # Бонус за комбинации (OpenAI + выпуск) — индикатор презентации
    if 'openai' in text and ('выпустил' in text or 'анонсировал' in text or 'представил' in text):
        score += 10
    if 'anthropic' in text and ('выпустил' in text or 'анонсировал' in text):
        score += 10
    if 'deepmind' in text and ('выпустил' in text or 'анонсировал' in text):
        score += 10

    return min(score, 100)


# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
    ],
)
logger = logging.getLogger('ai_digest_bot')


# ============================================================================
# ПРОКСИ
# ============================================================================

def get_proxies() -> Optional[Dict[str, str]]:
    """Получить прокси для requests (если задан PROXY_URL)."""
    if PROXY_URL:
        return {'http': PROXY_URL, 'https': PROXY_URL}
    return None


# ============================================================================
# АВТО-ПОИСК МОДЕЛИ AI (если заданная не работает)
# ============================================================================

FALLBACK_MODELS = [
    'openai/gpt-oss-20b',            # Рекомендуемая на 2026
    'openai/gpt-oss-120b',            # Лучшее качество
    'llama-3.1-8b-instant',          # Старая, но может работать
    'llama-3.3-70b-versatile',       # Legacy
    'qwen/qwen3.8-27b',              # Альтернатива
]


def test_ai_model(model_name: str) -> bool:
    """Проверить, отвечает ли модель."""
    if not AI_API_KEY:
        return False
    try:
        response = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={
                'Authorization': f'Bearer {AI_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model_name,
                'messages': [{'role': 'user', 'content': 'OK'}],
                'max_tokens': 5,
            },
            timeout=15,
            proxies=get_proxies(),
        )
        data = response.json()
        return 'choices' in data
    except Exception:
        return False


def find_working_ai_model() -> str:
    """Найти первую рабочую модель из списка."""
    global AI_MODEL
    logger.info(f"🔍 Проверяю модель AI: {AI_MODEL}")
    if test_ai_model(AI_MODEL):
        logger.info(f"✅ Модель {AI_MODEL} работает")
        return AI_MODEL

    logger.warning(f"⚠️ Модель {AI_MODEL} недоступна — ищу замену...")
    for model in FALLBACK_MODELS:
        if model == AI_MODEL:
            continue
        logger.info(f"   Пробую: {model}...")
        if test_ai_model(model):
            logger.info(f"✅ Найдена рабочая модель: {model}")
            AI_MODEL = model
            return model
    logger.error("❌ Не нашёл ни одной рабочей модели")
    return AI_MODEL


# ============================================================================
# ХРАНИЛИЩЕ СОСТОЯНИЯ
# ============================================================================

def load_posted_urls() -> List[Dict]:
    """Загрузить список опубликованных статей."""
    path = Path(POSTED_FILE)
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('posted_urls', [])
    except Exception as e:
        logger.warning(f"Не удалось загрузить posted_urls.json: {e}")
        return []


def save_posted_url(url: str, title: str = '') -> None:
    """Добавить URL и сделать авто-очистку старых."""
    posted = load_posted_urls()
    posted.append({
        'url': url,
        'title': title,
        'time': datetime.now().isoformat(),
    })

    # Авто-очистка: храним только последние N дней
    cutoff = datetime.now() - timedelta(days=CLEANUP_DAYS)
    posted_clean = []
    for item in posted:
        try:
            t = item.get('time', '')
            if t:
                item_time = datetime.fromisoformat(t)
                if item_time > cutoff:
                    posted_clean.append(item)
            else:
                posted_clean.append(item)
        except Exception:
            posted_clean.append(item)

    # Храним максимум 500 записей
    posted_clean = posted_clean[-500:]

    try:
        with open(POSTED_FILE, 'w', encoding='utf-8') as f:
            json.dump({'posted_urls': posted_clean}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить posted_urls.json: {e}")


def is_already_posted(url: str) -> bool:
    """Проверить, был ли URL уже опубликован."""
    posted = load_posted_urls()
    return any(
        (item.get('url') == url if isinstance(item, dict) else item == url)
        for item in posted
    )


# ============================================================================
# УМНЫЙ ФИЛЬТР AI-СТАТЕЙ
# ============================================================================

def ai_relevance_score(article: Dict) -> int:
    """Оценить релевантность статьи AI (0-10)."""
    text = (article.get('title', '') + ' ' + article.get('summary', '')).lower()

    # 1. Сильные сигналы
    strong_matches = sum(1 for kw in STRONG_KEYWORDS if kw in text)

    # 2. Слабые сигналы
    weak_matches = sum(1 for kw in WEAK_KEYWORDS if kw in text)

    # Логика: 1 сильный = >=5 баллов, 2+ слабых = +2 балла
    score = strong_matches * 5 + max(0, weak_matches - 1) * 2
    return score


def is_ai_related(article: Dict) -> bool:
    """Статья проходит фильтр если score >= 5."""
    return ai_relevance_score(article) >= 5


# ============================================================================
# RSS ПАРСИНГ
# ============================================================================

def fetch_rss_entries() -> List[Dict]:
    """Получить все свежие записи из всех RSS-лент (с картинками)."""
    all_entries = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                logger.warning(f"⚠️ RSS повреждён: {feed_url}")
            for entry in feed.entries[:15]:
                # Извлекаем картинку из статьи (если есть)
                image_url = extract_image_from_entry(entry)

                all_entries.append({
                    'title': getattr(entry, 'title', ''),
                    'link': getattr(entry, 'link', ''),
                    'summary': getattr(entry, 'summary', ''),
                    'published': getattr(entry, 'published', ''),
                    'source': feed_url,
                    'image_url': image_url,
                })
        except Exception as e:
            logger.error(f"Ошибка парсинга {feed_url}: {e}")
    logger.info(f"📦 Всего статей: {len(all_entries)}")
    return all_entries


def extract_image_from_entry(entry) -> Optional[str]:
    """Извлечь URL картинки из RSS-записи.

    Ищем по порядку:
    1. media_content (тоже самое что <media:content>)
    2. media_thumbnail
    3. enclosures с типом image
    4. <img src="..."> в summary
    """
    # 1. media_content
    try:
        mc = getattr(entry, 'media_content', None) or getattr(entry, 'media_thumbnail', None)
        if mc and isinstance(mc, list) and len(mc) > 0:
            url = mc[0].get('url') if isinstance(mc[0], dict) else None
            if url and url.startswith('http'):
                return url
    except Exception:
        pass

    # 2. enclosures
    try:
        enclosures = getattr(entry, 'enclosures', None)
        if enclosures:
            for enc in enclosures:
                if isinstance(enc, dict):
                    enc_type = str(enc.get('type', '')).lower()
                    if 'image' in enc_type:
                        href = enc.get('href', '')
                        if href.startswith('http'):
                            return href
    except Exception:
        pass

    # 3. links
    try:
        links = getattr(entry, 'links', None)
        if links:
            for link in links:
                if isinstance(link, dict):
                    link_type = str(link.get('type', '')).lower()
                    if 'image' in link_type:
                        href = link.get('href', '')
                        if href.startswith('http'):
                            return href
    except Exception:
        pass

    # 4. <img src="..."> в summary
    try:
        summary = getattr(entry, 'summary', '') or getattr(entry, 'content', [{}])[0].get('value', '')
        if summary:
            # Ищем первый img src
            match = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', summary)
            if match:
                url = match.group(1)
                if url.startswith('//'):
                    url = 'https:' + url
                elif url.startswith('/'):
                    # относительный URL — пропускаем
                    return None
                if url.startswith('http'):
                    return url
    except Exception:
        pass

    return None


def filter_fresh_articles(articles: List[Dict], hours: int = 72) -> List[Dict]:
    """Только свежие статьи (последние N часов)."""
    now = datetime.now()
    fresh = []
    for art in articles:
        if not art.get('published'):
            fresh.append(art)  # если даты нет — оставляем
            continue
        parsed_date = None
        for fmt in [
            '%a, %d %b %Y %H:%M:%S %z',
            '%a, %d %b %Y %H:%M:%S %Z',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%d %H:%M:%S',
        ]:
            try:
                parsed_date = datetime.strptime(art['published'], fmt)
                break
            except ValueError:
                continue
        if parsed_date is None:
            fresh.append(art)
            continue
        try:
            age = (now - parsed_date.replace(tzinfo=None)).total_seconds()
            if age < hours * 3600:
                fresh.append(art)
        except Exception:
            fresh.append(art)
    return fresh


def select_best_articles(articles: List[Dict], limit: int = 5) -> List[Dict]:
    """Выбрать лучшие статьи (по релевантности), не опубликованные."""
    candidates = []
    for art in articles:
        if not art.get('link') or is_already_posted(art['link']):
            continue
        art['_score'] = ai_relevance_score(art)
        candidates.append(art)

    # Сортируем по убыванию score
    candidates.sort(key=lambda x: x['_score'], reverse=True)
    return candidates[:limit]


# ============================================================================
# AI-САММАРИЗАЦИЯ С АВТО-ПЕРЕПАДНИЕМ
# ============================================================================

AI_FAILURE_COUNT = 0
AI_FAILURE_THRESHOLD = 5
AI_DISABLED = False


def make_summary(article: Dict) -> Optional[str]:
    """Сделать саммари статьи. Если AI падает 5+ раз — fallback."""
    global AI_FAILURE_COUNT, AI_DISABLED

    if not AI_API_KEY or AI_DISABLED:
        return format_without_ai(article)

    title = article.get('title', 'Без названия')[:300]
    summary_raw = clean_html(article.get('summary', ''))[:1500]
    link = article.get('link', '')

    prompt = f"""Ты — редактор Telegram-канала "AI Дайджест".
Сделай из этой новости готовый пост для канала на русском.

Правила:
1. Цепляющий заголовок с эмодзи в начале (например "🚀 GPT-5 вышел")
2. 3-5 ключевых пунктов с эмодзи (✅, ⚡️, 📊, 💡)
3. "Почему это важно" — 1-2 строки
4. В конце — ссылка на источник и 2-3 хештега
5. Объём: 500-900 символов
6. Если статья на английском — переведи на русский
7. Стиль живой, понятный, без сложных терминов
8. НЕ используй слова "инновационный", "уникальный", "высококачественный"

ИСТОЧНИК:
Заголовок: {title}
Текст: {summary_raw}
Ссылка: {link}

После поста, на новой строке, добавь строку вида:
IMAGE_PROMPT: <английские ключевые слова для генерации картинки, 5-15 слов>
Например: IMAGE_PROMPT: futuristic robot holding smartphone, cyberpunk style

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={
                    'Authorization': f'Bearer {AI_API_KEY}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.7,
                    'max_tokens': 1500,
                },
                timeout=60,
                proxies=get_proxies(),
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()

            # Сбрасываем счётчик ошибок
            AI_FAILURE_COUNT = 0

            # Извлекаем IMAGE_PROMPT (если AI его сгенерировал)
            image_prompt = ''
            img_match = re.search(r'IMAGE_PROMPT:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if img_match:
                image_prompt = img_match.group(1).strip()
                # Удаляем строку из поста
                content = re.sub(r'IMAGE_PROMPT:.*?(?:\n|$)', '', content, flags=re.IGNORECASE).strip()

            # Сохраняем промпт в article для дальнейшего использования
            article['_image_prompt'] = image_prompt

            if link and link not in content:
                content += f"\n\n🔗 {link}"
            return content
        except Exception as e:
            logger.warning(f"AI попытка {attempt+1}/3 не удалась: {e}")
            time.sleep(2 * (attempt + 1))

    # Все 3 попытки провалились
    AI_FAILURE_COUNT += 1
    if AI_FAILURE_COUNT >= AI_FAILURE_THRESHOLD:
        logger.error(f"🚫 AI не отвечает {AI_FAILURE_COUNT} раз подряд — переключаемся в fallback-режим")
        AI_DISABLED = True
        notify_admin(f"⚠️ AI-провайдер недоступен уже {AI_FAILURE_COUNT} раз. Бот перешёл в fallback-режим (без AI).")
    return format_without_ai(article)


def clean_html(text: str) -> str:
    """Убрать HTML-теги из текста."""
    return re.sub(r'<[^>]+>', '', text)


def format_without_ai(article: Dict) -> str:
    """Fallback-формат без AI."""
    title = article.get('title', 'Без названия')[:200]
    summary = clean_html(article.get('summary', ''))[:500]
    link = article.get('link', '')
    return f"""📰 {title}

{summary}

🔗 {link}

#AI #нейросети #AIДайджест"""


# ============================================================================
# КАРТИНКИ ДЛЯ ПОСТОВ
# ============================================================================

import urllib.parse


def generate_image_url(prompt: str, seed: int = None) -> str:
    """Сгенерировать URL картинки через Pollinations.ai (бесплатно, без ключа).

    Pollinations.ai генерирует изображение при обращении к URL.
    Возвращает URL, который можно открыть или скачать.
    """
    if seed is None:
        seed = random.randint(1, 999999999)

    # Добавляем стиль для качества и неИИшного вида
    styled_prompt = f"{prompt}, professional photograph, editorial style, high quality, sharp focus, natural lighting, no text overlay, no watermark"
    encoded = urllib.parse.quote(styled_prompt, safe='')

    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}"
        f"&nologo=true&seed={seed}&model=flux"
    )


def download_image_bytes(url: str, timeout: int = 90) -> Optional[bytes]:
    """Скачать байты картинки по URL (для последующей загрузки в Telegram)."""
    try:
        logger.info(f"🖼️ Скачиваю картинку: {url[:80]}...")
        r = requests.get(url, timeout=timeout, proxies=get_proxies())
        r.raise_for_status()
        # Проверяем, что это действительно картинка
        content_type = r.headers.get('Content-Type', '').lower()
        if 'image' not in content_type and len(r.content) < 1000:
            logger.warning(f"⚠️ URL не содержит картинку (Content-Type: {content_type})")
            return None
        logger.info(f"✅ Картинка скачана: {len(r.content)} байт")
        return r.content
    except Exception as e:
        logger.error(f"Ошибка скачивания картинки: {e}")
        return None


def send_photo_to_telegram(image_bytes: bytes, caption: str) -> bool:
    """Отправить картинку с подписью в Telegram-канал."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        return False

    # Telegram limit: caption = 1024 символа
    caption = caption[:1020] + '...' if len(caption) > 1024 else caption

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {
        'photo': ('image.jpg', image_bytes, 'image/jpeg'),
    }
    data = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'caption': caption,
        'parse_mode': 'HTML',
    }

    for attempt in range(3):
        try:
            response = requests.post(url, files=files, data=data, timeout=90, proxies=get_proxies())
            result = response.json()
            if result.get('ok'):
                logger.info("✅ Пост с картинкой опубликован")
                return True
            # Если HTML парсинг упал — пробуем без parse_mode
            if 'parse' in str(result.get('description', '')).lower():
                data['parse_mode'] = ''
                # files уже отправлен — нужно переоткрыть
                files['photo'] = ('image.jpg', image_bytes, 'image/jpeg')
                continue
            logger.error(f"Telegram sendPhoto error: {result}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            logger.error(f"Ошибка отправки фото (попытка {attempt+1}/3): {e}")
            time.sleep(5 * (attempt + 1))

    return False


def get_post_image(article: Dict) -> Optional[bytes]:
    """Получить байты картинки для поста.

    Стратегия:
    1. Если IMAGE_SOURCE in ('article', 'both') — пробуем картинку из RSS
    2. Если IMAGE_SOURCE in ('generated', 'both') — генерируем через Pollinations
    """
    if not ENABLE_IMAGES:
        return None

    # 1. Пробуем картинку из статьи (реальная фотография — не AI)
    if IMAGE_SOURCE in ('article', 'both'):
        article_image = article.get('image_url')
        if article_image:
            image_bytes = download_image_bytes(article_image)
            if image_bytes:
                logger.info(f"✅ Используем картинку из статьи (реальное фото)")
                return image_bytes
            logger.warning("⚠️ Не удалось скачать картинку из статьи")

    # 2. Генерируем через Pollinations (как fallback)
    if IMAGE_SOURCE in ('generated', 'both'):
        # Промпт от AI или из заголовка статьи
        prompt = article.get('_image_prompt', '').strip()
        if not prompt:
            # Извлекаем ключевые слова из заголовка
            title = article.get('title', '')[:200]
            # Убираем стоп-слова
            prompt = f"news about {title}, technology, ai, digital"
        if not prompt:
            prompt = "artificial intelligence, technology, abstract digital"

        url = generate_image_url(prompt)
        image_bytes = download_image_bytes(url, timeout=120)
        if image_bytes:
            logger.info(f"✅ Сгенерирована картинка (промпт: {prompt[:50]}...)")
            return image_bytes

    return None


# ============================================================================
# TELEGRAM ПУБЛИКАЦИЯ
# ============================================================================

def send_to_telegram(text: str) -> bool:
    """Отправить пост в Telegram с авто-повторами."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        return False

    if len(text) > 4096:
        text = text[:4090] + '...'

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHANNEL_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False,
    }

    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=30, proxies=get_proxies())
            result = response.json()
            if result.get('ok'):
                logger.info("✅ Пост опубликован")
                return True
            # Если HTML парсинг упал — пробуем без parse_mode
            if 'parse' in str(result.get('description', '')).lower():
                payload['parse_mode'] = ''
                continue
            logger.error(f"Telegram error: {result}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            logger.error(f"Ошибка отправки (попытка {attempt+1}/3): {e}")
            time.sleep(5 * (attempt + 1))

    notify_admin("❌ Не удалось отправить пост в Telegram уже 3 попытки подряд")
    return False


def notify_admin(message: str) -> None:
    """Отправить уведомление админу (если задан ADMIN_USER_ID)."""
    if not ADMIN_USER_ID or not TELEGRAM_BOT_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            'chat_id': ADMIN_USER_ID,
            'text': f"🤖 AI Дайджест бот:\n\n{message}",
        }, timeout=30, proxies=get_proxies())
    except Exception:
        pass


# ============================================================================
# УМНОЕ РАСПИСАНИЕ
# ============================================================================

def is_working_hours() -> bool:
    """Сейчас рабочие часы для постинга?"""
    hour = datetime.now().hour
    return WORKING_HOURS_START <= hour < WORKING_HOURS_END


def calculate_next_interval() -> int:
    """Динамически вычислить интервал до следующего поста (минуты)."""
    hour = datetime.now().hour

    # Базовый интервал
    base_interval = (24 * 60) // max(POSTS_PER_DAY, 1)

    # Если сейчас пиковый час — публикуем чаще (интервал / 2)
    if hour in PEAK_HOURS:
        base_interval = base_interval // 2

    # Добавляем случайность ±30% (чтобы бот выглядел "человечнее")
    variance = random.uniform(0.7, 1.3)
    final_interval = int(base_interval * variance)

    # Минимум 30 минут, максимум 6 часов
    return max(30, min(360, final_interval))


# ============================================================================
# ГЛАВНЫЙ ЦИКЛ С АВТО-ВОССТАНОВЛЕНИЕМ
# ============================================================================

LAST_POST_TIME = None
BOT_START_TIME = datetime.now()


def publish_next_post() -> bool:
    """Выбрать и опубликовать лучший пост."""
    global LAST_POST_TIME

    try:
        articles = fetch_rss_entries()
        if not articles:
            logger.warning("RSS пустой")
            return False

        ai_articles = [a for a in articles if is_ai_related(a)]
        logger.info(f"🎯 AI-статей: {len(ai_articles)} из {len(articles)}")

        fresh = filter_fresh_articles(ai_articles, hours=72)
        logger.info(f"🕐 Свежих: {len(fresh)}")

        best = select_best_articles(fresh, limit=1)
        if not best:
            logger.info("🎉 Все свежие уже опубликованы — пропускаем")
            return False

        article = best[0]
        logger.info(f"📝 Публикуем: {article['title'][:80]}")

        post_text = make_summary(article)
        if not post_text:
            return False

        # Получаем картинку для поста (если включено)
        image_bytes = get_post_image(article)

        # Публикуем: картинка с подписью или только текст
        if image_bytes:
            success = send_photo_to_telegram(image_bytes, post_text)
            # Если отправка фото упала — пробуем текстом
            if not success:
                logger.warning("⚠️ Не удалось отправить фото — отправляю текстом")
                success = send_to_telegram(post_text)
        else:
            success = send_to_telegram(post_text)

        if success:
            save_posted_url(article['link'], article['title'])
            LAST_POST_TIME = datetime.now()
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в publish_next_post: {e}")
        notify_admin(f"Критическая ошибка: {e}")
        return False


def watchdog() -> None:
    """Фоновый поток: если бот не публиковал >24 часов — уведомить."""
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL)
        if LAST_POST_TIME:
            hours_since = (datetime.now() - LAST_POST_TIME).total_seconds() / 3600
            if hours_since > 24:
                logger.warning(f"⚠️ Watchdog: бот не публиковал {hours_since:.1f} часов")
                notify_admin(f"⚠️ Бот не публиковал посты уже {hours_since:.1f} часов. Возможно, завис.")


# ============================================================================
# РАСПИСАНИЕ ПО КОНКРЕТНЫМ ЧАСАМ
# ============================================================================

PENDING_FILE = os.environ.get('PENDING_FILE', 'pending_posts.json')
BREAKING_LOG_FILE = os.environ.get('BREAKING_LOG_FILE', 'breaking_log.json')


def parse_post_times() -> List[str]:
    """Парсить POST_TIMES из .env. Возвращает список ['09:00', '12:00', ...]."""
    if not POST_TIMES:
        return []
    times = []
    for t in POST_TIMES.split(','):
        t = t.strip()
        # Формат HH:MM
        if re.match(r'^\d{1,2}:\d{2}$', t):
            times.append(t)
    return times


def get_today_schedule() -> List[datetime]:
    """Получить список времён публикации на сегодня."""
    today = datetime.now().replace(second=0, microsecond=0)
    times = parse_post_times()
    if not times:
        # Fallback: равные интервалы в рабочие часы
        if not (WORKING_HOURS_START < WORKING_HOURS_END):
            return []
        total_hours = WORKING_HOURS_END - WORKING_HOURS_START
        interval = total_hours / max(POSTS_PER_DAY, 1)
        return [
            today.replace(hour=int(WORKING_HOURS_START + i * interval),
                          minute=int((WORKING_HOURS_START + i * interval) % 1 * 60))
            for i in range(POSTS_PER_DAY)
        ]
    result = []
    for t in times:
        h, m = t.split(':')
        try:
            result.append(today.replace(hour=int(h), minute=int(m)))
        except ValueError:
            continue
    return result


def get_next_scheduled_time() -> Optional[datetime]:
    """Найти следующее время публикации (сегодня или завтра)."""
    now = datetime.now()
    today_times = get_today_schedule()
    for t in today_times:
        if t > now:
            return t
    # Если все сегодняшние прошли — первое на завтра
    if today_times:
        tomorrow_first = today_times[0] + timedelta(days=1)
        return tomorrow_first
    return None


# ============================================================================
# ХРАНИЛИЩЕ ОТЛОЖЕННЫХ ПОСТОВ
# ============================================================================

def load_pending_posts() -> List[Dict]:
    """Загрузить отложенные посты из JSON."""
    path = Path(PENDING_FILE)
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('pending', [])
    except Exception as e:
        logger.warning(f"Не удалось загрузить pending_posts.json: {e}")
        return []


def save_pending_posts(posts: List[Dict]) -> None:
    """Сохранить отложенные посты."""
    try:
        # Не сохраняем image_bytes (слишком большой), только путь
        clean_posts = []
        for p in posts:
            p_copy = dict(p)
            if 'image_bytes' in p_copy:
                del p_copy['image_bytes']
            clean_posts.append(p_copy)
        with open(PENDING_FILE, 'w', encoding='utf-8') as f:
            json.dump({'pending': clean_posts}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить pending_posts.json: {e}")


def load_image_cache(post_id: str) -> Optional[bytes]:
    """Загрузить картинку из кэша."""
    cache_dir = Path('image_cache')
    path = cache_dir / f"{post_id}.jpg"
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        return None


def save_image_cache(post_id: str, image_bytes: bytes) -> None:
    """Сохранить картинку в кэш."""
    cache_dir = Path('image_cache')
    cache_dir.mkdir(exist_ok=True)
    path = cache_dir / f"{post_id}.jpg"
    try:
        path.write_bytes(image_bytes)
    except Exception as e:
        logger.error(f"Не удалось сохранить картинку в кэш: {e}")


# ============================================================================
# ВАЖНЫЕ НОВОСТИ — ЛОГ И ОХЛАЖДЕНИЕ
# ============================================================================

def load_breaking_log() -> Dict:
    """Загрузить лог важных новостей (для охлаждения)."""
    path = Path(BREAKING_LOG_FILE)
    if not path.exists():
        return {'last_time': None, 'last_title': None, 'notified_urls': []}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'last_time': None, 'last_title': None, 'notified_urls': []}


def save_breaking_log(data: Dict) -> None:
    """Сохранить лог важных новостей."""
    try:
        with open(BREAKING_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить breaking_log.json: {e}")


def can_send_breaking(article_url: str) -> bool:
    """Проверить, можно ли слать уведомление (по охлаждению и дубликатам)."""
    log = load_breaking_log()
    # Проверяем cooldown
    if log.get('last_time'):
        try:
            last = datetime.fromisoformat(log['last_time'])
            cooldown_sec = BREAKING_NEWS_COOLDOWN * 60
            if (datetime.now() - last).total_seconds() < cooldown_sec:
                return False
        except Exception:
            pass
    # Проверяем — не уведомляли ли уже об этой статье
    if article_url in log.get('notified_urls', []):
        return False
    return True


def mark_breaking_sent(article: Dict) -> None:
    """Отметить, что об этой статье уже уведомили."""
    log = load_breaking_log()
    log['last_time'] = datetime.now().isoformat()
    log['last_title'] = article.get('title', '')[:100]
    notified = log.get('notified_urls', [])
    notified.append(article.get('link', ''))
    # Храним только последние 50
    log['notified_urls'] = notified[-50:]
    save_breaking_log(log)


# ============================================================================
# КОМАНДЫ АДМИНА (long polling Telegram)
# ============================================================================

# Глобальный список отложенных постов (в памяти, синхронизируется с JSON)
PENDING_POSTS_MEMORY: List[Dict] = []


def send_message_to_admin(text: str, reply_markup: dict = None) -> bool:
    """Отправить сообщение админу (с клавиатурой кнопок)."""
    if not ADMIN_USER_ID or not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': ADMIN_USER_ID,
        'text': text,
        'parse_mode': 'HTML',
    }
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=30, proxies=get_proxies())
        return r.json().get('ok', False)
    except Exception as e:
        logger.error(f"Не удалось отправить админу: {e}")
        return False


def send_photo_to_admin(image_bytes: bytes, caption: str, reply_markup: dict = None) -> bool:
    """Отправить картинку админу."""
    if not ADMIN_USER_ID or not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
    data = {
        'chat_id': ADMIN_USER_ID,
        'caption': caption[:1020],
        'parse_mode': 'HTML',
    }
    if reply_markup:
        data['reply_markup'] = json.dumps(reply_markup)
    try:
        r = requests.post(url, files=files, data=data, timeout=60, proxies=get_proxies())
        return r.json().get('ok', False)
    except Exception as e:
        logger.error(f"Не удалось отправить фото админу: {e}")
        # Пробуем текстом
        return send_message_to_admin(caption, reply_markup)


def get_admin_keyboard() -> dict:
    """Клавиатура для управления постом."""
    return {
        'inline_keyboard': [
            [
                {'text': '✅ Опубликовать сейчас', 'callback_data': 'publish'},
                {'text': '⏭️ Пропустить', 'callback_data': 'skip'},
            ],
            [
                {'text': '🔄 Сгенерировать заново', 'callback_data': 'regenerate'},
                {'text': '📊 Статус', 'callback_data': 'status'},
            ]
        ]
    }


def get_breaking_keyboard() -> dict:
    """Клавиатура для важной новости."""
    return {
        'inline_keyboard': [
            [
                {'text': '🔥 Создать пост сейчас', 'callback_data': 'create_breaking'},
                {'text': '⏭️ Игнорировать', 'callback_data': 'skip_breaking'},
            ]
        ]
    }


def process_admin_commands() -> None:
    """Поток обработки команд админа (long polling)."""
    if not ADMIN_USER_ID:
        logger.warning("ADMIN_USER_ID не задан — команды не обрабатываются")
        return

    offset = 0
    logger.info(f"🎧 Слушаю команды админа (user_id: {ADMIN_USER_ID})")

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'offset': offset, 'timeout': 30}
            r = requests.get(url, params=params, timeout=60, proxies=get_proxies())
            data = r.json()
            if not data.get('ok'):
                logger.error(f"getUpdates error: {data}")
                time.sleep(10)
                continue

            for update in data.get('result', []):
                offset = update['update_id'] + 1

                # Обработка callback (кнопок)
                if 'callback_query' in update:
                    cb = update['callback_query']
                    handle_callback(cb)
                    # Подтверждаем callback
                    ack_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                    requests.post(ack_url, json={'callback_query_id': cb['id']}, timeout=10, proxies=get_proxies())
                    continue

                # Обработка текстовых сообщений
                if 'message' in update:
                    msg = update['message']
                    chat_id = str(msg.get('chat', {}).get('id', ''))
                    # Слушаем только админа
                    if chat_id != str(ADMIN_USER_ID):
                        continue
                    handle_command(msg)

        except Exception as e:
            logger.error(f"Ошибка в process_admin_commands: {e}")
            time.sleep(10)


def handle_command(message: Dict) -> None:
    """Обработать текстовую команду от админа."""
    text = message.get('text', '').strip()
    if not text:
        return

    logger.info(f"📩 Команда от админа: {text[:80]}")

    # Парсим команду и аргументы
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ['/start', '/help']:
        send_message_to_admin(
            "🤖 <b>AI Дайджест — команды</b>\n\n"
            "/status — статус бота\n"
            "/publish — опубликовать пост СЕЙЧАС (мгновенно)\n"
            "/skip — пропустить следующий отложенный пост\n"
            "/regenerate — сгенерировать новый пост взамен следующего\n"
            "/schedule — показать текущее расписание\n"
            "/schedule 8 23 — изменить часы постинга на 8:00-23:00\n"
            "/schedule 09:00,12:00,15:00,18:00,21:00 — конкретные часы\n"
            "/schedule default — вернуть по умолчанию (4 поста в день)\n"
            "/preview — показать текущее время предпросмотра\n"
            "/preview 15 — установить предпросмотр за 15 минут\n"
            "/next — показать когда следующий пост\n"
            "/help — эта справка"
        )
    elif cmd == '/status':
        send_status_to_admin()
    elif cmd == '/publish':
        publish_pending_now()
    elif cmd == '/skip':
        skip_pending()
    elif cmd == '/regenerate':
        regenerate_pending()
    elif cmd == '/schedule':
        if args:
            set_schedule(args)
        else:
            show_schedule()
    elif cmd == '/preview':
        if args:
            set_preview_minutes(args[0])
        else:
            show_preview_settings()
    elif cmd == '/next':
        show_next_post()
    else:
        send_message_to_admin(f"❓ Не знаю команду: {cmd}\nНаберите /help для списка команд")


def update_env_var(key: str, value: str) -> bool:
    """Обновить переменную в .env файле (создаёт если нет)."""
    path = Path('.env')
    if not path.exists():
        path.write_text(f"{key}={value}\n", encoding='utf-8')
        return True
    try:
        content = path.read_text(encoding='utf-8')
        lines = content.split('\n')
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f'{key}='):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            # Добавляем в конец
            if lines and lines[-1].strip():
                lines.append(f"{key}={value}")
            else:
                lines[-1] = f"{key}={value}"
        path.write_text('\n'.join(lines), encoding='utf-8')
        return True
    except Exception as e:
        logger.error(f"Не удалось обновить .env: {e}")
        return False


def set_schedule(args: list) -> None:
    """Изменить расписание постинга через команду /schedule."""
    global WORKING_HOURS_START, WORKING_HOURS_END, POST_TIMES, POSTS_PER_DAY

    if not args:
        show_schedule()
        return

    arg = args[0].lower()

    if arg == 'default':
        # Сбрасываем к дефолту
        POST_TIMES = ''
        WORKING_HOURS_START = 8
        WORKING_HOURS_END = 23
        POSTS_PER_DAY = 4
        update_env_var('POST_TIMES', '')
        update_env_var('WORKING_HOURS_START', '8')
        update_env_var('WORKING_HOURS_END', '23')
        update_env_var('POSTS_PER_DAY', '4')
        send_message_to_admin(
            "✅ <b>Расписание сброшено к умолчанию</b>\n"
            f"Часы: 8:00 - 23:00\n"
            f"Постов в день: 4"
        )
        return

    # Формат "8 23" — часы начала и конца
    if len(args) == 2:
        try:
            start = int(args[0])
            end = int(args[1])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                send_message_to_admin("❌ Часы должны быть 0-23. Пример: /schedule 8 23")
                return
            WORKING_HOURS_START = start
            WORKING_HOURS_END = end
            POST_TIMES = ''  # сбрасываем конкретные времена
            update_env_var('WORKING_HOURS_START', str(start))
            update_env_var('WORKING_HOURS_END', str(end))
            update_env_var('POST_TIMES', '')
            send_message_to_admin(
                f"✅ <b>Расписание изменено!</b>\n"
                f"Часы постинга: {start}:00 - {end}:00\n"
                f"Постов в день: {POSTS_PER_DAY}\n"
                f"Изменения сохранены в .env и применятся после перезапуска."
            )
        except ValueError:
            send_message_to_admin("❌ Неверный формат. Пример: /schedule 8 23")
        return

    # Формат "09:00,12:00,15:00" — конкретные времена
    if ':' in arg:
        # Валидируем
        times_list = [t.strip() for t in arg.split(',') if t.strip()]
        for t in times_list:
            try:
                h, m = t.split(':')
                if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                    raise ValueError()
            except ValueError:
                send_message_to_admin(f"❌ Неверное время: {t}. Пример: /schedule 09:00,12:00,15:00")
                return
        POST_TIMES = arg
        update_env_var('POST_TIMES', arg)
        send_message_to_admin(
            f"✅ <b>Установлены конкретные времена:</b>\n"
            + "\n".join(f"  • {t}" for t in times_list)
            + "\n\nИзменения сохранены и применятся после перезапуска."
        )
        return

    send_message_to_admin(
        "❌ Неверный формат.\n\n"
        "<b>Примеры:</b>\n"
        "/schedule 8 23 — часы 8:00-23:00\n"
        "/schedule 09:00,12:00,15:00 — конкретные времена\n"
        "/schedule default — вернуть по умолчанию"
    )


def set_preview_minutes(minutes_str: str) -> None:
    """Установить время предпросмотра в минутах."""
    global PREVIEW_BEFORE_MINUTES
    try:
        m = int(minutes_str)
        if not (0 <= m <= 1440):
            send_message_to_admin("❌ Минуты должны быть от 0 до 1440 (24 часа)")
            return
        PREVIEW_BEFORE_MINUTES = m
        update_env_var('PREVIEW_BEFORE_MINUTES', str(m))
        # Удаляем старую переменную, если была
        update_env_var('PREVIEW_BEFORE_HOURS', '0')
        if m == 0:
            send_message_to_admin("✅ Предпросмотр отключён (0 минут)")
        else:
            send_message_to_admin(
                f"✅ <b>Предпросмотр за {m} минут</b> до публикации\n"
                f"Изменения сохранены в .env."
            )
    except ValueError:
        send_message_to_admin("❌ Неверный формат. Пример: /preview 15")


def show_preview_settings() -> None:
    """Показать текущие настройки предпросмотра."""
    if not ENABLE_PREVIEW:
        msg = "👁️ <b>Предпросмотр:</b> отключён (ENABLE_PREVIEW=false)"
    elif PREVIEW_BEFORE_MINUTES == 0:
        msg = "👁️ <b>Предпросмотр:</b> отключён (0 минут)"
    else:
        msg = (
            f"👁️ <b>Предпросмотр:</b> за {PREVIEW_BEFORE_MINUTES} минут до публикации\n\n"
            f"Изменить: /preview 15 (за 15 минут)\n"
            f"Отключить: /preview 0"
        )
    send_message_to_admin(msg)


def handle_callback(callback: Dict) -> None:
    """Обработать нажатие кнопки."""
    data = callback.get('data', '')
    msg = callback.get('message', {})
    user = callback.get('from', {})

    # Проверяем, что админ
    if str(user.get('id', '')) != str(ADMIN_USER_ID):
        return

    logger.info(f"🔘 Callback: {data}")

    if data == 'publish':
        publish_pending_now()
    elif data == 'skip':
        skip_pending()
    elif data == 'regenerate':
        regenerate_pending()
    elif data == 'status':
        send_status_to_admin()
    elif data == 'create_breaking':
        # Находим последнюю важную новость и публикуем
        publish_breaking_now()
    elif data == 'skip_breaking':
        send_message_to_admin("👌 Ок, новость проигнорирована")


def send_status_to_admin() -> None:
    """Отправить статус бота админу."""
    pending = load_pending_posts()
    next_time = get_next_scheduled_time()

    msg = "📊 <b>Статус бота</b>\n\n"
    msg += f"🤖 AI: {AI_MODEL}\n"
    msg += f"📡 Канал: {TELEGRAM_CHANNEL_ID}\n"
    msg += f"📅 Расписание: {POST_TIMES or 'по умолчанию'}\n"
    if next_time:
        msg += f"⏰ Следующий пост: {next_time.strftime('%H:%M')}\n"
    msg += f"\n📋 <b>Отложенные посты:</b> {len(pending)}\n"
    for i, p in enumerate(pending[:5], 1):
        target = p.get('target_time', '')
        title = p.get('title', '')[:40]
        msg += f"  {i}. [{target}] {title}\n"
    if len(pending) > 5:
        msg += f"  ... и ещё {len(pending) - 5}\n"

    send_message_to_admin(msg)


def show_schedule() -> None:
    """Показать расписание постов."""
    times = parse_post_times()
    if times:
        msg = f"📅 <b>Расписание публикаций:</b>\n"
        for t in times:
            msg += f"  • {t}\n"
    else:
        msg = (f"📅 <b>Расписание по умолчанию</b>\n"
               f"Постов в день: {POSTS_PER_DAY}\n"
               f"Часы: {WORKING_HOURS_START}:00 - {WORKING_HOURS_END}:00")
    send_message_to_admin(msg)


def show_next_post() -> None:
    """Показать когда следующий пост."""
    next_time = get_next_scheduled_time()
    if next_time:
        delta = next_time - datetime.now()
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        msg = f"⏰ Следующий пост: {next_time.strftime('%H:%M')}\n"
        msg += f"   через {hours}ч {mins}мин"
    else:
        msg = "❌ Не запланировано ни одного поста"
    send_message_to_admin(msg)


def publish_pending_now() -> None:
    """Опубликовать следующий отложенный пост СЕЙЧАС.

    Если отложенных постов нет — мгновенно генерирует новый из свежих RSS.
    """
    global PENDING_POSTS_MEMORY
    pending = load_pending_posts()

    if not pending:
        # Нет отложенных — генерируем новый мгновенно
        send_message_to_admin("⚡️ Нет отложенных постов. <b>Генерирую новый прямо сейчас...</b>")
        articles = fetch_rss_entries()
        ai_articles = [a for a in articles if is_ai_related(a)]
        fresh = filter_fresh_articles(ai_articles, hours=72)
        best = select_best_articles(fresh, limit=1)
        if not best:
            send_message_to_admin("❌ Нет свежих AI-статей для публикации. Попробуйте позже.")
            return
        article = best[0]
        send_message_to_admin(f"📝 Готовлю пост: {article['title'][:60]}")

        post_text = make_summary(article)
        if not post_text:
            send_message_to_admin("❌ Не удалось создать текст поста (AI упал?).")
            return

        image_bytes = get_post_image(article)

        if image_bytes:
            success = send_photo_to_telegram(image_bytes, post_text)
            if not success:
                success = send_to_telegram(post_text)
        else:
            success = send_to_telegram(post_text)

        if success:
            save_posted_url(article['link'], article['title'])
            send_message_to_admin(f"✅ <b>Пост опубликован!</b>\n\n{post_text[:300]}...")
        else:
            send_message_to_admin("❌ Не удалось опубликовать. Проверьте VPN/прокси.")
        return

    # Берём первый отложенный
    post = pending.pop(0)
    save_pending_posts(pending)
    PENDING_POSTS_MEMORY = pending

    text = post.get('text', '')
    image_path = post.get('image_path')
    image_bytes = None
    if image_path:
        post_id = post.get('id', '')
        image_bytes = load_image_cache(post_id)

    send_message_to_admin(f"🚀 <b>Публикую пост:</b>\n\n{text[:200]}...")

    if image_bytes:
        success = send_photo_to_telegram(image_bytes, text)
        if not success:
            success = send_to_telegram(text)
    else:
        success = send_to_telegram(text)

    if success:
        save_posted_url(post.get('link', ''), post.get('title', ''))
        send_message_to_admin("✅ Пост опубликован!")
    else:
        send_message_to_admin("❌ Не удалось опубликовать. Попробуйте позже.")


def skip_pending() -> None:
    """Пропустить следующий отложенный пост."""
    pending = load_pending_posts()
    if not pending:
        send_message_to_admin("📭 Нет отложенных постов для пропуска.")
        return
    post = pending.pop(0)
    save_pending_posts(pending)
    send_message_to_admin(f"⏭️ <b>Пропущен пост:</b>\n{post.get('title', '')[:80]}")


def regenerate_pending() -> None:
    """Сгенерировать новый пост взамен следующего отложенного."""
    pending = load_pending_posts()
    if not pending:
        send_message_to_admin("📭 Нет отложенных постов.")
        return

    old = pending.pop(0)
    save_pending_posts(pending)
    send_message_to_admin(f"🔄 <b>Удаляю:</b> {old.get('title', '')[:60]}\n\nГотовлю новый...")

    # Найдём свежую непубликованную статью
    articles = fetch_rss_entries()
    ai_articles = [a for a in articles if is_ai_related(a)]
    fresh = filter_fresh_articles(ai_articles, hours=72)
    best = select_best_articles(fresh, limit=1)
    if not best:
        send_message_to_admin("❌ Нет свежих статей для нового поста.")
        return
    prepare_pending_post(best[0], datetime.now() + timedelta(minutes=5))
    send_message_to_admin("✅ Новый пост подготовлен и отправлен на предпросмотр!")


def publish_breaking_now() -> None:
    """Опубликовать последнюю важную новость немедленно."""
    articles = fetch_rss_entries()
    ai_articles = [a for a in articles if is_ai_related(a)]
    # Сортируем по значимости
    ai_articles.sort(key=score_news_significance, reverse=True)
    if not ai_articles:
        send_message_to_admin("❌ Нет статей для публикации.")
        return
    best = ai_articles[0]
    score = score_news_significance(best)
    if score < BREAKING_NEWS_SCORE:
        send_message_to_admin(f"ℹ️ Самая значимая статья имеет {score} баллов (нужно {BREAKING_NEWS_SCORE}).")
        return

    send_message_to_admin(f"🔥 <b>Готовлю пост из важной новости:</b>\n{best['title'][:80]}")
    post_text = make_summary(best)
    image_bytes = get_post_image(best)
    if image_bytes:
        success = send_photo_to_telegram(image_bytes, post_text)
        if not success:
            success = send_to_telegram(post_text)
    else:
        success = send_to_telegram(post_text)
    if success:
        save_posted_url(best['link'], best['title'])
        send_message_to_admin("✅ Пост из важной новости опубликован!")
    else:
        send_message_to_admin("❌ Не удалось опубликовать.")


# ============================================================================
# ПОДГОТОВКА И ПРЕДПРОСМОТР ОТЛОЖЕННЫХ ПОСТОВ
# ============================================================================

def prepare_pending_post(article: Dict, target_time: datetime) -> bool:
    """Сгенерировать пост и отправить на предпросмотр админу."""
    try:
        # Генерируем текст
        post_text = make_summary(article)
        if not post_text:
            return False

        # Генерируем картинку
        image_bytes = get_post_image(article)
        post_id = f"{int(time.time())}_{random.randint(1000, 9999)}"
        if image_bytes:
            save_image_cache(post_id, image_bytes)

        # Сохраняем в pending
        post_data = {
            'id': post_id,
            'text': post_text,
            'title': article.get('title', ''),
            'link': article.get('link', ''),
            'target_time': target_time.isoformat(),
            'image_path': f"image_cache/{post_id}.jpg" if image_bytes else None,
            'prepared_at': datetime.now().isoformat(),
        }

        pending = load_pending_posts()
        pending.append(post_data)
        save_pending_posts(pending)

        # Отправляем админу на предпросмотр
        if ENABLE_PREVIEW and ADMIN_USER_ID:
            caption = (
                f"👁️ <b>Предпросмотр поста на {target_time.strftime('%H:%M')}</b>\n\n"
                f"{post_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ Авто-публикация: {target_time.strftime('%H:%M')}\n"
                f"🔔 Можно: опубликовать сейчас, пропустить или сгенерировать заново"
            )
            keyboard = get_admin_keyboard()
            if image_bytes:
                send_photo_to_admin(image_bytes, caption, keyboard)
            else:
                send_message_to_admin(caption, keyboard)

        logger.info(f"✅ Пост подготовлен на {target_time.strftime('%H:%M')}")
        return True
    except Exception as e:
        logger.error(f"Ошибка подготовки поста: {e}")
        return False


def check_and_prepare_pending() -> None:
    """Проверить — нужно ли подготовить новый пост для следующего времени."""
    next_time = get_next_scheduled_time()
    if not next_time:
        return

    now = datetime.now()
    delta = (next_time - now).total_seconds()

    # Готовим пост за PREVIEW_BEFORE_MINUTES минут до публикации
    preview_seconds = PREVIEW_BEFORE_MINUTES * 60
    if delta <= preview_seconds and delta > 0:
        # Проверяем — есть ли уже пост для этого времени
        pending = load_pending_posts()
        for p in pending:
            try:
                p_time = datetime.fromisoformat(p['target_time'])
                if p_time.replace(microsecond=0) == next_time:
                    return  # Уже подготовлен
            except Exception:
                continue

        # Готовим новый
        logger.info(f"📝 Готовлю пост на {next_time.strftime('%H:%M')}")
        articles = fetch_rss_entries()
        ai_articles = [a for a in articles if is_ai_related(a)]
        fresh = filter_fresh_articles(ai_articles, hours=72)
        best = select_best_articles(fresh, limit=1)
        if best:
            prepare_pending_post(best[0], next_time)
        else:
            logger.warning("Нет свежих статей для подготовки поста")


def check_and_publish_pending() -> None:
    """Опубликовать все отложенные посты, чьё время пришло."""
    pending = load_pending_posts()
    if not pending:
        return

    now = datetime.now()
    still_pending = []
    published_count = 0

    for post in pending:
        try:
            target = datetime.fromisoformat(post['target_time'])
        except Exception:
            continue

        if now >= target:
            # Время пришло — публикуем
            text = post.get('text', '')
            post_id = post.get('id', '')
            image_bytes = load_image_cache(post_id) if post.get('image_path') else None

            if image_bytes:
                success = send_photo_to_telegram(image_bytes, text)
                if not success:
                    success = send_to_telegram(text)
            else:
                success = send_to_telegram(text)

            if success:
                save_posted_url(post.get('link', ''), post.get('title', ''))
                published_count += 1
                logger.info(f"✅ Авто-опубликован пост на {target.strftime('%H:%M')}")
            else:
                logger.error(f"❌ Не удалось опубликовать пост на {target.strftime('%H:%M')}")
                # Оставим в очереди для следующей попытки
                still_pending.append(post)
        else:
            still_pending.append(post)

    if published_count > 0:
        save_pending_posts(still_pending)
        if ADMIN_USER_ID:
            notify_admin(f"✅ Авто-опубликовано {published_count} постов по расписанию")


# ============================================================================
# ВАЖНЫЕ НОВОСТИ — ПРОВЕРКА
# ============================================================================

def check_breaking_news() -> None:
    """Проверить RSS на наличие важной новости."""
    if not ENABLE_BREAKING_NEWS or not ADMIN_USER_ID:
        return

    try:
        articles = fetch_rss_entries()
        ai_articles = [a for a in articles if is_ai_related(a)]
        # Сортируем по значимости
        ai_articles.sort(key=score_news_significance, reverse=True)

        if not ai_articles:
            return

        best = ai_articles[0]
        score = score_news_significance(best)

        if score < BREAKING_NEWS_SCORE:
            return  # Слишком слабая новость

        # Проверяем cooldown и дубликаты
        if not can_send_breaking(best.get('link', '')):
            return

        # Отправляем уведомление
        title = best.get('title', '')[:100]
        source = best.get('source', '').split('//')[-1].split('/')[0] if '//' in best.get('source', '') else ''

        caption = (
            f"🔥 <b>ВАЖНАЯ НОВОСТЬ!</b> (score: {score}/100)\n\n"
            f"<b>{title}</b>\n\n"
            f"📡 Источник: {source}\n"
            f"🔗 {best.get('link', '')}\n\n"
            f"Если это действительно важная новость — создайте пост сейчас.\n"
            f"Иначе проигнорируйте (не чаще 1 раза в {BREAKING_NEWS_COOLDOWN // 60}ч)."
        )

        send_message_to_admin(caption, get_breaking_keyboard())
        mark_breaking_sent(best)
        logger.info(f"🔥 Уведомление о важной новости отправлено (score={score})")
    except Exception as e:
        logger.error(f"Ошибка проверки breaking news: {e}")


def main_loop() -> None:
    """Главный цикл с авто-рестартом (НОВОЕ РАСПИСАНИЕ)."""
    global BOT_START_TIME

    logger.info("=" * 60)
    logger.info("🤖 AI Дайджест бот запущен (АВТОНОМНЫЙ РЕЖИМ)")
    logger.info(f"📅 Расписание: {POST_TIMES or f'по умолчанию ({POSTS_PER_DAY} в день)'}")
    logger.info(f"🕘 Часы постинга: {WORKING_HOURS_START}:00 - {WORKING_HOURS_END}:00")
    logger.info(f"👁️ Предпросмотр: за {PREVIEW_BEFORE_MINUTES} мин до публикации")
    logger.info(f"🔥 Важные новости: {'вкл' if ENABLE_BREAKING_NEWS else 'выкл'}, порог={BREAKING_NEWS_SCORE}, кулдаун={BREAKING_NEWS_COOLDOWN}мин")
    logger.info(f"🖼️ Картинки: {'вкл' if ENABLE_IMAGES else 'выкл'}, источник={IMAGE_SOURCE}")
    logger.info(f"📡 Канал: {TELEGRAM_CHANNEL_ID or 'НЕ ЗАДАН!'}")
    logger.info(f"🧠 AI: {AI_BASE_URL} / {AI_MODEL}")
    logger.info(f"🛡️ Админ: {ADMIN_USER_ID or 'не задан'}")
    logger.info(f"🆓 Режим AI: {'отключён' if AI_DISABLED else 'активен'}")
    logger.info("=" * 60)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID!")
        logger.error("   Запустите: python setup.py")
        sys.exit(1)

    # Авто-поиск рабочей AI-модели (на случай если в .env устаревшая)
    if AI_API_KEY:
        find_working_ai_model()

    # Запускаем watchdog
    threading.Thread(target=watchdog, daemon=True).start()

    # Запускаем обработчик команд админа (long polling)
    if ADMIN_USER_ID:
        threading.Thread(target=process_admin_commands, daemon=True).start()

    # Уведомляем админа о старте
    uptime_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_time = get_next_scheduled_time()
    next_time_str = next_time.strftime('%H:%M') if next_time else 'не запланировано'
    # Отправляем сообщение с inline-кнопкой "Опубликовать сейчас"
    keyboard = {
        'inline_keyboard': [[
            {'text': '🚀 Опубликовать сейчас', 'callback_data': 'publish'},
            {'text': '📊 Статус', 'callback_data': 'status'},
        ]]
    }
    send_message_to_admin(
        f"✅ Бот запущен в {uptime_str}\n"
        f"📡 Канал: {TELEGRAM_CHANNEL_ID}\n"
        f"📅 Расписание: {POST_TIMES or f'по умолчанию ({POSTS_PER_DAY} в день, {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00)'}\n"
        f"👁️ Предпросмотр: за {PREVIEW_BEFORE_MINUTES} мин\n"
        f"⏰ Следующий пост: {next_time_str}\n\n"
        f"Нажмите кнопку ниже для мгновенной публикации:",
        reply_markup=keyboard
    )

    failure_count = 0
    last_breaking_check = 0  # timestamp последней проверки важной новости

    while True:
        try:
            # 1. Проверяем — нужно ли подготовить пост на следующее время
            check_and_prepare_pending()

            # 2. Проверяем — нужно ли опубликовать отложенный пост (по расписанию)
            check_and_publish_pending()

            # 3. Проверяем важные новости (раз в 30 минут)
            now_ts = time.time()
            if ENABLE_BREAKING_NEWS and (now_ts - last_breaking_check) > 1800:
                threading.Thread(target=check_breaking_news, daemon=True).start()
                last_breaking_check = now_ts

            # Спим 60 секунд — проверяем каждую минуту
            time.sleep(60)
            failure_count = 0
        except KeyboardInterrupt:
            logger.info("👋 Остановлено")
            notify_admin("👋 Бот остановлен вручную")
            break
        except Exception as e:
            failure_count += 1
            logger.error(f"❌ Сбой в главном цикле ({failure_count}): {e}")
            delay = min(60 * failure_count, MAX_RESTART_DELAY)
            logger.info(f"🔄 Перезапуск через {delay} сек...")
            time.sleep(delay)


# ============================================================================
# WEB-ПАНЕЛЬ СТАТУСА
# ============================================================================

def start_web_server() -> None:
    """HTTP-сервер статуса (для Render и самопроверки)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                posted_count = len(load_posted_urls())
                uptime = str(datetime.now() - BOT_START_TIME).split('.')[0]
                last_post = LAST_POST_TIME.strftime('%H:%M:%S') if LAST_POST_TIME else '—'
                html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🤖 AI Дайджест Бот</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         background: linear-gradient(135deg, #0f1419 0%, #1a2332 100%);
         color: #e6e6e6; min-height: 100vh; padding: 20px; }}
  .container {{ max-width: 600px; margin: 40px auto; }}
  h1 {{ color: #5eb1ff; font-size: 28px; margin-bottom: 8px; }}
  .subtitle {{ color: #888; margin-bottom: 30px; font-size: 14px; }}
  .box {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px;
         margin-bottom: 16px; border: 1px solid rgba(255,255,255,0.1); }}
  .stat {{ display: flex; justify-content: space-between; align-items: center;
          padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  .stat:last-child {{ border-bottom: none; }}
  .stat-label {{ color: #aaa; }}
  .stat-value {{ color: #4dd964; font-weight: 600; }}
  .stat-value.warning {{ color: #ff9800; }}
  .stat-value.error {{ color: #f44336; }}
  .footer {{ text-align: center; color: #555; margin-top: 20px; font-size: 12px; }}
  .pulse {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
           background: #4dd964; margin-right: 8px; animation: pulse 2s infinite; }}
  @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
</style></head>
<body>
<div class="container">
  <h1><span class="pulse"></span>🤖 AI Дайджест Бот</h1>
  <div class="subtitle">Автономный бот-автопостинг в Telegram</div>

  <div class="box">
    <div class="stat"><span class="stat-label">Статус</span>
      <span class="stat-value">✅ Активен</span></div>
    <div class="stat"><span class="stat-label">Uptime</span>
      <span class="stat-value">{uptime}</span></div>
    <div class="stat"><span class="stat-label">Последний пост</span>
      <span class="stat-value">{last_post}</span></div>
    <div class="stat"><span class="stat-label">Опубликовано всего</span>
      <span class="stat-value">{posted_count}</span></div>
  </div>

  <div class="box">
    <div class="stat"><span class="stat-label">Канал</span>
      <span class="stat-value">{TELEGRAM_CHANNEL_ID or '—'}</span></div>
    <div class="stat"><span class="stat-label">Постов в день</span>
      <span class="stat-value">{POSTS_PER_DAY}</span></div>
    <div class="stat"><span class="stat-label">Часы постинга</span>
      <span class="stat-value">{WORKING_HOURS_START}:00 - {WORKING_HOURS_END}:00</span></div>
  </div>

  <div class="box">
    <div class="stat"><span class="stat-label">AI модель</span>
      <span class="stat-value">{AI_MODEL}</span></div>
    <div class="stat"><span class="stat-label">AI статус</span>
      <span class="stat-value {'warning' if AI_DISABLED else ''}">
        {'⚠️ Отключён (fallback)' if AI_DISABLED else '✅ Активен'}</span></div>
    <div class="stat"><span class="stat-label">RSS источников</span>
      <span class="stat-value">{len(RSS_FEEDS)}</span></div>
  </div>

  <div class="footer">
    Текущее время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
    <a href="/health" style="color:#5eb1ff">/health</a>
  </div>
</div>
</body></html>"""
                self.wfile.write(html.encode('utf-8'))
            elif self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'ok',
                    'channel': TELEGRAM_CHANNEL_ID,
                    'posts_today': len(load_posted_urls()),
                    'ai_active': not AI_DISABLED,
                }).encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(('0.0.0.0', PORT), Handler)
    logger.info(f"🌐 Web-панель: http://localhost:{PORT}")
    server.serve_forever()


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == '__main__':
    # Запускаем web-сервер в фоне
    threading.Thread(target=start_web_server, daemon=True).start()
    # Главный цикл
    main_loop()
