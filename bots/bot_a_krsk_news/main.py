#!/usr/bin/env python3
"""
🤖 Красноярск Сейчас — автономный бот-автопостинг новостей Красноярска
=======================================================================

Изюминка канала:
  ☀️  Утро: "Крск за 60 секунд" — дайджест главных новостей дня
  🏔️  День: отдельные новости с эмодзи Красноярска (🌁 мост, 🏔️ столбы, ⚡️ ГЭС)
  🌙  Вечер: "Улица дня" — что сегодня произошло на известной улице города

Автоматизация:
  • Парсит 8 RSS-источников (ngs24, newslab, prokras, gtrk-krsk, sibnovosti, 1gorodskoi, aif-krsk, prokras)
  • Фильтрует по локальным ключевикам (Красноярск, край, район, мкр, улица, ДТП)
  • AI делает саммари на русском с локальным колоритом
  • Автопостинг 5 раз в день в пиковые часы
  • Картинки из статей + AI-генерация как fallback
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
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
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
import image_picker  # ← общий модуль подбора картинок

# === CONFIG ===
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')
ADMIN_USER_ID = os.environ.get('ADMIN_USER_ID', '')
PROXY_URL = os.environ.get('PROXY_URL', '').strip()
ENABLE_IMAGES = os.environ.get('ENABLE_IMAGES', 'true').lower() == 'true'
IMAGE_SOURCE = os.environ.get('IMAGE_SOURCE', 'both').lower()
IMAGE_WIDTH = int(os.environ.get('IMAGE_WIDTH', '1024'))
IMAGE_HEIGHT = int(os.environ.get('IMAGE_HEIGHT', '576'))
PREVIEW_BEFORE_MINUTES = int(os.environ.get('PREVIEW_BEFORE_MINUTES', '15'))
ENABLE_PREVIEW = os.environ.get('ENABLE_PREVIEW', 'true').lower() == 'true'
ENABLE_BREAKING_NEWS = os.environ.get('ENABLE_BREAKING_NEWS', 'true').lower() == 'true'
BREAKING_NEWS_SCORE = int(os.environ.get('BREAKING_NEWS_SCORE', '70'))
BREAKING_NEWS_COOLDOWN = int(os.environ.get('BREAKING_NEWS_COOLDOWN', '180'))
AI_API_KEY = os.environ.get('AI_API_KEY', '')
AI_BASE_URL = os.environ.get('AI_BASE_URL', 'https://api.groq.com/openai/v1')
AI_MODEL = os.environ.get('AI_MODEL', 'openai/gpt-oss-20b')
POSTS_PER_DAY = int(os.environ.get('POSTS_PER_DAY', '5'))
WORKING_HOURS_START = int(os.environ.get('WORKING_HOURS_START', '7'))
WORKING_HOURS_END = int(os.environ.get('WORKING_HOURS_END', '23'))
POSTED_FILE = os.environ.get('POSTED_FILE', 'posted_urls.json')
LOG_FILE = os.environ.get('LOG_FILE', 'bot.log')
PORT = int(os.environ.get('PORT', '8080'))

# === RSS КРАСНОЯРСКА ===
# Внимание: проверено 2026-10. Многие «канонические» URL-ы давно умерли
# (ngs24.ru/rss/, sibnovosti.ru/rss/krasnoyarskiy-kray/, 1gorodskoi.ru, krsk.aif.ru, gtrk.ru/krsk/rss,
#  newslab.ru/rss, 24rus.ru/rss, prokras.ru — последний вообще не про Красноярск, а про покраску дерева).
# Ниже — реальные рабочие URL-ы на 2026-10.
RSS_FEEDS = [
    'https://ngs24.ru/rss-feeds/rss.xml',         # НГС24 — главный городской портал (RSS по новому пути)
    'https://krsk.sibnovosti.ru/rss',            # Сибновости — Красноярский край (301-redirect на sibnovosti.ru/rss/krasnoyarskiy-kray/)
    'https://tass.ru/rss/v2.xml',                 # ТАСС — федеральный (ред. новости с тегом «Красноярск» проходят фильтр)
    'https://ria.ru/export/rss2/archive/index.xml', # РИА Новости — федеральный (тоже ред. может пройти фильтр)
]

# Если федеральный фид не содержит Красноярск-новостей — он просто не даст вклад в локальную выборку,
# но это не критично, так как ngs24 + sibnovosti стабильно отдают ~15-30 новостей края в день.

STRONG_KEYWORDS = [
    'красноярск', 'края', 'край', 'енесейск', 'енесей', 'сибирск',
    'академгородок', 'столбы', 'подгорный', 'мкр ', 'тц ', 'советск',
    'железногорск', 'сосновоборск', 'дивногорск', 'аэропорт ',
    'емельяново', 'базаиха', 'солнечный', 'взлётка', 'северный',
    'кропоткин', 'свердловск', 'централ', 'октябрьск',
]

WEAK_KEYWORDS = [
    'улиц', 'дом ', 'район', 'мост', 'парк', 'дтп', 'авария', 'пожар',
    'школа', 'больниц', 'новострой', 'жк ', 'суд ', 'полиц', 'следств',
    'детск', 'сад ', 'метео', 'погода', 'зима', 'лето',
]

BREAKING_STRONG = [
    'взрыв', 'пожар', 'наводнени', 'обрушен', 'авария', 'погиб',
    'пострадал', 'эвакуац', 'теракт', 'массовое', 'чп ', 'пробк',
    'блокиров', 'без вести', 'пропал', 'найден мертв', 'убийств',
    'нападени', 'ограблен', 'задержан', 'возбуждено дело', 'резкое',
    'рекорд', 'историческ', 'впервые',
]

BREAKING_MEDIUM = [
    'срочно', 'экстренно', 'важно', 'прорыв', 'крупный', 'масштабн',
    'беспрецедент', 'завтра ', 'сегодня ', 'вчера ', 'мэр ',
    'губернатор', 'правительств', 'ограбление', 'кража', 'наезд',
    'столкн',
]

BREAKING_WEAK = ['красноярск', 'край', 'район', 'города', 'новостройки', 'заводы', 'офис']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding='utf-8')],
)
logger = logging.getLogger('krsk_news')

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
    cutoff = datetime.now() - timedelta(days=30)
    posted = [p for p in posted if isinstance(p, dict) and p.get('time') and datetime.fromisoformat(p['time']) > cutoff]
    posted = posted[-500:]
    try:
        with open(POSTED_FILE, 'w', encoding='utf-8') as f:
            json.dump({'posted_urls': posted}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_posted_url: {e}")

def is_already_posted(url):
    posted = load_posted_urls()
    return any((item.get('url') == url if isinstance(item, dict) else item == url) for item in posted)

def ai_relevance_score(article):
    text = (article.get('title', '') + ' ' + article.get('summary', '')).lower()
    strong = sum(1 for kw in STRONG_KEYWORDS if kw in text)
    weak = sum(1 for kw in WEAK_KEYWORDS if kw in text)
    return strong * 5 + max(0, weak - 1) * 2

def is_ai_related(article):
    return ai_relevance_score(article) >= 5

def fetch_rss_entries():
    all_entries = []
    feed_status = []  # для диагностики
    for feed_url in RSS_FEEDS:
        try:
            # feedparser по умолчанию не отправляет User-Agent и не работает с Cloudflare/DDoS-Guard.
            # Скармливаем ему UA Chrome + Accept-encoding, чтобы ngs24/sibnovosti отдавали XML.
            feed = feedparser.parse(
                feed_url,
                agent=(
                    'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) '
                    'Gecko/20100101 Firefox/130.0'
                ),
                request_headers={
                    'Accept': 'application/rss+xml, application/atom+xml, text/xml;q=0.9, */*;q=0.8',
                    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5',
                },
            )
            status = feed.get('status', 0)
            n_entries = len(feed.entries)
            feed_status.append(f"{feed_url} -> HTTP {status}, {n_entries} entries")
            if n_entries == 0:
                # Если фид вернул 0 — попробуем скачать raw и отдать feedparser уже строки
                try:
                    r = requests.get(
                        feed_url,
                        timeout=15,
                        headers={
                            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0',
                            'Accept': 'application/rss+xml, application/atom+xml, text/xml;q=0.9, */*;q=0.8',
                            'Accept-Language': 'ru-RU,ru;q=0.9',
                        },
                        proxies=get_proxies(),
                    )
                    if r.status_code == 200 and r.text.strip().startswith('<'):
                        feed = feedparser.parse(r.text)
                        n_entries = len(feed.entries)
                        feed_status[-1] = f"{feed_url} -> fallback HTTP {r.status_code}, {n_entries} entries"
                except Exception as e_fallback:
                    feed_status[-1] = f"{feed_url} -> fallback failed: {e_fallback}"
            for entry in feed.entries[:15]:
                image_url = extract_image_from_entry(entry)
                all_entries.append({
                    'title': getattr(entry, 'title', ''),
                    'link': getattr(entry, 'link', ''),
                    'summary': getattr(entry, 'summary', ''),
                    'published': getattr(entry, 'published', ''),
                    'source': feed_url,
                    'image_url': image_url,
                    '_entry': entry,  # сохраняем entry целиком для image_picker
                })
        except Exception as e:
            logger.error(f"Парсинг {feed_url}: {e}")
            feed_status.append(f"{feed_url} -> ERROR: {e}")
    # Диагностика: если собрано 0 — сообщим админу, что все фиды сломались
    if not all_entries:
        logger.error(f"⚠️ Все RSS-фиды пустые! Детали:\n  " + "\n  ".join(feed_status))
    else:
        logger.info(f"📦 Всего статей: {len(all_entries)} (из {len(RSS_FEEDS)} фидов)")
        for line in feed_status:
            logger.info(f"  • {line}")
    return all_entries

def extract_image_from_entry(entry):
    try:
        mc = getattr(entry, 'media_content', None) or getattr(entry, 'media_thumbnail', None)
        if mc and isinstance(mc, list) and len(mc) > 0:
            url = mc[0].get('url') if isinstance(mc[0], dict) else None
            if url and url.startswith('http'):
                return url
    except Exception:
        pass
    try:
        enclosures = getattr(entry, 'enclosures', None)
        if enclosures:
            for enc in enclosures:
                if isinstance(enc, dict) and 'image' in str(enc.get('type', '')).lower():
                    href = enc.get('href', '')
                    if href.startswith('http'):
                        return href
    except Exception:
        pass
    try:
        summary = getattr(entry, 'summary', '') or getattr(entry, 'content', [{}])[0].get('value', '')
        if summary:
            match = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', summary)
            if match:
                url = match.group(1)
                if url.startswith('//'):
                    url = 'https:' + url
                elif url.startswith('/'):
                    return None
                if url.startswith('http'):
                    return url
    except Exception:
        pass
    return None

def filter_fresh_articles(articles, hours=72):
    now = datetime.now()
    fresh = []
    for art in articles:
        if not art.get('published'):
            fresh.append(art)
            continue
        parsed_date = None
        for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S']:
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

def select_best_articles(articles, limit=5):
    candidates = []
    for art in articles:
        if not art.get('link') or is_already_posted(art['link']):
            continue
        art['_score'] = ai_relevance_score(art)
        candidates.append(art)
    candidates.sort(key=lambda x: x['_score'], reverse=True)
    return candidates[:limit]

AI_FAILURE_COUNT = 0
AI_FAILURE_THRESHOLD = 5
AI_DISABLED = False

def make_summary(article):
    """Уникальный промпт с маркетинговыми фишками и локальным колоритом."""
    global AI_FAILURE_COUNT, AI_DISABLED
    if not AI_API_KEY or AI_DISABLED:
        return format_without_ai(article)
    title = article.get('title', 'Без названия')[:300]
    summary_raw = clean_html(article.get('summary', ''))[:1500]
    link = article.get('link', '')

    prompt = f"""Ты — главный редактор Telegram-канала "Красноярск Сейчас" (⚡️ @krsk_seychas).

Твоя задача — сделать из новости крутой пост, который ВЫЗЫВАЕТ ЭМОЦИЮ и ЗАСТАВЛЯЕТ ЧИТАТЬ.

🌟 МАРКЕТИНГОВЫЕ ПРИНЦИПЫ:
1. **Эмоциональный заголовок** с локальным колоритом (используй эмодзи 🌁 мост, 🏔️ столбы, ⚡️ ГЭС, 🌡️ погода, 🚦 ДТП)
2. **Первый абзац — "крючок"**: одна цепляющая фраза, которая заставит читать дальше
3. **3-5 ключевых фактов** с разными эмодзи (✅ ⚡️ 📊 📍 💡 🔥)
4. **"Что это значит для красноярцев"** — 1-2 строки, приземлённо
5. **Призыв к действию**: "А вы видели? 👀" или "Поделитесь с друзьями 🔁"
6. **Локальная лексика**: Крск, Енисей, столбы, мост, централка, взлётка
7. Объём: 600-900 символов
8. Ссылка + 2-3 хештега в конце (#Красноярск #КрскСейчас)

ИСТОЧНИК:
Заголовок: {title}
Текст: {summary_raw}
Ссылка: {link}

После поста, на новой строке, добавь:
IMAGE_PROMPT: <английские ключевые слова для картинки, 5-15 слов>

Ответ — пост + строка IMAGE_PROMPT: ..."""

    for attempt in range(3):
        try:
            response = requests.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={'Authorization': f'Bearer {AI_API_KEY}', 'Content-Type': 'application/json'},
                json={
                    'model': AI_MODEL,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.8,
                    'max_tokens': 1500,
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
    title = article.get('title', 'Без названия')[:200]
    summary = clean_html(article.get('summary', ''))[:500]
    link = article.get('link', '')
    return f"📰 {title}\n\n{summary}\n\n🔗 {link}\n\n#Красноярск #КрскСейчас"

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
    """Возвращает ОДНУ картинку (bytes) для прямой публикации без preview.
    Использует image_picker — берёт картинки из статьи (RSS, og:image),
    БЕЗ генерации через pollinations.ai.
    """
    if not ENABLE_IMAGES:
        return None
    images = image_picker.pick_images_for_article(article, max_count=1)
    if images:
        return images[0][1]
    return None

def get_post_images(article, max_count=4):
    """Возвращает СПИСОК картинок [(url, bytes), ...] для preview с inline-кнопками."""
    if not ENABLE_IMAGES:
        return []
    return image_picker.pick_images_for_article(article, max_count=max_count)

def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("send_to_telegram: нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        return False
    if len(text) > 4096:
        text = text[:4090] + '...'
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': False}
    last_err = ''
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30, proxies=get_proxies())
            data = r.json()
            if data.get('ok'):
                return True
            desc = str(data.get('description', ''))
            err_code = data.get('error_code', '')
            last_err = f"{err_code} {desc}"
            if 'parse' in desc.lower() or 'can\'t find end of' in desc.lower() or 'tag' in desc.lower():
                payload['parse_mode'] = ''
                continue
            if 'chat not found' in desc.lower() or 'forbidden' in desc.lower() or 'not enough rights' in desc.lower() or 'chat deactivated' in desc.lower():
                logger.error(f"send_to_telegram: Telegram отказал (chat={TELEGRAM_CHANNEL_ID}): {last_err}. Бот не админ в канале или неверный channel_id.")
                return False
            logger.warning(f"send_to_telegram {attempt+1}/3: {last_err}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            last_err = str(e)
            logger.error(f"send {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    logger.error(f"send_to_telegram: не удалось после 3 попыток. Последняя ошибка: {last_err}")
    return False

def send_photo_to_telegram(image_bytes, caption):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("send_photo_to_telegram: нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHANNEL_ID")
        return False
    caption = caption[:1020] + '...' if len(caption) > 1024 else caption
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
    data = {'chat_id': TELEGRAM_CHANNEL_ID, 'caption': caption, 'parse_mode': 'HTML'}
    last_err = ''
    for attempt in range(3):
        try:
            r = requests.post(url, files=files, data=data, timeout=90, proxies=get_proxies())
            result = r.json()
            if result.get('ok'):
                return True
            desc = str(result.get('description', ''))
            err_code = result.get('error_code', '')
            last_err = f"{err_code} {desc}"
            if 'parse' in desc.lower():
                data['parse_mode'] = ''
                files = {'photo': ('image.jpg', image_bytes, 'image/jpeg')}
                continue
            if 'chat not found' in desc.lower() or 'forbidden' in desc.lower() or 'not enough rights' in desc.lower() or 'chat deactivated' in desc.lower():
                logger.error(f"send_photo_to_telegram: Telegram отказал (chat={TELEGRAM_CHANNEL_ID}): {last_err}. Проверь, что бот — админ канала с правом 'Post Messages'.")
                return False
            logger.warning(f"send_photo {attempt+1}/3: {last_err}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            last_err = str(e)
            logger.error(f"send_photo {attempt+1}/3: {e}")
            time.sleep(5 * (attempt + 1))
    logger.error(f"send_photo_to_telegram: не удалось после 3 попыток. Последняя ошибка: {last_err}")
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
            "🤖 <b>Красноярск Сейчас — команды</b>\n\n"
            "/status — статус\n/publish — пост СЕЙЧАС\n/skip — пропустить\n"
            "/schedule 7 23 — изменить часы\n/preview 15 — предпросмотр\n"
            "/next — след. пост\n/help — справка"
        )
    elif cmd == '/status':
        posted = len(load_posted_urls())
        send_message_to_admin(f"📊 <b>Статус</b>\n📡 Канал: {TELEGRAM_CHANNEL_ID}\n📨 Постов: {posted}\n🧠 AI: {AI_MODEL} ({'✅' if not AI_DISABLED else '⚠️ fallback'})\n🖼️ Картинки: {'вкл' if ENABLE_IMAGES else 'выкл'}\n📅 {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00")
    elif cmd == '/publish':
        publish_next_post(force=True)
    elif cmd == '/skip':
        SKIP_NEXT_POST = True
        send_message_to_admin("⏭️ Пропущен")
    elif cmd == '/schedule':
        if len(args) == 2:
            try:
                s, e = int(args[0]), int(args[1])
                update_env_var('WORKING_HOURS_START', str(s))
                update_env_var('WORKING_HOURS_END', str(e))
                send_message_to_admin(f"✅ Расписание: {s}:00-{e}:00 (после рестарта)")
            except ValueError:
                send_message_to_admin("❌ /schedule 7 23")
        else:
            send_message_to_admin(f"📅 Сейчас: {WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00")
    elif cmd == '/preview':
        if args:
            try:
                m = int(args[0])
                update_env_var('PREVIEW_BEFORE_MINUTES', str(m))
                send_message_to_admin(f"✅ Предпросмотр за {m} мин")
            except ValueError:
                send_message_to_admin("❌ /preview 15")
        else:
            send_message_to_admin(f"👁️ {PREVIEW_BEFORE_MINUTES} мин")
    elif cmd == '/next':
        articles = fetch_rss_entries()
        ai = [a for a in articles if is_ai_related(a)]
        fresh = filter_fresh_articles(ai, hours=72)
        best = select_best_articles(fresh, limit=1)
        if best:
            send_message_to_admin(f"📋 <b>Следующий:</b>\n📰 {best[0]['title'][:150]}")
        else:
            send_message_to_admin("📭 Нет свежих")
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
    elif data.startswith('pv_pub:'):
        post_id = data.split(':', 1)[1]
        publish_from_preview(post_id, use_image=True)
    elif data.startswith('pv_noimg:'):
        post_id = data.split(':', 1)[1]
        publish_from_preview(post_id, use_image=False)
    elif data.startswith('pv_next:'):
        post_id = data.split(':', 1)[1]
        change_preview_image(post_id, +1)
    elif data.startswith('pv_prev:'):
        post_id = data.split(':', 1)[1]
        change_preview_image(post_id, -1)
    elif data.startswith('pv_skip:'):
        post_id = data.split(':', 1)[1]
        skip_from_preview(post_id)
    elif data.startswith('pv_noop:'):
        pass  # кнопка-индикатор "1/4"

LAST_POST_TIME = None
BOT_START_TIME = datetime.now()

# === PREVIEW WITH IMAGE PICKER ===
# In-memory storage для отложенных на предпросмотр постов
# post_id -> {'article', 'text', 'images': [(url, bytes)], 'current_idx': int, 'message_id': int}
PENDING_PREVIEW: Dict[str, dict] = {}

def send_preview_to_admin(article, text, images):
    """Отправляет админу пост + первую картинку (если есть) с inline-кнопками."""
    post_id = str(int(time.time() * 1000))
    PENDING_PREVIEW[post_id] = {
        'article': article,
        'text': text,
        'images': images,
        'current_idx': 0,
        'created_at': time.time(),
    }
    # Чистим старые (>1 часа) preview, чтобы не копились
    _cleanup_stale_preview()

    keyboard = build_preview_keyboard(post_id, len(images))

    caption = text[:1024]
    if len(text) > 1024:
        caption += '...'

    if images:
        # Отправляем с первой картинкой
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        files = {'photo': ('img.jpg', images[0][1], 'image/jpeg')}
        data = {
            'chat_id': ADMIN_USER_ID,
            'caption': caption,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard),
        }
        try:
            r = requests.post(url, files=files, data=data, timeout=60, proxies=get_proxies())
            result = r.json()
            if result.get('ok'):
                PENDING_PREVIEW[post_id]['message_id'] = result['result']['message_id']
                logger.info(f"👁️ Preview отправлен, post_id={post_id}, картинок={len(images)}")
                return True
            logger.error(f"Preview send error: {result}")
        except Exception as e:
            logger.error(f"Preview send: {e}")
        return False
    else:
        # Без картинки
        full_text = caption + "\n\n_(нет подходящих картинок)_"
        return send_message_to_admin(full_text, reply_markup=keyboard)

def build_preview_keyboard(post_id: str, total_images: int) -> dict:
    """Строит inline-клавиатуру для preview-поста."""
    rows = []
    if total_images > 1:
        rows.append([
            {'text': '◀️ Пред. картинка', 'callback_data': f'pv_prev:{post_id}'},
            {'text': f'1/{total_images}', 'callback_data': f'pv_noop:{post_id}'},
            {'text': 'След. картинка ▶️', 'callback_data': f'pv_next:{post_id}'},
        ])
    rows.append([
        {'text': '✅ Опубликовать', 'callback_data': f'pv_pub:{post_id}'},
    ])
    if total_images > 0:
        rows.append([
            {'text': '🚫 Без картинки', 'callback_data': f'pv_noimg:{post_id}'},
        ])
    rows.append([
        {'text': '⏭️ Пропустить пост', 'callback_data': f'pv_skip:{post_id}'},
    ])
    return {'inline_keyboard': rows}

def _cleanup_stale_preview():
    """Удаляет preview-посты старше 1 часа."""
    now = time.time()
    stale = [pid for pid, p in PENDING_PREVIEW.items()
             if now - p.get('created_at', 0) > 3600]
    for pid in stale:
        PENDING_PREVIEW.pop(pid, None)

def change_preview_image(post_id: str, direction: int):
    """Меняет картинку в предпросмотре (editMessageMedia). direction: -1 или +1."""
    p = PENDING_PREVIEW.get(post_id)
    if not p:
        return False
    images = p['images']
    if len(images) <= 1:
        return False

    p['current_idx'] = (p['current_idx'] + direction) % len(images)
    new_idx = p['current_idx']
    new_bytes = images[new_idx][1]
    msg_id = p.get('message_id')
    if not msg_id:
        return False

    caption_text = p['text'][:1024]
    if len(p['text']) > 1024:
        caption_text += '...'

    # Обновляем inline-клавиатуру (номер картинки)
    keyboard = build_preview_keyboard(post_id, len(images))

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageMedia"
    # editMessageMedia требует multipart/form-data
    # media как attach://photo
    media_payload = {
        'type': 'photo',
        'media': 'attach://photo',
        'caption': caption_text,
        'parse_mode': 'HTML',
    }
    files = {
        'media': (None, json.dumps(media_payload), 'application/json'),
        'photo': ('img.jpg', new_bytes, 'image/jpeg'),
    }
    data = {
        'chat_id': ADMIN_USER_ID,
        'message_id': msg_id,
        'reply_markup': json.dumps(keyboard),
    }
    try:
        r = requests.post(url, files=files, data=data, timeout=60, proxies=get_proxies())
        result = r.json()
        if result.get('ok'):
            logger.info(f"🔄 Картинка сменена на {new_idx+1}/{len(images)} (post_id={post_id})")
            return True
        logger.warning(f"editMessageMedia: {result}")
    except Exception as e:
        logger.error(f"change_preview_image: {e}")
    return False

def publish_from_preview(post_id: str, use_image: bool = True, image_idx: int = None):
    """Публикует пост из preview в канал."""
    p = PENDING_PREVIEW.get(post_id)
    if not p:
        send_message_to_admin(f"⚠️ Preview {post_id} не найден (устарел?)")
        return False
    article = p['article']
    text = p['text']
    images = p['images']

    if use_image and images:
        idx = image_idx if image_idx is not None else p['current_idx']
        idx = idx % len(images)
        img_bytes = images[idx][1]
        success = send_photo_to_telegram(img_bytes, text)
        if not success:
            logger.warning("sendPhoto failed, fallback to text")
            success = send_to_telegram(text)
    else:
        success = send_to_telegram(text)

    if success:
        save_posted_url(article['link'], article['title'])
        global LAST_POST_TIME
        LAST_POST_TIME = datetime.now()
        # Удаляем сообщение предпросмотра у админа
        msg_id = p.get('message_id')
        if msg_id:
            try:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
                requests.post(url, json={'chat_id': ADMIN_USER_ID, 'message_id': msg_id},
                              timeout=10, proxies=get_proxies())
            except Exception:
                pass
        send_message_to_admin(f"✅ Опубликовано: {article['title'][:60]}")
        PENDING_PREVIEW.pop(post_id, None)
        return True
    send_message_to_admin(f"❌ Ошибка публикации: {article['title'][:60]}")
    return False

def skip_from_preview(post_id: str):
    """Пропускает пост из preview (просто удаляет)."""
    p = PENDING_PREVIEW.pop(post_id, None)
    if not p:
        return False
    msg_id = p.get('message_id')
    if msg_id:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
            requests.post(url, json={'chat_id': ADMIN_USER_ID, 'message_id': msg_id},
                          timeout=10, proxies=get_proxies())
        except Exception:
            pass
    send_message_to_admin("⏭️ Пост пропущен")
    return True

def is_working_hours():
    hour = datetime.now().hour
    return WORKING_HOURS_START <= hour < WORKING_HOURS_END

def calculate_next_interval():
    hour = datetime.now().hour
    base = (24 * 60) // max(POSTS_PER_DAY, 1)
    if hour in [9, 12, 18, 21]:
        base = base // 2
    return max(60, min(240, int(base * random.uniform(0.7, 1.3))))

def publish_next_post(force=False):
    global LAST_POST_TIME, SKIP_NEXT_POST
    if SKIP_NEXT_POST and not force:
        SKIP_NEXT_POST = False
        return False
    try:
        articles = fetch_rss_entries()
        if not articles:
            return False
        ai_articles = [a for a in articles if is_ai_related(a)]
        logger.info(f"🎯 Локальных: {len(ai_articles)}")
        fresh = filter_fresh_articles(ai_articles, hours=72)
        best = select_best_articles(fresh, limit=1)
        if not best:
            logger.info("🎉 Все свежие уже опубликованы")
            return False
        article = best[0]
        logger.info(f"📝 Публикуем: {article['title'][:80]}")
        text = make_summary(article)
        if not text:
            return False

        # Если включён preview И есть admin — отправляем превью с кнопками
        if ENABLE_PREVIEW and ADMIN_USER_ID and not force:
            images = get_post_images(article, max_count=4)
            logger.info(f"🖼️ Найдено картинок для preview: {len(images)}")
            return send_preview_to_admin(article, text, images)

        # Иначе (force=True или preview выключен) — публикуем сразу
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
    logger.info("🤖 Красноярск Сейчас — бот запущен")
    logger.info(f"📅 Постов в день: {POSTS_PER_DAY}")
    logger.info(f"🕘 Часы: {WORKING_HOURS_START}:00 - {WORKING_HOURS_END}:00")
    logger.info(f"📡 Канал: {TELEGRAM_CHANNEL_ID or 'НЕ ЗАДАН!'}")
    logger.info(f"🧠 AI: {AI_BASE_URL} / {AI_MODEL}")
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
                html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>Красноярск Сейчас</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: linear-gradient(135deg, #0a1e3c 0%, #1a3a6c 100%); color: #fff; padding: 20px; }}
.box {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; margin: 16px auto; max-width: 500px; border: 1px solid rgba(255,215,0,0.2); }}
h1 {{ color: #ffd700; text-align: center; }}
.stat {{ display: flex; justify-content: space-between; padding: 8px 0; }}
</style></head><body>
<h1>🏔️ Красноярск Сейчас</h1>
<div class="box">
  <div class="stat"><span>Статус</span><span style="color:#4dd964">✅ Активен</span></div>
  <div class="stat"><span>Uptime</span><span>{uptime}</span></div>
  <div class="stat"><span>Последний пост</span><span>{last}</span></div>
  <div class="stat"><span>Опубликовано</span><span>{posted}</span></div>
</div>
<div class="box">
  <div class="stat"><span>Канал</span><span>{TELEGRAM_CHANNEL_ID or '—'}</span></div>
  <div class="stat"><span>Расписание</span><span>{WORKING_HOURS_START}:00-{WORKING_HOURS_END}:00</span></div>
  <div class="stat"><span>Постов в день</span><span>{POSTS_PER_DAY}</span></div>
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
