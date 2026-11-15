"""أدوات مشتركة: جمع المصادر، الإرسال لتلگرام، و Gemini (اختياري ومجاني)."""

import difflib
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from sources import RSS_FEEDS, TELEGRAM_CHANNELS

BAGHDAD = timezone(timedelta(hours=3))
UA = {"User-Agent": "Mozilla/5.0 (news-bot)"}
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def log(*a):
    print(*a, file=sys.stderr)


def key_for(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def norm(text: str) -> str:
    """توحيد النص للمقارنة: يشيل اسم المصدر بعد ' - ' والتشكيل والرموز."""
    text = re.sub(r"\s+[-–|]\s+[^-–|]{2,40}$", "", text)  # "العنوان - اسم الموقع"
    text = re.sub(r"[ً-ْـ]", "", text)       # تشكيل وتطويل
    text = re.sub(r"[أإآ]", "ا", text).replace("ة", "ه").replace("ى", "ي")
    return " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())


def similar(a: str, b: str, threshold: float = 0.72) -> bool:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= threshold


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


# ---------------------------------------------------------------- المصادر

def fetch_rss(max_age_hours: float, per_feed: int = 40) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items = []
    for name, (emoji, url) in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=UA)
        except Exception as exc:
            log(f"RSS failed: {name}: {exc}")
            continue
        count = 0
        for e in feed.entries[:per_feed]:
            published = e.get("published_parsed") or e.get("updated_parsed")
            ts = None
            if published:
                dt = datetime(*published[:6], tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                ts = dt.timestamp()
            title = clean_title(e.get("title", ""))
            if not title:
                continue
            link = e.get("link", "")
            items.append({
                "key": key_for(link or title),
                "emoji": emoji,
                "source": name,
                "text": title,
                "link": link,
                "ts": ts or time.time(),
            })
            count += 1
        log(f"  {name}: {count}")
    return items


def parse_telegram_page(page: str, username: str, emoji: str) -> list[dict]:
    items = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        post = re.search(r'data-post="([^"]+)"', block)
        body = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S
        )
        if not post or not body:
            continue
        text = re.sub(r"<br\s*/?>", " ", body.group(1))
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text))).strip()
        if len(text) < 15:
            continue
        when = re.search(r'<time datetime="([^"]+)"', block)
        ts = time.time()
        if when:
            try:
                ts = datetime.fromisoformat(when.group(1)).timestamp()
            except ValueError:
                pass
        items.append({
            "key": key_for("tg", post.group(1)),
            "emoji": emoji,
            "source": f"@{username}",
            "text": text[:400],
            "link": f"https://t.me/{post.group(1)}",
            "ts": ts,
        })
    return items


def fetch_telegram(max_age_hours: float) -> list[dict]:
    cutoff = time.time() - max_age_hours * 3600
    items = []
    for ch, emoji in TELEGRAM_CHANNELS.items():
        try:
            r = requests.get(f"https://t.me/s/{ch}", headers=UA, timeout=20)
            r.raise_for_status()
            posts = [p for p in parse_telegram_page(r.text, ch, emoji) if p["ts"] >= cutoff]
            items += posts
            log(f"  @{ch}: {len(posts)}")
        except Exception as exc:
            log(f"Telegram channel failed: {ch}: {exc}")
    return items


def dedupe(items: list[dict], against: list[str] | None = None) -> list[dict]:
    """يحذف المكرر بالرابط، والعناوين المتشابهة (نفس الخبر من أكثر من موقع)."""
    against = list(against or [])
    out, seen_keys = [], set()
    for it in sorted(items, key=lambda x: x["ts"]):
        if it["key"] in seen_keys:
            continue
        if any(similar(it["text"], t) for t in against):
            continue
        seen_keys.add(it["key"])
        against.append(it["text"])
        out.append(it)
    return out


# ---------------------------------------------------------------- تلگرام

def split_message(text: str, limit: int = 4000) -> list[str]:
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def strip_html(text: str) -> str:
    text = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2 (\1)", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def send_telegram(text: str, silent: bool = False) -> None:
    for part in split_message(text):
        if DRY_RUN:
            print(part, "\n" + "-" * 40)
            continue
        url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
        payload = {
            "chat_id": os.environ["TELEGRAM_CHAT_ID"],
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }
        for _ in range(3):
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(r.json().get("parameters", {}).get("retry_after", 5) + 1)
                continue
            if r.status_code == 400 and "parse_mode" in payload:
                log(f"HTML rejected ({r.text}); sending as plain text")
                payload.pop("parse_mode")
                payload["text"] = strip_html(part)
                continue
            r.raise_for_status()
            break
        time.sleep(1.1)


def link_html(label: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


# ---------------------------------------------------------------- Gemini (اختياري)

def gemini_enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def gemini(prompt: str, model: str, json_mode: bool = False) -> str:
    """يرجع نص الرد، أو يرفع خطأ (مثلاً إذا خلصت الحصة المجانية)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    r = requests.post(
        url,
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json=body,
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise RuntimeError(f"Gemini empty response: {json.dumps(data)[:300]}")
    return text
