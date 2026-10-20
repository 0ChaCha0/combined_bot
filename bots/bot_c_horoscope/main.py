#!/usr/bin/env python3
"""
✨ Звёзды Говорят — бот ежедневных гороскопов
=============================================

Изюминка канала:
  🌅  Утренний гороскоп на день для каждого знака
  💫  "Цвет дня" — рекомендуемый цвет для привлечения удачи
  🍀  "Число удачи" — конкретное число для каждого знака
  ❤️  "Любовь дня" — отдельный пост про отношения
  🌙  Вечерний "Итог дня" — что было важно

Автоматизация:
  • AI генерирует гороскопы на основе текущей даты + фаз луны
  • 2-3 поста в день (утро — общий, день — любовь, вечер — итог)
  • 12 знаков зодиака с уникальными предсказаниями
  • Картинки — мистические изображения через AI
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

import requests

# === CONFIG ===
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '')
PROXY_URL = os.environ.get('PROXY_URL', '').strip()
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
IMAGE_WIDTH = int(os.environ.get('IMAGE_WIDTH', '1024'))
IMAGE_HEIGHT = int(os.environ.get('IMAGE_HEIGHT', '576'))
AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.groq.com/openai/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'openai/gpt-oss-20b')
POSTS_PER_DAY = int(os.environ.get('POSTS_PER_DAY', '3'))  # утро, день, вечер
POSTED_FILE = os.environ.get('POSTED_FILE', 'posted_horoscopes.json')
LOG_FILE = os.environ.get('LOG_FILE', 'bot.log')
PORT = int(os.environ.get('PORT', '8080'))

# Знаки зодиака
ZODIAC_SIGNS = [
    {'name': 'Овен', 'emoji': '♈', 'date_range': '21 марта - 20 апреля', 'element': 'fire', 'ruler': 'Марс'},
    {'name': 'Телец', 'emoji': '♉', 'date_range': '21 апреля - 21 мая', 'element': 'earth', 'ruler': 'Венера'},
    {'name': 'Близнецы', 'emoji': '♊', 'date_range': '22 мая - 21 июня', 'element': 'air', 'ruler': 'Меркурий'},
    {'name': 'Рак', 'emoji': '♋', 'date_range': '22 июня - 22 июля', 'element': 'water', 'ruler': 'Луна'},
    {'name': 'Лев', 'emoji': '♌', 'date_range': '23 июля - 21 августа', 'element': 'fire', 'ruler': 'Солнце'},
    {'name': 'Дева', 'emoji': '♍', 'date_range': '22 августа - 23 сентября', 'element': 'earth', 'ruler': 'Меркурий'},
    {'name': 'Весы', 'emoji': '♎', 'date_range': '24 сентября - 23 октября', 'element': 'air', 'ruler': 'Венера'},
    {'name': 'Скорпион', 'emoji': '♏', 'date_range': '24 октября - 22 ноября', 'element': 'water', 'ruler': 'Плутон'},
    {'name': 'Стрелец', 'emoji': '♐', 'date_range': '23 ноября - 21 декабря', 'element': 'fire', 'ruler': 'Юпитер'},
    {'name': 'Козерог', 'emoji': '♑', 'date_range': '22 декабря - 20 января', 'element': 'earth', 'ruler': 'Сатурн'},
    {'name': 'Водолей', 'emoji': '♒', 'date_range': '21 января - 19 февраля', 'element': 'air', 'ruler': 'Уран'},
    {'name': 'Рыбы', 'emoji': '♓', 'date_range': '20 февраля - 20 марта', 'element': 'water', 'ruler': 'Нептун'},
]

# Цвета для "цвета дня"
COLORS_OF_DAY = [
    ('красный', 'red, passion, fire'), ('оранжевый', 'orange, sunset, warm'),
    ('жёлтый', 'yellow, sunlight, joy'), ('зелёный', 'green, nature, growth'),
    ('голубой', 'light blue, sky, calm'), ('синий', 'blue, ocean, deep'),
    ('фиолетовый', 'purple, mystic, royal'), ('розовый', 'pink, love, gentle'),
    ('золотой', 'gold, luxury, shine'), ('серебряный', 'silver, moonlight, elegant'),
    ('белый', 'white, pure, clean'), ('бордовый', 'burgundy, deep, sophisticated'),
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger('horoscope_bot')

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

# === ЛУННЫЕ ФАЗЫ ===

def get_moon_phase():
    """Простая аппроксимация фазы луны по дате."""
    now = datetime.now()
    # Используем известную новолуние 2000-01-06 18:14 UTC как точку отсчёта
    new_moon = datetime(2000, 1, 6, 18, 14)
    synodic_month = 29.53058867  # дней
    days_since = (now - new_moon).total_seconds() / 86400
    phase = (days_since % synodic_month) / synodic_month
    if phase < 0.03 or phase > 0.97:
        return ('Новолуние', '🌑')
    elif phase < 0.22:
        return ('Растущая Луна', '🌒')
    elif phase < 0.28:
        return ('Первая четверть', '🌓')
    elif phase < 0.47:
        return ('Растущая Луна', '🌔')
    elif phase < 0.53:
        return ('Полнолуние', '🌕')
    elif phase < 0.72:
        return ('Убывающая Луна', '🌖')
    elif phase < 0.78:
        return ('Последняя четверть', '🌗')
    else:
        return ('Убывающая Луна', '🌘')

# === ХРАНИЛИЩЕ ===

def load_posted():
    path = Path(POSTED_FILE)
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('posted', [])
    except Exception:
        return []

def save_posted(post_type, content_hash):
    posted = load_posted()
    posted.append({'type': post_type, 'hash': content_hash, 'time': datetime.now().isoformat(), 'date': datetime.now().strftime('%Y-%m-%d')})
    cutoff = datetime.now() - timedelta(days=30)
    posted = [p for p in posted if p.get('time') and datetime.fromisoformat(p['time']) > cutoff]
    posted = posted[-1000:]
    try:
        with open(POSTED_FILE, 'w', encoding='utf-8') as f:
            json.dump({'posted': posted}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_posted: {e}")

def is_already_posted_today(post_type):
    """Проверить, был ли уже пост такого типа сегодня."""
    today = datetime.now().strftime('%Y-%m-%d')
    posted = load_posted()
    return any(p.get('type') == post_type and p.get('date') == today for p in posted)

# === AI ГЕНЕРАЦИЯ ГОРОСКОПОВ ===

AI_FAILURE_COUNT = 0
AI_DISABLED = False

def generate_morning_horoscope():
    """Утренний гороскоп на день для всех 12 знаков."""
    global AI_FAILURE_COUNT, AI_DISABLED
    if not AI_API_KEY or AI_DISABLED:
        return format_fallback_horoscope('morning')

    today = datetime.now().strftime('%d %B %Y')
    moon_phase, moon_emoji = get_moon_phase()
    lucky_color_ru, lucky_color_en = random.choice(COLORS_OF_DAY)
    lucky_number = random.randint(1, 99)

    # Выбираем 3 случайных знака для поста (чтобы не делать огромный пост)
    signs_today = random.sample(ZODIAC_SIGNS, 3)

    prompt = f"""Ты — астролог Telegram-канала "Звёзды Говорят" (✨ @zvezdy_govoryat).

Сегодня: {today}
Фаза Луны: {moon_phase} {moon_emoji}
Цвет дня: {lucky_color_ru}
Число удачи: {lucky_number}

Сделай утренний гороскоп для 3 знаков зодиака: {", ".join(s['name'] for s in signs_today)}.

🌟 ФОРМАТ ПОСТА:
1. Заголовок: "🌅 Утренний гороскоп на {today}"
2. Фаза луны: "{moon_emoji} {moon_phase}"
3. Цвет дня: "🎨 {lucky_color_ru}" — рекомендуй надеть что-то с этим цветом
4. Число удачи: "🍀 {lucky_number}"
5. Для каждого знака:
   - Эмодзи + название
   - Краткий прогноз (2-3 строки): карьера, любовь, здоровье
   - Совет дня (1 строка)
6. В конце: "Поделитесь с друзьями, кому сегодня повезёт ✨"
7. Хештеги: #Гороскоп #Астрология #ЗвёздыГоворят

Объём: 800-1200 символов.

После поста, на новой строке, добавь:
IMAGE_PROMPT: mystic astrology background with zodiac symbols, moon, stars, deep night sky, gold accents, ethereal

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.9,  # максимум креатива
                    'max_tokens': 2000,
                },
                timeout=60,
                proxies=get_proxies(),
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            AI_FAILURE_COUNT = 0
            # Убираем IMAGE_PROMPT из поста
            image_prompt = 'mystic astrology background, zodiac wheel, gold and deep blue, ethereal'
            img_match = re.search(r'IMAGE_PROMPT:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if img_match:
                image_prompt = img_match.group(1).strip()
                content = re.sub(r'IMAGE_PROMPT:.*?(?:\n|$)', '', content, flags=re.IGNORECASE).strip()
            return content, image_prompt
        except Exception as e:
            logger.warning(f"AI {attempt+1}/3: {e}")
            time.sleep(2 * (attempt + 1))
    AI_FAILURE_COUNT += 1
    if AI_FAILURE_COUNT >= 5:
        AI_DISABLED = True
    return format_fallback_horoscope('morning'), 'mystic astrology background, zodiac, gold'

def generate_love_horoscope():
    """Гороскоп любви."""
    global AI_FAILURE_COUNT, AI_DISABLED
    if not AI_API_KEY or AI_DISABLED:
        return format_fallback_horoscope('love'), 'love heart mystic background'

    today = datetime.now().strftime('%d %B %Y')
    signs_today = random.sample(ZODIAC_SIGNS, 3)

    prompt = f"""Ты — астролог канала "Звёзды Говорят" (✨ @zvezdy_govoryat).

Сегодня: {today}

Сделай гороскоп ЛЮБВИ для 3 знаков: {", ".join(s['name'] for s in signs_today)}.

❤️ ФОРМАТ:
1. Заголовок: "❤️ Гороскоп любви на {today}"
2. Для каждого знака:
   - Эмодзи + название
   - Любовный прогноз (2-3 строки)
   - Идеальный знак для встречи сегодня
3. Совет дня для всех: что делать, чтобы привлечь любовь
4. Хештеги: #ГороскопЛюбви #Астрология #Любовь

Объём: 600-900 символов.

IMAGE_PROMPT: romantic heart zodiac, mystic love, pink and gold accents, ethereal

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.9,
                    'max_tokens': 1500,
                },
                timeout=60,
                proxies=get_proxies(),
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            AI_FAILURE_COUNT = 0
            image_prompt = 'romantic heart zodiac, pink and gold'
            img_match = re.search(r'IMAGE_PROMPT:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if img_match:
                image_prompt = img_match.group(1).strip()
                content = re.sub(r'IMAGE_PROMPT:.*?(?:\n|$)', '', content, flags=re.IGNORECASE).strip()
            return content, image_prompt
        except Exception as e:
            logger.warning(f"AI {attempt+1}/3: {e}")
            time.sleep(2 * (attempt + 1))
    AI_FAILURE_COUNT += 1
    return format_fallback_horoscope('love'), 'romantic heart zodiac'

def generate_evening_summary():
    """Вечерний итог дня."""
    global AI_FAILURE_COUNT, AI_DISABLED
    if not AI_API_KEY or AI_DISABLED:
        return format_fallback_horoscope('evening'), 'evening stars mystic'

    today = datetime.now().strftime('%d %B %Y')
    moon_phase, moon_emoji = get_moon_phase()

    prompt = f"""Ты — астролог канала "Звёзды Говорят" (✨ @zvezdy_govoryat).

Сегодня: {today}
Фаза Луны: {moon_phase} {moon_emoji}

Сделай вечерний астрологический итог дня.

🌙 ФОРМАТ:
1. Заголовок: "🌙 Вечерний итог дня. {today}"
2. Что важного принесли звёзды сегодня (2-3 строки)
3. Советы на завтра для всех знаков
4. Луна сегодня: "{moon_emoji} {moon_phase}" — что это значит
5. Пожелание спокойной ночи
6. Хештеги: #ГороскопВечер #Астрология #СпокойнойНочи

Объём: 500-800 символов.

IMAGE_PROMPT: night sky stars moon, peaceful, deep blue and gold, dreamy ethereal

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.85,
                    'max_tokens': 1200,
                },
                timeout=60,
                proxies=get_proxies(),
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            AI_FAILURE_COUNT = 0
            image_prompt = 'night sky stars moon, peaceful blue'
            img_match = re.search(r'IMAGE_PROMPT:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if img_match:
                image_prompt = img_match.group(1).strip()
                content = re.sub(r'IMAGE_PROMPT:.*?(?:\n|$)', '', content, flags=re.IGNORECASE).strip()
            return content, image_prompt
        except Exception as e:
            logger.warning(f"AI {attempt+1}/3: {e}")
            time.sleep(2 * (attempt + 1))
    AI_FAILURE_COUNT += 1
    return format_fallback_horoscope('evening'), 'night sky stars'

def format_fallback_horoscope(post_type):
    """Если AI недоступен — простой шаблон."""
    today = datetime.now().strftime('%d %B %Y')
    signs = random.sample(ZODIAC_SIGNS, 3)
    moon_phase, moon_emoji = get_moon_phase()
    lucky_color, _ = random.choice(COLORS_OF_DAY)
    lucky_number = random.randint(1, 99)

    if post_type == 'morning':
        text = f"🌅 Утренний гороскоп на {today}\n\n{moon_emoji} {moon_phase}\n🎨 Цвет дня: {lucky_color}\n🍀 Число удачи: {lucky_number}\n\n"
        for s in signs:
            text += f"{s['emoji']} {s['name']}\nСегодня благоприятный день для новых начинаний. Слушайте интуицию.\n\n"
        text += "\nПоделитесь с друзьями ✨\n#Гороскоп #Астрология"
    elif post_type == 'love':
        text = f"❤️ Гороскоп любви на {today}\n\n"
        for s in signs:
            text += f"{s['emoji']} {s['name']}\nСегодня возможна встреча с тем, кто изменит вашу жизнь. Будьте открыты.\n\n"
        text += "\n#ГороскопЛюбви #Любовь"
    else:  # evening
        text = f"🌙 Вечерний итог дня. {today}\n\n{moon_emoji} {moon_phase}\n\nСегодня звёзды были благосклонны. Завтра — новый шанс.\n\nСпокойной ночи ✨\n#СпокойнойНочи"
    return text

# === КАРТИНКИ ===

def generate_image_url(prompt, seed=None):
    """DEPRECATED — pollinations.ai перестал быть бесплатным (402 Payment Required).
    Возвращает None — генерация отключена."""
    return None

def download_image_bytes(url, timeout=120):
    try:
        r = requests.get(url, timeout=timeout, proxies=get_proxies())
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.error(f"Download: {e}")
        return None

def get_post_image(image_prompt):
    """Гороскопы не имеют источника картинок — генерация отключена, всегда None.
    Бот будет постить только текст гороскопа.
    """
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
    text = (message.get('text') or '').strip()
    if not text:
        return
    logger.info(f"📩 Команда: {text[:50]}")
    cmd = text.split()[0].lower()

    if cmd in ['/start', '/help']:
        send_message_to_admin(
            "✨ <b>Звёзды Говорят — команды</b>\n\n"
            "/status — статус\n/publish morning — утренний гороскоп\n"
            "/publish love — гороскоп любви\n/publish evening — вечерний итог\n"
            "/help — справка"
        )
    elif cmd == '/status':
        posted = len(load_posted())
        send_message_to_admin(f"📊 <b>Статус</b>\n📡 Канал: {TELEGRAM_CHANNEL_ID}\n📨 Гороскопов опубликовано: {posted}\n🧠 AI: {AI_MODEL} ({'✅' if not AI_DISABLED else '⚠️ fallback'})")
    elif cmd == '/publish':
        args = text.split()[1:] if len(text.split()) > 1 else ['morning']
        post_type = args[0] if args else 'morning'
        publish_horoscope(post_type, force=True)
    else:
        send_message_to_admin(f"❓ {cmd} — /help")

def handle_callback(callback):
    data = callback.get('data', '')
    logger.info(f"🔘 Callback: {data}")
    if data.startswith('publish_'):
        post_type = data.replace('publish_', '')
        publish_horoscope(post_type, force=True)
    elif data == 'status':
        handle_command({'text': '/status'})

# === ГЛАВНАЯ ЛОГИКА ===

LAST_POST_TIME = None
BOT_START_TIME = datetime.now()

def publish_horoscope(post_type='morning', force=False):
    """Опубликовать гороскоп указанного типа."""
    global LAST_POST_TIME
    if not force and is_already_posted_today(post_type):
        logger.info(f"ℹ️ {post_type} уже опубликован сегодня")
        return False
    try:
        if post_type == 'morning':
            text, image_prompt = generate_morning_horoscope()
        elif post_type == 'love':
            text, image_prompt = generate_love_horoscope()
        elif post_type == 'evening':
            text, image_prompt = generate_evening_summary()
        else:
            return False
        if not text:
            return False
        image_bytes = get_post_image(image_prompt) if ENABLE_IMAGES else None
        if image_bytes:
            success = send_photo_to_telegram(image_bytes, text)
            if not success:
                success = send_to_telegram(text)
        else:
            success = send_to_telegram(text)
        if success:
            content_hash = hash(text)
            save_posted(post_type, content_hash)
            LAST_POST_TIME = datetime.now()
            return True
        return False
    except Exception as e:
        logger.error(f"publish_horoscope: {e}")
        return False

def main_loop():
    global BOT_START_TIME
    logger.info("=" * 60)
    logger.info("✨ Звёзды Говорят — бот запущен")
    logger.info(f"📅 Гороскопов в день: {POSTS_PER_DAY}")
    logger.info(f"📡 Канал: {TELEGRAM_CHANNEL_ID or 'НЕ ЗАДАН!'}")
    logger.info(f"🧠 AI: {AI_BASE_URL} / {AI_MODEL}")
    logger.info("=" * 60)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("❌ Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        sys.exit(1)
    if AI_API_KEY:
        test_ai_model(AI_MODEL)
    if ADMIN_USER_ID:
        threading.Thread(target=process_admin_commands, daemon=True).start()
        keyboard = {'inline_keyboard': [[
            {'text': '🌅 Утро', 'callback_data': 'publish_morning'},
            {'text': '❤️ Любовь', 'callback_data': 'publish_love'},
            {'text': '🌙 Вечер', 'callback_data': 'publish_evening'},
        ]]}
        send_message_to_admin(
            f"✅ Бот запущен в {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📡 Канал: {TELEGRAM_CHANNEL_ID}\n\n"
            f"Выберите тип гороскопа для публикации:",
            reply_markup=keyboard
        )
    # Расписание публикации гороскопов
    # 8:00 - утренний, 14:00 - любовь, 21:00 - вечерний
    schedule = [
        (8, 'morning'),
        (14, 'love'),
        (21, 'evening'),
    ]
    while True:
        try:
            now = datetime.now()
            for hour, post_type in schedule:
                if now.hour == hour and not is_already_posted_today(post_type):
                    logger.info(f"⏰ Время публиковать {post_type} (час {hour})")
                    publish_horoscope(post_type)
            # Проверяем каждые 10 минут
            time.sleep(600)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"❌ Main: {e}")
            time.sleep(300)

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

def start_web_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                posted = len(load_posted())
                uptime = str(datetime.now() - BOT_START_TIME).split('.')[0]
                last = LAST_POST_TIME.strftime('%H:%M:%S') if LAST_POST_TIME else '—'
                moon_phase, moon_emoji = get_moon_phase()
                html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Звёзды Говорят</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #0a0a2a 0%, #1a1a4a 100%); color: #fff; padding: 20px; }}
.box {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; margin: 16px auto; max-width: 500px; border: 1px solid rgba(255,215,0,0.2); }}
h1 {{ color: #ffd700; text-align: center; }}
.stat {{ display: flex; justify-content: space-between; padding: 8px 0; }}
</style></head><body>
<h1>✨ Звёзды Говорят</h1>
<div class="box">
  <div class="stat"><span>Статус</span><span style="color:#4dd964">✅ Активен</span></div>
  <div class="stat"><span>Uptime</span><span>{uptime}</span></div>
  <div class="stat"><span>Фаза Луны</span><span>{moon_emoji} {moon_phase}</span></div>
  <div class="stat"><span>Последний пост</span><span>{last}</span></div>
  <div class="stat"><span>Опубликовано</span><span>{posted}</span></div>
</div>
<div class="box">
  <div class="stat"><span>Канал</span><span>{TELEGRAM_CHANNEL_ID or '—'}</span></div>
  <div class="stat"><span>Расписание</span><span>8:00 / 14:00 / 21:00</span></div>
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
