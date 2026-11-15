"""
متابعة الأخبار أول بأول — يشتغل كل ٥ دقايق على GitHub Actions. مجاني بالكامل.

- بدون GEMINI_API_KEY: يرسل العناوين الجديدة مثل ما هي (بعد حذف المكرر).
- ويا GEMINI_API_KEY (مجاني من Google): يفرز ويحذف المكرر بذكاء ويكتب كل خبر بسطر عربي.
  إذا خلصت الحصة المجانية، يرجع تلقائياً للطريقة الأولى.

المتغيرات: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
اختياري:   GEMINI_API_KEY, REALTIME_MODEL (الافتراضي gemini-2.5-flash-lite), DRY_RUN=1
"""

import html
import json
import os
import re
import time

from common import (
    dedupe, fetch_rss, fetch_telegram, gemini, gemini_enabled,
    link_html, log, send_telegram,
)
from sources import EXTRA_INSTRUCTIONS

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
MODEL = os.environ.get("REALTIME_MODEL", "gemini-2.5-flash-lite")
MAX_AGE_HOURS = 3
KEEP_SEEN_DAYS = 3
KEEP_RECENT_SENT = 200
MAX_ITEMS_PER_CALL = 80


# ---------------------------------------------------------------- الحالة

def load_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    cutoff = time.time() - KEEP_SEEN_DAYS * 86400
    state["seen"] = {k: v for k, v in state["seen"].items() if v > cutoff}
    state["recent_sent"] = state["recent_sent"][-KEEP_RECENT_SENT:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


# ---------------------------------------------------------------- بدون ذكاء اصطناعي

def format_plain(items: list[dict]) -> list[str]:
    msgs = []
    for it in items:
        msgs.append(
            f"{it['emoji']} {html.escape(it['text'])}\n— {link_html(it['source'], it['link'])}"
        )
    return msgs


# ---------------------------------------------------------------- ويا Gemini

PROMPT = """أنت محرر أخبار عاجلة لقارئ عراقي. اهتماماته: العراق أولاً، والعالم، والفن والمشاهير،
والتقنية والعلوم، والرياضة، والمنوعات المفيدة أو الملهمة. يريد كل خبر جديد أول بأول.
{extra}

عناوين انبعثت له مؤخراً (لا تعيد نفس الخبر حتى لو من مصدر ثاني):
{recent}

أخبار جديدة مرقّمة:
{items}

المطلوب:
- اختار كل خبر حقيقي وله قيمة. احذف الإعلانات والتهاني والمحتوى الفارغ والمكرر.
- إذا أكثر من مصدر يحچي عن نفس الخبر، ادمجهم بخبر واحد واذكر كل أرقامهم بـ ids.
- اكتب كل خبر بسطر أو سطرين بالعربية الفصحى المبسطة.
- رجّع JSON array فقط بهذا الشكل:
[{{"ids": [3, 7], "emoji": "🇮🇶", "urgent": false, "text": "..."}}]
  emoji واحد من: 🇮🇶 🌍 🎬 💡 ⚽ ✨
  urgent = true فقط للخبر الكبير جداً.
- إذا ماكو شي يستاهل، رجّع []
"""


def extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("text")]


def format_with_gemini(items: list[dict], recent: list[str]) -> tuple[list[str], list[str]]:
    """يرجع (الرسائل، النصوص المرسلة)."""
    msgs, texts = [], []
    for start in range(0, len(items), MAX_ITEMS_PER_CALL):
        batch = items[start:start + MAX_ITEMS_PER_CALL]
        prompt = PROMPT.format(
            extra=EXTRA_INSTRUCTIONS.strip(),
            recent="\n".join(f"- {t}" for t in (recent + texts)[-60:]) or "(لا شيء)",
            items="\n".join(f"{i}. [{it['source']}] {it['text']}" for i, it in enumerate(batch)),
        )
        picks = extract_json_array(gemini(prompt, MODEL, json_mode=True))
        for p in picks:
            ids = [i for i in p.get("ids", []) if isinstance(i, int) and 0 <= i < len(batch)]
            links, seen_links = [], set()
            for i in ids:
                if batch[i]["link"] and batch[i]["link"] not in seen_links:
                    seen_links.add(batch[i]["link"])
                    links.append(link_html(batch[i]["source"], batch[i]["link"]))
            prefix = "🔴 <b>عاجل</b> | " if p.get("urgent") else ""
            msg = f"{p.get('emoji', '📰')} {prefix}{html.escape(str(p['text']).strip())}"
            if links:
                msg += "\n— " + " · ".join(links[:3])
            msgs.append(msg)
            texts.append(str(p["text"]).strip())
    return msgs, texts


# ---------------------------------------------------------------- التشغيل

def main() -> None:
    log("Fetching sources…")
    items = fetch_rss(MAX_AGE_HOURS) + fetch_telegram(MAX_AGE_HOURS)
    state = load_state()
    now = time.time()

    if state is None:
        # أول تشغيل: نسجّل الموجود حالياً بدون إرسال حتى ما ينغرق تلگرام
        state = {"seen": {it["key"]: now for it in items},
                 "recent_sent": [it["text"] for it in items][-KEEP_RECENT_SENT:]}
        save_state(state)
        mode = "ويا Gemini ✨" if gemini_enabled() else "بدون ذكاء اصطناعي"
        send_telegram(f"✅ بدأت متابعة الأخبار أول بأول ({mode}).\nأي خبر جديد راح يوصلك هنا.")
        return

    new = [it for it in items if it["key"] not in state["seen"]]
    for it in new:
        state["seen"][it["key"]] = now
    new = dedupe(new, against=state["recent_sent"])
    log(f"Fetched {len(items)}, new after dedupe: {len(new)}")

    if new:
        msgs, texts = None, None
        if gemini_enabled():
            try:
                msgs, texts = format_with_gemini(new, state["recent_sent"])
            except Exception as exc:
                log(f"Gemini failed, falling back to plain mode: {exc}")
        if msgs is None:
            msgs, texts = format_plain(new), [it["text"] for it in new]

        for m in msgs:
            send_telegram(m)
        # نحفظ العناوين الأصلية والمكتوبة، حتى نقارن بيها بالمرات الجاية
        state["recent_sent"] += [it["text"] for it in new] + texts
        log(f"Sent {len(msgs)} messages")

    save_state(state)


if __name__ == "__main__":
    main()
