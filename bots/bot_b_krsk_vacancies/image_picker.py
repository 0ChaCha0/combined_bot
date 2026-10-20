#!/usr/bin/env python3
"""
image_picker.py — модуль подбора картинок для поста
=====================================================

Что делает:
  • Извлекает ВСЕ URL картинок из статьи (RSS media_content, enclosures, <img> в summary)
  • Достаёт og:image / twitter:image со страницы статьи (если в RSS нет картинок)
  • Валидирует каждую (скачивает, проверяет размер, формат)
  • Возвращает список (url, image_bytes) до max_count штук

БЕЗ генерации через pollinations.ai / AI — только реальные картинки из источников.

Использование:
    from image_picker import pick_images_for_article

    images = pick_images_for_article(article, max_count=4)
    if images:
        first_url, first_bytes = images[0]
        send_photo_to_telegram(first_bytes, caption)
"""

import re
import logging
import requests
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# Лимиты
MAX_PHOTO_SIZE = 10 * 1024 * 1024       # 10 MB (Telegram limit)
MIN_PHOTO_SIZE = 5 * 1024                # 5 KB — меньше игнорируем (иконки, трекеры)
MAX_IMAGES_TO_CHECK = 8                  # сколько URL проверять максимум
HTTP_TIMEOUT = 20                        # секунд на скачивание одной картинки
OG_FETCH_TIMEOUT = 15                    # секунд на загрузку страницы для og:image

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)


def _normalize_url(url: str) -> Optional[str]:
    """Нормализует URL: добавляет https:// если нужно, проверяет валидность."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith('//'):
        url = 'https:' + url
    elif url.startswith('/'):
        return None  # относительный URL — пропускаем
    if not (url.startswith('http://') or url.startswith('https://')):
        return None
    return url


def extract_images_from_summary(summary: str) -> List[str]:
    """Извлекает все <img src="..."> из HTML-строки summary/content."""
    if not summary:
        return []
    urls = []
    for match in re.finditer(
        r'<img[^>]+src=["\']([^"\']+)["\']',
        summary, re.IGNORECASE,
    ):
        url = _normalize_url(match.group(1))
        if url:
            urls.append(url)
    return urls


def extract_images_from_feed_entry(entry) -> List[str]:
    """
    Извлекает все URL картинок из feedparser entry.
    Источники: media_content, media_thumbnail, enclosures, links, summary.
    """
    urls = []

    # 1. media_content
    try:
        mc = getattr(entry, 'media_content', None) or []
        for item in mc:
            if isinstance(item, dict):
                url = _normalize_url(item.get('url'))
                if url:
                    urls.append(url)
    except Exception:
        pass

    # 2. media_thumbnail
    try:
        mt = getattr(entry, 'media_thumbnail', None) or []
        for item in mt:
            if isinstance(item, dict):
                url = _normalize_url(item.get('url'))
                if url:
                    urls.append(url)
    except Exception:
        pass

    # 3. enclosures
    try:
        for enc in getattr(entry, 'enclosures', []) or []:
            if isinstance(enc, dict):
                if 'image' in str(enc.get('type', '')).lower():
                    url = _normalize_url(enc.get('href', ''))
                    if url:
                        urls.append(url)
    except Exception:
        pass

    # 4. links с type=image/*
    try:
        for link in getattr(entry, 'links', []) or []:
            if isinstance(link, dict):
                if 'image' in str(link.get('type', '')).lower():
                    url = _normalize_url(link.get('href', ''))
                    if url:
                        urls.append(url)
    except Exception:
        pass

    # 5. <img> в summary или content[0].value
    try:
        summary = getattr(entry, 'summary', '') or ''
        if not summary:
            content = getattr(entry, 'content', None)
            if content and isinstance(content, list) and len(content) > 0:
                summary = content[0].get('value', '') if isinstance(content[0], dict) else ''
        urls.extend(extract_images_from_summary(summary))
    except Exception:
        pass

    return urls


def fetch_open_graph_image(article_url: str) -> Optional[str]:
    """
    Загружает страницу статьи и достаёт og:image или twitter:image.
    Используется когда в RSS не было картинок.
    """
    if not article_url:
        return None
    try:
        r = requests.get(
            article_url,
            timeout=OG_FETCH_TIMEOUT,
            headers={'User-Agent': USER_AGENT},
            allow_redirects=True,
        )
        r.raise_for_status()
        html = r.text[:200000]  # ограничиваем размер чтобы не парсить огромные страницы

        # og:image
        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                html, re.IGNORECASE,
            )
        if match:
            return _normalize_url(match.group(1))

        # twitter:image
        match = re.search(
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
                html, re.IGNORECASE,
            )
        if match:
            return _normalize_url(match.group(1))

        # Первый <img> на странице (с фильтром по размеру)
        for img_match in re.finditer(
            r'<img[^>]+src=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        ):
            url = _normalize_url(img_match.group(1))
            if url and ('logo' not in url.lower() and 'icon' not in url.lower() and 'avatar' not in url.lower()):
                return url

    except Exception as e:
        logger.debug(f"fetch_og {article_url}: {e}")
    return None


def validate_and_download_image(url: str) -> Optional[bytes]:
    """
    Скачивает картинку, проверяет:
      - Content-Type содержит image
      - Размер: 5 KB .. 10 MB
      - Сигнатура: JPEG / PNG / WebP / GIF
    Возвращает bytes или None.
    """
    try:
        r = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            stream=True,
            headers={'User-Agent': USER_AGENT},
            allow_redirects=True,
        )
        r.raise_for_status()

        ctype = r.headers.get('Content-Type', '').lower()
        # Если сервер сказал Content-Type и это не image — пропускаем
        if ctype and 'image' not in ctype and 'octet-stream' not in ctype:
            return None

        # Скачиваем с лимитом
        content = b''
        for chunk in r.iter_content(8192):
            content += chunk
            if len(content) > MAX_PHOTO_SIZE + 1024:
                return None  # слишком большая
        if len(content) < MIN_PHOTO_SIZE:
            return None
        if len(content) > MAX_PHOTO_SIZE:
            return None

        # Проверяем сигнатуру (без PIL)
        # JPEG: \xFF\xD8
        # PNG: \x89PNG
        # GIF: GIF8
        # WebP: RIFF....WEBP
        if not (
            content.startswith(b'\xff\xd8')
            or content.startswith(b'\x89PNG')
            or content.startswith(b'GIF8')
            or (content.startswith(b'RIFF') and b'WEBP' in content[:16])
        ):
            return None

        return content
    except Exception as e:
        logger.debug(f"validate_image {url}: {e}")
        return None


def pick_images_for_article(article: dict, max_count: int = 4) -> List[Tuple[str, bytes]]:
    """
    Главная функция. Найти до max_count валидных картинок для статьи.

    article — dict с полями:
      - link: URL статьи (для og:image)
      - summary: HTML контент (для <img>)
      - image_url: уже найденная картинка (если была)
      - _entry: feedparser entry (опционально, для всех источников RSS)

    Возвращает список [(url, bytes), ...] — до max_count штук.
    """
    candidates: List[str] = []

    # 1. Из feedparser entry (если сохранён)
    entry = article.get('_entry')
    if entry is not None:
        candidates.extend(extract_images_from_feed_entry(entry))

    # 2. <img> из summary (HTML)
    summary = article.get('summary', '') or ''
    candidates.extend(extract_images_from_summary(summary))

    # 3. Поле image_url (если бот сам его нашёл)
    img_url = article.get('image_url')
    if img_url:
        normalized = _normalize_url(img_url)
        if normalized:
            candidates.append(normalized)

    # 4. og:image со страницы (если кандидатов мало)
    if len(candidates) < 2 and article.get('link'):
        og = fetch_open_graph_image(article['link'])
        if og:
            candidates.append(og)

    # Уникальные
    seen = set()
    unique = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    # Валидируем (проверяем не больше MAX_IMAGES_TO_CHECK)
    result: List[Tuple[str, bytes]] = []
    for url in unique[:MAX_IMAGES_TO_CHECK]:
        if len(result) >= max_count:
            break
        img_bytes = validate_and_download_image(url)
        if img_bytes:
            result.append((url, img_bytes))
            logger.info(f"🖼️ Картинка валидна: {url[:80]} ({len(img_bytes) // 1024} KB)")

    return result


def pick_first_image(article: dict) -> Optional[bytes]:
    """
    Удобная обёртка — получить ОДНУ лучшую картинку (для публикации без preview).
    """
    images = pick_images_for_article(article, max_count=1)
    return images[0][1] if images else None
