#!/usr/bin/env python3
"""
💼 Работа Красноярск — бот вакансий через hh.ru API
====================================================

Изюминка канала:
  💰  "Зарплата дня" — самая высокооплачиваемая вакансия за сутки
  🆕  "Без опыта" — рубрика для начинающих
  🏠  "Удалёнка Крск" — удалённые вакансии для жителей Красноярска
  ⚡  Краткий формат: должность, зарплата, компания, ссылка

Автоматизация:
  • Парсит hh.ru API: https://api.hh.ru/vacancies?area=72 (Красноярск)
  • 8-10 публикаций в день (вакансий много!)
  • AI-форматирование в привлекательный пост
  • Картинки: иконки профессий через AI-генерацию
"""

import os
import json
import time
import logging
import threading
import random
import re
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def load_env_file(env_path: str = '.env') -> None:
    path = Path(env_path)
    if not path.exists():
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                if value and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"⚠️ .env: {e}", file=sys.stderr)

load_env_file()

import feedparser
import requests

# === CONFIG ===
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '')
PROXY_URL = os.environ.get('PROXY_URL', '').strip()
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
IMAGE_SOURCE = os.environ.get('IMAGE_SOURCE', 'generated').lower()
IMAGE_WIDTH = int(os.environ.get('IMAGE_WIDTH', '1024'))
IMAGE_HEIGHT = int(os.environ.get('IMAGE_HEIGHT', '576'))
PREVIEW_BEFORE_MINUTES = int(os.environ.get('PREVIEW_BEFORE_MINUTES', '15'))
AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.groq.com/openai/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'openai/gpt-oss-20b')
POSTS_PER_DAY = int(os.environ.get('POSTS_PER_DAY', '8'))
WORKING_HOURS_START = int(os.environ.get('WORKING_HOURS_START', '8'))
WORKING_HOURS_END = int(os.environ.get('WORKING_HOURS_END', '22'))
POSTED_FILE = os.environ.get('POSTED_FILE', 'posted_urls.json')
LOG_FILE = os.environ.get('LOG_FILE', 'bot.log')
PORT = int(os.environ.get('PORT', '8080'))

# Категории вакансий для разнообразия
VACANCY_CATEGORIES = [
    {'key': 'IT', 'text': 'IT', 'emoji': '💻'},
    {'key': 'SALES', 'text': 'Продажи', 'emoji': '💼'},
    {'key': 'MARKETING', 'text': 'Маркетинг', 'emoji': '📢'},
    {'key': 'LOGISTIC', 'text': 'Логистика', 'emoji': '🚚'},
    {'key': 'FINANCE', 'text': 'Финансы', 'emoji': '💰'},
    {'key': 'HR', 'text': 'HR', 'emoji': '🤝'},
    {'key': 'ENGINEER', 'text': 'Инженеры', 'emoji': '⚙️'},
    {'key': 'ADMIN', 'text': 'Администраторы', 'emoji': '📋'},
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger('krsk_vacancies')

def get_proxies():
    if PROXY_URL:
        return {'http': PROXY_URL, 'https': PROXY_URL}
    return None

def update_env_var(key, value):
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
            if lines and lines[-1].strip():
                lines.append(f"{key}={value}")
            else:
                lines[-1] = f"{key}={value}"
        path.write_text('\n'.join(lines), encoding='utf-8')
        return True
    except Exception as e:
        return False

# === HH.RU API ПАРСИНГ ===

def fetch_hh_vacancies():
    """Получить вакансии через HH.ru API (area=72 — Красноярск)."""
    all_vacancies = []
    # Пробуем разные категории для разнообразия
    categories_to_fetch = random.sample(VACANCY_CATEGORIES, min(3, len(VACANCY_CATEGORIES)))

    for cat in categories_to_fetch:
        try:
            # HH.ru API: area=72 = Красноярск
            url = "https://api.hh.ru/vacancies"
            params = {
                'area': '72',  # Красноярск
                'text': cat['text'],
                'per_page': '10',
                'page': '0',
                'order_by': 'publication_time',
                'search_field': 'name',
                'only_with_salary': 'false',
            }
            headers = {
                'User-Agent': 'KrasnoyarskJobsBot/1.0 (contact: admin@example.com)',
                'Accept': 'application/json',
            }
            r = requests.get(url, params=params, headers=headers, timeout=15, proxies=get_proxies())
            data = r.json()
            for item in data.get('items', []):
                vacancy = {
                    'title': item.get('name', 'Вакансия'),
                    'link': item.get('alternate_url', ''),
                    'employer': item.get('employer', {}).get('name', 'Компания'),
                    'salary': format_salary(item.get('salary')),
                    'type': item.get('type', {}).get('name', ''),
                    'schedule': item.get('schedule', {}).get('name', ''),
                    'snippet': item.get('snippet', {}).get('requirement', '') or item.get('snippet', {}).get('responsibility', ''),
                    'published': item.get('published_at', ''),
                    'category': cat['text'],
                    'category_emoji': cat['emoji'],
                    'image_url': item.get('employer', {}).get('logo_urls', {}).get('240x240') if item.get('employer') else None,
                    'source': 'hh.ru',
                }
                all_vacancies.append(vacancy)
            logger.info(f"📥 {cat['text']}: получено {len(data.get('items', []))} вакансий")
            time.sleep(1)  # вежливость к API
        except Exception as e:
            logger.error(f"HH.ru {cat['text']}: {e}")

    logger.info(f"📦 Всего вакансий: {len(all_vacancies)}")
    return all_vacancies

def format_salary(salary_data):
    """Форматировать зарплату в читаемый вид."""
    if not salary_data:
        return 'не указана'
    fr = salary_data.get('from')
    to = salary_data.get('to')
    cur = salary_data.get('currency', 'RUB')
    gross = salary_data.get('gross', True)
    cur_symbol = {'RUB': '₽', 'USD': '$', 'EUR': '€'}.get(cur, cur)
    if fr and to:
        return f"{fr}-{to} {cur_symbol}"
    elif fr:
        return f"от {fr} {cur_symbol}"
    elif to:
        return f"до {to} {cur_symbol}"
    return 'не указана'

def fetch_rss_entries():
    """Совместимость: возвращаем вакансии как RSS entries."""
    return fetch_hh_vacancies()

# === ФИЛЬТРЫ ===

def ai_relevance_score(article):
    """Все вакансии релевантны — score 10."""
    return 10

def is_ai_related(article):
    return True  # все вакансии релевантны для канала вакансий

def filter_fresh_articles(articles, hours=48):
    """Только свежие вакансии (последние 48 часов)."""
    now = datetime.now()
    fresh = []
    for art in articles:
        published = art.get('published', '')
        if not published:
            continue
        try:
            # HH.ru формат: 2024-01-15T10:30:00+0300
            parsed = datetime.fromisoformat(published.replace('+0300', '+03:00').replace('+0000', '+00:00'))
            age = (now - parsed.replace(tzinfo=None)).total_seconds()
            if age < hours * 3600:
                fresh.append(art)
        except Exception:
            fresh.append(art)
    return fresh

def select_best_articles(articles, limit=5):
    """Выбрать вакансии, исключая уже опубликованные."""
    candidates = []
    for art in articles:
        if not art.get('link') or is_already_posted(art['link']):
            continue
        candidates.append(art)
    random.shuffle(candidates)  # разнообразие
    return candidates[:limit]

# === ХРАНИЛИЩЕ ===

SKIP_NEXT_POST = False

def load_posted_urls():
    path = Path(POSTED_FILE)
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('posted_urls', [])
    except Exception:
        return []

def save_posted_url(url, title=''):
    posted = load_posted_urls()
    posted.append({'url': url, 'title': title, 'time': datetime.now().isoformat()})
    cutoff = datetime.now() - timedelta(days=14)  # вакансии устаревают быстрее
    posted = [p for p in posted if isinstance(p, dict) and p.get('time') and datetime.fromisoformat(p['time']) > cutoff]
    posted = posted[-1000:]
    try:
        with open(POSTED_FILE, 'w', encoding='utf-8') as f:
            json.dump({'posted_urls': posted}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_posted_url: {e}")

def is_already_posted(url):
    posted = load_posted_urls()
    return any((item.get('url') == url if isinstance(item, dict) else item == url) for item in posted)

# === AI ФОРМАТИРОВАНИЕ ВАКАНСИИ ===

AI_FAILURE_COUNT = 0
AI_FAILURE_THRESHOLD = 5
AI_DISABLED = False

def make_summary(article):
    """Превратить вакансию в цепляющий пост."""
    global AI_FAILURE_COUNT, AI_DISABLED
    if not AI_API_KEY or AI_DISABLED:
        return format_without_ai(article)

    title = article.get('title', '')[:200]
    employer = article.get('employer', 'Компания')
    salary = article.get('salary', 'не указана')
    link = article.get('link', '')
    cat_emoji = article.get('category_emoji', '💼')
    cat_name = article.get('category', '')
    schedule = article.get('schedule', '')
    snippet = clean_html(article.get('snippet', ''))[:1000]

    prompt = f"""Ты — HR-маркетолог Telegram-канала "Работа Красноярск" (💼 @rabota_krsk).

Преврати вакансию в цепляющий пост, который ХОЧЕТСЯ открыть и откликнуться.

🌟 МАРКЕТИНГОВЫЕ ПРИНЦИПЫ:
1. **Заголовок с эмодзи** профессии (💻 IT, 🚚 логистика, 📞 продажи, 🍽 общепит, 🏥 медицина)
2. **Должность крупно** в первой строке
3. **Зарплата с акцентом** — если высокая, отметь 🔥 "Зарплата дня"
4. **Компания + локация** — "📍 {employer}, Красноярск"
5. **Краткое описание** — 2-3 строки сути требований
6. **График** — удалёнка/гибрид/офис, удобство
7. **Призыв**: "Откликнуться: [ссылка]" или "Жми на ссылку 👇"
8. Объём: 400-700 символов (коротко = кликабельнее)
9. Хештеги: #РаботаКрасноярск #ВакансииКрск #РаботаКрск

ВАКАНСИЯ:
Должность: {title}
Компания: {employer}
Зарплата: {salary}
График: {schedule}
Категория: {cat_emoji} {cat_name}
Описание: {snippet}
Ссылка: {link}

После поста, на новой строке, добавь:
IMAGE_PROMPT: <английские ключевые слова для картинки, 5-10 слов, например: office work computer professional>

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.7,
                    'max_tokens': 1000,
                },
                timeout=60,
                proxies=get_proxies(),
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            AI_FAILURE_COUNT = 0
            image_prompt = ''
            img_match = re.search(r'IMAGE_PROMPT:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if img_match:
                image_prompt = img_match.group(1).strip()
                content = re.sub(r'IMAGE_PROMPT:.*?(?:\n|$)', '', content, flags=re.IGNORECASE).strip()
            article['_image_prompt'] = image_prompt
            if link and link not in content:
                content += f"\n\n🔗 {link}"
            return content
        except Exception as e:
            logger.warning(f"AI {attempt+1}/3: {e}")
            time.sleep(2 * (attempt + 1))
    AI_FAILURE_COUNT += 1
    if AI_FAILURE_COUNT >= AI_FAILURE_THRESHOLD:
        AI_DISABLED = True
    return format_without_ai(article)

def clean_html(text):
    return re.sub(r'<[^>]+>', '', text)

def format_without_ai(article):
    title = article.get('title', '')[:200]
    employer = article.get('employer', 'Компания')
    salary = article.get('salary', 'не указана')
    link = article.get('link', '')
    cat_emoji = article.get('category_emoji', '💼')
    return f"{cat_emoji} {title}\n\n🏢 {employer}\n💰 {salary}\n📍 Красноярск\n\n🔗 {link}\n\n#РаботаКрасноярск #ВакансииКрск"

# === КАРТИНКИ ===

def generate_image_url(prompt, seed=None):
    """DEPRECATED — pollinations.ai перестал быть бесплатным (402 Payment Required).
    Возвращает None — генерация отключена."""
    return None

def download_image_bytes(url, timeout=90):
    try:
        r = requests.get(url, timeout=timeout, proxies=get_proxies())
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.error(f"Download: {e}")
        return None

def get_post_image(article):
    """Для вакансий картинки обычно нет в источнике, и генерация отключена.
    Пробуем взять из image_url (если вдруг есть), иначе возвращаем None.
    """
    if not ENABLE_IMAGES:
        return None
    if article.get('image_url'):
        img = download_image_bytes(article['image_url'])
        if img:
            return img
    return None

# === TELEGRAM ===

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False
    if len(text) > 4096:
        text = text[:4090] + '...'
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'text': text, 'parse_mode': 'HTML'}
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30, proxies=get_proxies())
            data = r.json()
            if data.get('ok'):
                return True
            if 'parse' in str(data.get('description', '')).lower():
                payload['parse_mode'] = ''
                continue
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            logger.error(f"send {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    return False

def send_photo_to_telegram(image_bytes, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False
    caption = caption[:1020] + '...' if len(caption) > 1024 else caption
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
    data = {'chat_id': TELEGRAM_CHANNEL_ID, 'caption': caption, 'parse_mode': 'HTML'}
    for attempt in range(3):
        try:
            r = requests.post(url, files=files, data=data, timeout=90, proxies=get_proxies())
            result = r.json()
            if result.get('ok'):
                return True
            if 'parse' in str(result.get('description', '')).lower():
                data['parse_mode'] = ''
                files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
                continue
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            logger.error(f"send_photo {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    return False

def send_message_to_admin(text, reply_markup=None):
    if not ADMIN_USER_ID or not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': ADMIN_USER_ID, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=30, proxies=get_proxies())
        return r.json().get('ok', False)
    except Exception as e:
        logger.error(f"admin msg: {e}")
        return False

# === АДМИН КОМАНДЫ ===

def process_admin_commands():
    if not ADMIN_USER_ID:
        return
    offset = 0
    logger.info(f"🎧 Слушаю админа ({ADMIN_USER_ID})")
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {'offset': offset, 'timeout': 30}
            r = requests.get(url, params=params, timeout=60, proxies=get_proxies())
            data = r.json()
            if not data.get('ok'):
                time.sleep(10)
                continue
            for update in data.get('result', []):
                offset = update['update_id'] + 1
                if 'callback_query' in update:
                    handle_callback(update['callback_query'])
                    ack = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                    requests.post(ack, json={'callback_query_id': update['callback_query']['id']}, timeout=10, proxies=get_proxies())
                    continue
                if 'message' in update:
                    msg = update['message']
                    if str(msg.get('chat', {}).get('id', '')) == str(ADMIN_USER_ID):
                        handle_command(msg)
        except Exception as e:
            logger.error(f"admin commands: {e}")
            time.sleep(10)

def handle_command(message):
    global SKIP_NEXT_POST
    text = (message.get('text') or '').strip()
    if not text:
        return
    logger.info(f"📩 Команда: {text[:50]}")
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ['/start', '/help']:
        send_message_to_admin(
            "💼 <b>Работа Красноярск — команды</b>\n\n"
            "/status — статус\n/publish — вакансия СЕЙЧАС\n/skip — пропустить\n"
            "/schedule 8 22 — изменить часы\n/preview 15 — предпросмотр\n"
            "/next — след. вакансия\n/help — справка"
        )
    elif cmd == '/status':
        posted = len(load_posted_urls())
        send_message_to_admin(f"📊 <b>Статус</b>\n📡 Канал: {TELEGRAM_CHANNEL_ID}\n📨 Вакансий опубликовано: {posted}\n🧠 AI: {AI_MODEL} ({'✅' if not AI_DISABLED else '⚠️ fallback'})\n📅 {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00")
    elif cmd == '/publish':
        publish_next_post(force=True)
    elif cmd == '/skip':
        SKIP_NEXT_POST = True
        send_message_to_admin("⏭️ Пропущено")
    elif cmd == '/schedule':
        if len(args) == 2:
            try:
                s, e = int(args[0]), int(args[1])
                update_env_var('WORKING_HOURS_START', str(s))
                update_env_var('WORKING_HOURS_END', str(e))
                send_message_to_admin(f"✅ {s}:00-{e}:00 (после рестарта)")
            except ValueError:
                send_message_to_admin("❌ /schedule 8 22")
        else:
            send_message_to_admin(f"📅 {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00")
    elif cmd == '/preview':
        if args:
            try:
                m = int(args[0])
                update_env_var('PREVIEW_BEFORE_MINUTES', str(m))
                send_message_to_admin(f"✅ {m} мин")
            except ValueError:
                send_message_to_admin("❌ /preview 15")
        else:
            send_message_to_admin(f"👁️ {PREVIEW_BEFORE_MINUTES} мин")
    elif cmd == '/next':
        articles = fetch_hh_vacancies()
        fresh = filter_fresh_articles(articles, hours=48)
        best = select_best_articles(fresh, limit=1)
        if best:
            send_message_to_admin(f"📋 <b>Следующая:</b>\n💼 {best[0]['title'][:150]}\n💰 {best[0].get('salary', '')}")
        else:
            send_message_to_admin("📭 Нет свежих вакансий")
    else:
        send_message_to_admin(f"❓ {cmd} — /help")

def handle_callback(callback):
    data = callback.get('data', '')
    logger.info(f"🔘 Callback: {data}")
    if data == 'publish':
        publish_next_post(force=True)
    elif data == 'status':
        handle_command({'text': '/status'})
    elif data == 'skip':
        global SKIP_NEXT_POST
        SKIP_NEXT_POST = True

# === ГЛАВНАЯ ЛОГИКА ===

LAST_POST_TIME = None
BOT_START_TIME = datetime.now()

def is_working_hours():
    hour = datetime.now().hour
    return WORKING_HOURS_START <= hour < WORKING_HOURS_END

def calculate_next_interval():
    hour = datetime.now().hour
    base = (24 * 60) // max(POSTS_PER_DAY, 1)
    # Пиковые часы для просмотра вакансий: 9, 13, 17, 19
    if hour in [9, 13, 17, 19, 21]:
        base = base // 2
    return max(30, min(180, int(base * random.uniform(0.7, 1.3))))

def publish_next_post(force=False):
    global LAST_POST_TIME, SKIP_NEXT_POST
    if SKIP_NEXT_POST and not force:
        SKIP_NEXT_POST = False
        return False
    try:
        articles = fetch_hh_vacancies()
        if not articles:
            return False
        fresh = filter_fresh_articles(articles, hours=48)
        best = select_best_articles(fresh, limit=1)
        if not best:
            logger.info("🎉 Все вакансии опубликованы")
            return False
        article = best[0]
        logger.info(f"📝 Публикуем: {article['title'][:80]}")
        text = make_summary(article)
        if not text:
            return False
        image_bytes = get_post_image(article)
        if image_bytes:
            success = send_photo_to_telegram(image_bytes, text)
            if not success:
                success = send_to_telegram(text)
        else:
            success = send_to_telegram(text)
        if success:
            save_posted_url(article['link'], article['title'])
            LAST_POST_TIME = datetime.now()
            return True
        return False
    except Exception as e:
        logger.error(f"publish: {e}")
        return False

def watchdog():
    while True:
        time.sleep(3600)
        if LAST_POST_TIME:
            hours = (datetime.now() - LAST_POST_TIME).total_seconds() / 3600
            if hours > 24:
                send_message_to_admin(f"⚠️ Бот не публиковал {hours:.1f}ч!")

FALLBACK_MODELS = ['openai/gpt-oss-20b', 'openai/gpt-oss-120b', 'llama-3.1-8b-instant', 'qwen/qwen3.8-27b']

def test_ai_model(model):
    global AI_MODEL
    try:
        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
            json={'model': model, 'messages': [{'role': 'user', 'content': 'OK'}], 'max_tokens': 5},
            timeout=15, proxies=get_proxies(),
        )
        if 'choices' in r.json():
            logger.info(f"✅ Модель {model} работает")
            return True
    except Exception as e:
        logger.warning(f"⚠️ {model}: {e}")
    for m in FALLBACK_MODELS:
        if m == model:
            continue
        try:
            r = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={'model': m, 'messages': [{'role': 'user', 'content': 'OK'}], 'max_tokens': 5},
                timeout=15, proxies=get_proxies(),
            )
            if 'choices' in r.json():
                AI_MODEL = m
                logger.info(f"✅ Найдена: {m}")
                return True
        except Exception:
            pass
    return False

def main_loop():
    global BOT_START_TIME
    logger.info("=" * 60)
    logger.info("💼 Работа Красноярск — бот запущен")
    logger.info(f"📅 Вакансий в день: {POSTS_PER_DAY}")
    logger.info(f"🕘 Часы: {WORKING_HOURS_START}:00 - {WORKING_HOURS_END}:00")
    logger.info(f"📡 Канал: {TELEGRAM_CHANNEL_ID or 'НЕ ЗАДАН!'}")
    logger.info(f"🧠 AI: {AI_BASE_URL} / {AI_MODEL}")
    logger.info(f"🔍 Источник: hh.ru API (area=72)")
    logger.info("=" * 60)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        sys.exit(1)
    if AI_API_KEY:
        test_ai_model(AI_MODEL)
    threading.Thread(target=watchdog, daemon=True).start()
    if ADMIN_USER_ID:
        threading.Thread(target=process_admin_commands, daemon=True).start()
        keyboard = {'inline_keyboard': [[
            {'text': '🚀 Опубликовать сейчас', 'callback_data': 'publish'},
            {'text': '📊 Статус', 'callback_data': 'status'},
        ]]}
        send_message_to_admin(
            f"✅ Бот запущен в {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📡 Канал: {TELEGRAM_CHANNEL_ID}\n"
            f"🔍 Источник: hh.ru API\n"
            f"📅 {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00, {POSTS_PER_DAY}/день\n\n"
            f"Нажмите для публикации:",
            reply_markup=keyboard
        )
    while True:
        try:
            if is_working_hours():
                publish_next_post()
            interval = calculate_next_interval()
            logger.info(f"⏰ Следующий через ~{interval} мин")
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ Main: {e}")
            time.sleep(300)

def start_web_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                posted = len(load_posted_urls())
                uptime = str(datetime.now() - BOT_START_TIME).split('.')[0]
                last = LAST_POST_TIME.strftime('%H:%M:%S') if LAST_POST_TIME else '—'
                html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Работа Красноярск</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #1a1f3a 0%, #2a3a5a 100%); color: #fff; padding: 20px; }}
.box {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; margin: 16px auto; max-width: 500px; border: 1px solid rgba(255,165,0,0.2); }}
h1 {{ color: #ffa500; text-align: center; }}
.stat {{ display: flex; justify-content: space-between; padding: 8px 0; }}
</style></head><body>
<h1>💼 Работа Красноярск</h1>
<div class="box">
  <div class="stat"><span>Статус</span><span style="color:#4dd964">✅ Активен</span></div>
  <div class="stat"><span>Uptime</span><span>{uptime}</span></div>
  <div class="stat"><span>Последняя вакансия</span><span>{last}</span></div>
  <div class="stat"><span>Опубликовано</span><span>{posted}</span></div>
</div>
<div class="box">
  <div class="stat"><span>Источник</span><span>hh.ru API</span></div>
  <div class="stat"><span>Расписание</span><span>{WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00</span></div>
  <div class="stat"><span>Вакансий в день</span><span>{POSTS_PER_DAY}</span></div>
</div>
</body></html>"""
                self.wfile.write(html.encode('utf-8'))
            elif self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, *args): pass
    server = HTTPServer(('0.0.0.0', PORT), H)
    logger.info(f"🌐 Web-панель: http://localhost:{PORT}")
    server.serve_forever()

if __name__ == '__main__':
    threading.Thread(target=start_web_server, daemon=True).start()
    main_loop()
