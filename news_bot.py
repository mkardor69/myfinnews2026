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
from deep_translator import GoogleTranslator

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # مثال: @mychannel یا -1001234567890

STATE_FILE = "sent_news.json"
MAX_STATE_ITEMS = 500          # حداکثر تعداد لینک ذخیره‌شده برای جلوگیری از تکرار
MAX_ITEMS_PER_RUN = 15         # حداکثر خبر در هر اجرا (برای جلوگیری از اسپم)

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


def translate_to_persian(text):
    try:
        if not text:
            return ""
        # گوگل ترنسلیت محدودیت طول داره، پس کوتاه می‌کنیم
        text = text[:1800]
        return GoogleTranslator(source="auto", target="fa").translate(text)
    except Exception as e:
        print(f"خطا در ترجمه: {e}")
        return text  # اگه ترجمه نشد، متن اصلی رو برگردون


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

    for source_name, feed_url in RSS_FEEDS.items():
        if sent_count >= MAX_ITEMS_PER_RUN:
            break

        print(f"در حال بررسی فید: {source_name}")
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"خطا در خواندن فید {source_name}: {e}")
            continue

        for entry in feed.entries[:10]:  # حداکثر ۱۰ خبر آخر هر فید
            if sent_count >= MAX_ITEMS_PER_RUN:
                break

            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()

            if not title or not link:
                continue

            item_hash = make_hash(link)
            if item_hash in sent_hashes:
                continue  # قبلاً ارسال شده

            if not matches_keywords(title, summary):
                continue

            # پاک کردن تگ‌های HTML ساده از خلاصه
            import re
            clean_summary = re.sub("<[^<]+?>", "", summary)[:500]

            title_fa = translate_to_persian(title)
            summary_fa = translate_to_persian(clean_summary)

            success = send_to_telegram(title_fa, summary_fa, link, source_name)
            if success:
                sent_hashes.add(item_hash)
                sent_count += 1
                print(f"✅ ارسال شد: {title_fa[:60]}")
                time.sleep(3)  # جلوگیری از rate-limit تلگرام
            else:
                print(f"❌ ارسال نشد: {title[:60]}")

    state["sent_hashes"] = list(sent_hashes)
    save_state(state)
    print(f"\nپایان اجرا. تعداد اخبار ارسال‌شده: {sent_count}")


if __name__ == "__main__":
    main()
