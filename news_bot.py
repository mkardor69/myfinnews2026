#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات اخبار مالی/جنگ -> ترجمه فارسی -> ارسال به کانال تلگرام
منابع: RSS رایگان (بدون نیاز به API Key پولی)
"""

import os
import json
import time
import hashlib
import requests
import feedparser
import re
import trafilatura
from datetime import datetime, timezone, date
from calendar import timegm
from zoneinfo import ZoneInfo
from deep_translator import GoogleTranslator

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # مثال: @mychannel یا -1001234567890

STATE_FILE = "sent_news.json"
MAX_STATE_ITEMS = 500          # حداکثر تعداد لینک ذخیره‌شده برای جلوگیری از تکرار
MAX_ITEMS_PER_RUN = 6          # حداکثر خبر در هر اجرا (برای پخش‌شدن اخبار در طول روز)
MAX_NEWS_AGE_HOURS = 3          # فقط اخباری که کمتر از این تعداد ساعت پیش منتشر شده‌اند (تازه بمونه)
MAX_BODY_CHARS = 1200          # حداکثر طول متن خبر قبل از ترجمه
MAX_SENTENCES_TO_TRANSLATE = 12  # حداکثر تعداد جمله برای ترجمه (جلوگیری از کندی/محدودیت نرخ)
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# فیدهای RSS مرتبط با: جنگ، اقتصاد کلان، فارکس، طلا، نفت، ارز دیجیتال
RSS_FEEDS = {
    "Reuters - Business":        "https://feeds.reuters.com/reuters/businessNews",
    "Reuters - World":           "https://feeds.reuters.com/Reuters/worldNews",
    "Investing.com - Economy":   "https://www.investing.com/rss/news_14.rss",
    "Investing.com - Forex":     "https://www.investing.com/rss/news_1.rss",
    "Investing.com - Commodities": "https://www.investing.com/rss/news_11.rss",
    "OilPrice.com":              "https://oilprice.com/rss/main",
    "Kitco News":                "https://www.kitco.com/rss/KitcoNews.xml",
    "CoinDesk":                  "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph":             "https://cointelegraph.com/rss",
    "ForexLive":                 "https://www.forexlive.com/feed/news",
}

# کلمات کلیدی برای فیلتر کردن اخبار (اختیاری - اگر خالی بذاری همه اخبار فیدها می‌رن)
KEYWORDS = [
    "war", "conflict", "attack", "military", "sanction", "missile",
    "gold", "oil", "opec", "crude", "forex", "dollar", "fed", "inflation",
    "interest rate", "crypto", "bitcoin", "ethereum", "market", "economy",
    "recession", "central bank", "geopolit",
]

# اولویت منابع: عدد کمتر = اولویت بالاتر (زودتر فرستاده می‌شود)
SOURCE_PRIORITY = {
    "Investing.com - Forex":       1,
    "ForexLive":                   1,
    "Kitco News":                  1,   # طلا
    "OilPrice.com":                1,   # نفت
    "Investing.com - Commodities": 1,
    "CoinDesk":                    2,
    "CoinTelegraph":               2,
    "Investing.com - Economy":     2,
    "Reuters - Business":          3,   # آخر از همه چک می‌شود
    "Reuters - World":             3,   # آخر از همه چک می‌شود
}

# ---------------------------------------------------------------------------
# توابع کمکی
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sent_hashes": []}


def save_state(state):
    # محدود کردن اندازه فایل استیت
    state["sent_hashes"] = state["sent_hashes"][-MAX_STATE_ITEMS:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def make_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def matches_keywords(title, summary):
    if not KEYWORDS:
        return True
    text = f"{title} {summary}".lower()
    return any(kw.lower() in text for kw in KEYWORDS)


def is_recent(entry):
    """فقط اخباری که در بازه‌ی MAX_NEWS_AGE_HOURS اخیر منتشر شده‌اند قبول می‌شوند."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        # اگر فید تاریخ نداشت، خبر را رد نمی‌کنیم (بهتر از حذف اشتباهی)
        return True
    published_dt = datetime.fromtimestamp(timegm(published), tz=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600
    return age_hours <= MAX_NEWS_AGE_HOURS


def get_published_dt(entry):
    """زمان انتشار خبر را برمی‌گرداند (برای مرتب‌سازی جدیدترین‌ها اول)."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(timegm(published), tz=timezone.utc)


def fetch_full_article_text(url):
    """تلاش برای گرفتن متن کامل خبر از خود صفحه (به‌جای فقط خلاصه‌ی کوتاه RSS)."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded)
            if text:
                return text.strip()
    except Exception as e:
        print(f"خطا در استخراج متن کامل: {e}")
    return ""


def translate_to_persian(text, max_sentences=MAX_SENTENCES_TO_TRANSLATE):
    """ترجمه جمله‌به‌جمله برای افزایش دقت ترجمه."""
    try:
        if not text:
            return ""
        text = text[:MAX_BODY_CHARS]
        sentences = re.split(r'(?<=[.!?])\s+', text)[:max_sentences]
        translated_parts = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            translated = GoogleTranslator(source="auto", target="fa").translate(sentence)
            translated_parts.append(translated if translated else sentence)
        return " ".join(translated_parts)
    except Exception as e:
        print(f"خطا در ترجمه: {e}")
        return text  # اگه ترجمه نشد، متن اصلی رو برگردون


def fix_bidi_text(text):
    """
    دور هر کلمه/عبارت انگلیسی-عددی (مثل IPO, Fed, GDP, 4.3) یک ایزوله‌ی جهتی
    یونیکد می‌کشد تا وسط متن فارسی (راست‌به‌چپ) درست و خوانا نمایش داده شود.
    """
    if not text:
        return text
    LRI = '\u2066'  # Left-to-Right Isolate
    PDI = '\u2069'  # Pop Directional Isolate
    return re.sub(
        r'[A-Za-z0-9][A-Za-z0-9.\-%/]*',
        lambda m: f'{LRI}{m.group(0)}{PDI}',
        text,
    )


def send_plain_message(text):
    if not BOT_TOKEN or not CHANNEL_ID:
        print("توکن یا آیدی کانال تنظیم نشده!")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, data=payload, timeout=20)
        if resp.status_code == 200:
            return True
        else:
            print(f"خطای تلگرام: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"خطا در ارسال به تلگرام: {e}")
        return False


def fetch_economic_calendar():
    try:
        resp = requests.get(ECONOMIC_CALENDAR_URL, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"خطا در گرفتن تقویم اقتصادی: {e}")
        return []


def build_calendar_message(events):
    now_tehran = datetime.now(TEHRAN_TZ)
    today_local = now_tehran.date()
    today_events = []
    for e in events:
        raw_date = e.get("date", "")
        try:
            # این تاریخ‌ها معمولاً منطقه‌ی زمانی خودشون رو دارن (مثلاً ...-04:00)
            event_dt_aware = datetime.fromisoformat(raw_date)
        except Exception:
            continue
        event_local = event_dt_aware.astimezone(TEHRAN_TZ)
        if event_local.date() == today_local and e.get("impact") in ("High", "Medium"):
            today_events.append((event_local, e))

    if not today_events:
        return None

    today_events.sort(key=lambda x: x[0])

    lines = ["📅 *تقویم اقتصادی امروز (به وقت تهران)*", ""]
    for event_local, e in today_events[:25]:
        country = e.get("country", "")
        title = e.get("title", "")
        impact = e.get("impact", "")
        forecast = e.get("forecast", "") or "—"
        previous = e.get("previous", "") or "—"

        title_fa = translate_to_persian(title, max_sentences=3)
        title_fa = fix_bidi_text(title_fa)
        impact_emoji = "🔴" if impact == "High" else "🟠"
        time_str = event_local.strftime("%H:%M")

        lines.append(f"{impact_emoji} {time_str} | {fix_bidi_text(country)}")
        lines.append(f"   {title_fa}")
        lines.append(f"   پیش‌بینی: {fix_bidi_text(forecast)}   |   قبلی: {fix_bidi_text(previous)}")
        lines.append("")  # فاصله بین رویدادها برای خوانایی بهتر

    return "\n".join(lines).strip()


def maybe_send_daily_calendar(state):
    today_str = str(datetime.now(TEHRAN_TZ).date())
    if state.get("last_calendar_date") == today_str:
        return  # امروز قبلاً فرستاده شده

    events = fetch_economic_calendar()
    message = build_calendar_message(events)
    if message:
        if send_plain_message(message):
            print("✅ تقویم اقتصادی امروز ارسال شد")
            state["last_calendar_date"] = today_str
    else:
        # حتی اگه رویداد مهمی نبود، تاریخ رو ثبت می‌کنیم که دوباره تلاش نکنه
        state["last_calendar_date"] = today_str


def send_to_telegram(title_fa, summary_fa, link, source):
    if not BOT_TOKEN or not CHANNEL_ID:
        print("توکن یا آیدی کانال تنظیم نشده!")
        return False

    message = (
        f"📰 *{title_fa}*\n\n"
        f"{summary_fa}\n\n"
        f"🔗 [مطالعه کامل خبر]({link})\n"
        f"🗞 منبع: {source}"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, data=payload, timeout=20)
        if resp.status_code == 200:
            return True
        else:
            print(f"خطای تلگرام: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"خطا در ارسال به تلگرام: {e}")
        return False


# ---------------------------------------------------------------------------
# منطق اصلی
# ---------------------------------------------------------------------------

def main():
    state = load_state()
    sent_hashes = set(state.get("sent_hashes", []))
    sent_count = 0

    # ابتدا تقویم اقتصادی امروز رو چک/ارسال کن (فقط یک‌بار در روز)
    maybe_send_daily_calendar(state)

    # مرحله ۱: همه‌ی فیدها رو می‌خونیم و خبرهای واجد شرایط (تازه، کلیدواژه‌دار، تکراری نبودن) رو جمع می‌کنیم
    candidates = []
    for source_name, feed_url in RSS_FEEDS.items():
        print(f"در حال بررسی فید: {source_name}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"خطا در خواندن فید {source_name}: {e}")
            continue

        for entry in feed.entries[:10]:  # حداکثر ۱۰ خبر آخر هر فید
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            rss_summary = entry.get("summary", entry.get("description", "")).strip()

            if not title or not link:
                continue

            item_hash = make_hash(link)
            if item_hash in sent_hashes:
                continue  # قبلاً ارسال شده

            if not matches_keywords(title, rss_summary):
                continue

            if not is_recent(entry):
                continue

            candidates.append({
                "source_name": source_name,
                "title": title,
                "link": link,
                "rss_summary": rss_summary,
                "item_hash": item_hash,
                "published_dt": get_published_dt(entry),
            })

    # مرحله ۲: اول بر اساس اولویت منبع (فارکس/طلا/نفت اول، Reuters آخر)، بعد جدیدترین‌ها اول
    candidates.sort(
        key=lambda c: (
            SOURCE_PRIORITY.get(c["source_name"], 2),
            -c["published_dt"].timestamp(),
        )
    )

    # مرحله ۳: ارسال به تلگرام (حداکثر MAX_ITEMS_PER_RUN خبر)
    for c in candidates[:MAX_ITEMS_PER_RUN]:
        # سعی می‌کنیم متن کامل مقاله رو از خود سایت بگیریم (طولانی‌تر از خلاصه RSS)
        full_text = fetch_full_article_text(c["link"])
        clean_rss_summary = re.sub("<[^<]+?>", "", c["rss_summary"])
        body_text = full_text if len(full_text) > len(clean_rss_summary) else clean_rss_summary
        body_text = body_text[:MAX_BODY_CHARS]

        title_fa = translate_to_persian(c["title"], max_sentences=3)
        summary_fa = translate_to_persian(body_text)
        title_fa = fix_bidi_text(title_fa)
        summary_fa = fix_bidi_text(summary_fa)

        success = send_to_telegram(title_fa, summary_fa, c["link"], c["source_name"])
        if success:
            sent_hashes.add(c["item_hash"])
            sent_count += 1
            print(f"✅ ارسال شد: {title_fa[:60]}")
            time.sleep(3)  # جلوگیری از rate-limit تلگرام
        else:
            print(f"❌ ارسال نشد: {c['title'][:60]}")

    state["sent_hashes"] = list(sent_hashes)
    save_state(state)
    print(f"\nپایان اجرا. تعداد اخبار ارسال‌شده: {sent_count}")


if __name__ == "__main__":
    main()
