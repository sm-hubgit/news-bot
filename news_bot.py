"""
الملخص اليومي — الساعة ٩ مساءً بتوقيت بغداد. مجاني بالكامل.

- بدون GEMINI_API_KEY: أهم عناوين اليوم مرتبة حسب الأقسام، ويا روابطها.
- ويا GEMINI_API_KEY: ملخص مكتوب بكل قسم + فكرة اليوم.

المتغيرات: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
اختياري:   GEMINI_API_KEY, DAILY_MODEL (الافتراضي gemini-2.5-flash), DRY_RUN=1
"""

import html
import os
import re
from datetime import datetime

from common import (
    BAGHDAD, dedupe, fetch_rss, fetch_telegram, gemini, gemini_enabled,
    link_html, log, send_telegram,
)
from sources import EXTRA_INSTRUCTIONS

MODEL = os.environ.get("DAILY_MODEL", "gemini-2.5-flash")
PER_SECTION = 7

SECTIONS = [
    ("🇮🇶", "العراق"),
    ("🌍", "العالم"),
    ("🎬", "فن ومشاهير"),
    ("💡", "تقنية وعلوم"),
    ("⚽", "رياضة"),
    ("✨", "منوعات"),
]


def plain_digest(items: list[dict]) -> str:
    out = []
    for emoji, title in SECTIONS:
        sec = [it for it in items if it["emoji"] == emoji]
        sec.sort(key=lambda x: x["ts"], reverse=True)
        if not sec:
            continue
        out.append(f"<b>{emoji} {title}</b>")
        for it in sec[:PER_SECTION]:
            out.append(f"• {html.escape(it['text'])} — {link_html(it['source'], it['link'])}")
        out.append("")
    return "\n".join(out).strip() or "ما لگيت أخبار اليوم. تأكد من المصادر بملف sources.py"


PROMPT = """أنت محرر نشرة يومية شخصية لقارئ عراقي. تاريخ اليوم {today}.
اكتب ملخص اليوم بالعربية الفصحى المبسطة بهذي الأقسام وبهذا الترتيب:
🇮🇶 العراق (٤-٦ أخبار)، 🌍 العالم (٤-٥)، 🎬 فن ومشاهير (٣-٥)، 💡 تقنية وعلوم (٣-٤)،
⚽ رياضة (٣-٤)، ✨ منوعات وأفكار (٣-٤)، وبالأخير 🧠 فكرة اليوم: جملة أو جملتين ملهمة أو تجربة صغيرة.

القواعد:
- اعتمد فقط على الأخبار المرقّمة أدناه. لا تخترع أخبار.
- كل خبر سطر أو سطرين بأسلوبك، وبعده رقم مصدره بين أقواس مربعة هيچ: [12]
- ادمج الأخبار المتشابهة، واختار الأهم.
- عنوان كل قسم بين <b></b>. لا تستخدم Markdown ولا أي وسوم ثانية.
- ابدأ مباشرة بدون مقدمة.
{extra}

الأخبار:
{items}
"""


def gemini_digest(items: list[dict]) -> str:
    items = sorted(items, key=lambda x: x["ts"], reverse=True)[:250]
    prompt = PROMPT.format(
        today=datetime.now(BAGHDAD).strftime("%Y-%m-%d"),
        extra=EXTRA_INSTRUCTIONS.strip(),
        items="\n".join(f"{i}. {it['emoji']} [{it['source']}] {it['text']}"
                        for i, it in enumerate(items)),
    )
    text = gemini(prompt, MODEL)
    text = text.replace("```html", "").replace("```", "").strip()

    # نحمي النص، ونرجّع <b> فقط، ونحوّل [12] لرابط المصدر
    safe = html.escape(text).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")

    def to_link(m):
        i = int(m.group(1))
        if 0 <= i < len(items) and items[i]["link"]:
            return link_html("المصدر", items[i]["link"])
        return ""

    return re.sub(r"\[(\d+)\]", to_link, safe)


def main() -> None:
    log("Fetching sources…")
    items = dedupe(fetch_rss(26) + fetch_telegram(26))
    log(f"{len(items)} unique items")

    body = None
    if gemini_enabled() and items:
        try:
            body = gemini_digest(items)
        except Exception as exc:
            log(f"Gemini failed, using plain digest: {exc}")
    if body is None:
        body = plain_digest(items)

    header = f"🗞 <b>ملخصك اليومي — {datetime.now(BAGHDAD):%Y/%m/%d}</b>\n\n"
    send_telegram(header + body)


if __name__ == "__main__":
    main()
