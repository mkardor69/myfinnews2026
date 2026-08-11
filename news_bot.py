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
import difflib
from datetime import datetime, timezone, date
from calendar import timegm
from zoneinfo import ZoneInfo
from deep_translator import GoogleTranslator

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# هدر مرورگر برای جلوگیری از مسدود شدن درخواست‌ها توسط سایت‌ها (بعضی سایت‌ها بدون این هدر ۴۰۳ می‌دن)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {"User-Agent": USER_AGENT}

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")  # مثال: @mychannel یا -1001234567890

# DeepSeek مستقیم (حساب پولی خودت - قابل‌اعتمادتر از نسخه‌های رایگان)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = "deepseek-v4-pro"  # مدل بزرگ‌تر و دقیق‌تر (سه برابر گران‌تر از Flash، ولی بازم خیلی ارزونه)
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# OpenRouter رایگان (پشتیبان، اگه DeepSeek جواب نداد یا کلیدش نبود)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL_CANDIDATES = [
    "openrouter/free",  # روتر خودکار - همیشه یه گزینه‌ی فعال پیدا می‌کنه، بدون نیاز به قفل کردن مدل خاص
]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

STATE_FILE = "sent_news.json"
MAX_STATE_ITEMS = 500          # حداکثر تعداد لینک ذخیره‌شده برای جلوگیری از تکرار
MAX_ITEMS_PER_RUN = 14         # حداکثر خبر در هر اجرا (چون هر ۲ ساعت اجرا می‌شه، نه هر ۳۰ دقیقه)
MAX_NEWS_AGE_HOURS = 5          # فقط اخباری که کمتر از این تعداد ساعت پیش منتشر شده‌اند (تازه بمونه)
MAX_BODY_CHARS = 1200          # حداکثر طول متن خبر قبل از ترجمه
MAX_SENTENCES_TO_TRANSLATE = 12  # (دیگر استفاده نمی‌شود، برای سازگاری نگه داشته شده)
ECONOMIC_CALENDAR_URL = "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"

# فیدهای RSS مرتبط با: جنگ، اقتصاد کلان، فارکس، طلا، نفت، ارز دیجیتال
# نکته: فیدهای رسمی رویترز از سال ۲۰۲۰ غیرفعال شدن، به‌جاش از MarketWatch استفاده می‌کنیم
RSS_FEEDS = {
    "MarketWatch - Top Stories": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "CNBC - Economy":            "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC - Finance":            "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "Investing.com - Economy":   "https://www.investing.com/rss/news_14.rss",
    "Investing.com - Forex":     "https://www.investing.com/rss/news_1.rss",
    "Investing.com - Commodities": "https://www.investing.com/rss/news_11.rss",
    "OilPrice.com":              "https://oilprice.com/rss/main",
    "Kitco News":                "https://www.kitco.com/news/category/mining/rss",
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
    "CNBC - Economy":              2,
    "CNBC - Finance":              2,
    "MarketWatch - Top Stories":   3,   # اخبار کلی/جنگ - آخر از همه چک می‌شود
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


ERROR_PAGE_MARKERS = [
    "error 500", "error 404", "server error", "that's an error",
    "that’s an error", "please try again later", "page not found",
    "404 not found", "500 internal server error",
]


def looks_like_error_page(title, summary=""):
    """تشخیص می‌ده که آیا این «خبر» در واقع یه صفحه‌ی خطای واقعیه که اشتباهی
    تو فید منبع افتاده (مثل خطای سرور خود سایت خبری)، نه یه خبر واقعی."""
    text = f"{title} {summary}".lower()
    return any(marker in text for marker in ERROR_PAGE_MARKERS)


ADVICE_COLUMN_MARKERS = [
    "is it a mistake", "should i ", "am i ", "can i afford",
    "my brokerage", "my retirement", "my 401", "my portfolio",
    "moneyist", "ask an advisor", "retirement weekly", "financial advisor",
    "my husband", "my wife", "my parents", "my inheritance",
]


def is_personal_advice_column(title):
    """تشخیص می‌ده که آیا این یه ستون پرسش‌وپاسخ مالی شخصیه (نه خبر واقعی بازار) —
    این‌جور مقالات معمولاً پشت پی‌وال هستن و برای کانال خبری مالی/فارکس بی‌ربطن،
    پس ترجمه‌شون فقط هزینه‌ی الکی می‌شه."""
    lower_title = title.lower()
    has_marker = any(marker in lower_title for marker in ADVICE_COLUMN_MARKERS)
    # الگوی رایج دیگه: سوال شخصی که با ضمیر اول‌شخص شروع/تموم می‌شه و علامت سوال داره
    looks_like_question = lower_title.strip().endswith("?") and (
        " my " in lower_title or lower_title.startswith("i ") or lower_title.startswith("i'm")
    )
    return has_marker or looks_like_question


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


def parse_feed_safely(url):
    """خواندن فید RSS با هدر مرورگر تا سایت‌هایی که ربات‌ها رو مسدود می‌کنن جواب بدن."""
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception as e:
        print(f"خطا در خواندن فید: {e}")
        return feedparser.parse(b"")


# عنوان‌های رایج بخش «مقالات مرتبط» که باید از انتهای متن استخراج‌شده حذف بشن
BOILERPLATE_MARKERS = [
    "related posts", "related articles", "more top reads",
    "you might also like", "read more", "recommended for you",
    "more from", "also read", "further reading",
    "is committed to providing", "editorial policy", "editorial independence",
    "editorial standards", "produced with full editorial",
    "disclaimer:", "this article does not contain", "not financial advice",
]


def strip_boilerplate(text):
    """اگه بخش «مقالات مرتبط» تو متن استخراج‌شده باشه، از همون‌جا به بعد رو قطع می‌کنه
    تا تیتر خبرهای دیگه قاطی متن اصلی نشه."""
    lower_text = text.lower()
    cut_at = len(text)
    for marker in BOILERPLATE_MARKERS:
        idx = lower_text.find(marker)
        if idx != -1 and idx < cut_at:
            cut_at = idx
    return text[:cut_at].strip()


def clean_markdown_and_dedupe_title(text, original_title=""):
    """علامت‌های خام مارک‌داون (#, ##) رو از تیترها حذف می‌کنه (چون تلگرام مارک‌داون
    استاندارد نمی‌فهمه)، و اگه اولین خط متن تقریباً همون تیتر خبر باشه (تکراری با
    عنوانی که جدا می‌فرستیم)، اون خط رو حذف می‌کنه."""
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # حذف نشانه‌های تیتر مارک‌داون از ابتدای خط (#, ##, ###...)
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        cleaned_lines.append(stripped)

    # حذف خط اول اگه تقریباً عین تیتر خبر باشه (جلوگیری از تکرار عنوان)
    if cleaned_lines and original_title:
        first_line_norm = re.sub(r"\s+", " ", cleaned_lines[0].lower()).strip()
        title_norm = re.sub(r"\s+", " ", original_title.lower()).strip()
        if first_line_norm and difflib.SequenceMatcher(None, first_line_norm, title_norm).ratio() > 0.6:
            cleaned_lines = cleaned_lines[1:]

    return "\n".join(l for l in cleaned_lines if l.strip())


def dedupe_paragraphs(text):
    """بعضی صفحات خبری اول یه خلاصه‌ی بولت‌پوینت می‌ذارن که همون جملات متن اصلی رو
    با کلمات مشابه (نه لزوماً عیناً) تکرار می‌کنه. این تابع با مقایسه‌ی شباهت متنی،
    پاراگراف‌های تکراری یا خیلی شبیه به‌هم رو حذف می‌کنه."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    kept = []
    kept_normalized = []
    for p in paragraphs:
        normalized = re.sub(r"\s+", " ", p.lower()).strip("-*#• ")
        is_duplicate = False
        for k_norm in kept_normalized:
            ratio = difflib.SequenceMatcher(None, normalized, k_norm).ratio()
            if ratio > 0.6:  # بیش از ۶۰٪ شباهت یعنی احتمالاً تکراره
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(p)
            kept_normalized.append(normalized)
    return "\n".join(kept)


def fetch_full_article_text(url, original_title=""):
    """تلاش برای گرفتن متن کامل خبر از خود صفحه (به‌جای فقط خلاصه‌ی کوتاه RSS)."""
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        if resp.status_code == 200:
            text = trafilatura.extract(
                resp.text,
                favor_precision=True,  # ترجیح میده کمتر بگیره ولی دقیق‌تر باشه (کمتر بولرپلیت)
                include_comments=False,
                include_tables=False,
                output_format="markdown",  # لیست‌های بولت‌دار و خط‌به‌خط رو حفظ می‌کنه (مهم برای خبرهای بازار)
            )
            if text:
                cleaned = dedupe_paragraphs(strip_boilerplate(text.strip()))
                return clean_markdown_and_dedupe_title(cleaned, original_title)
    except Exception as e:
        print(f"خطا در استخراج متن کامل: {e}")
    return ""


def translate_with_google_fallback(text):
    """مترجم پشتیبان رایگان - وقتی OpenRouter در دسترس نیست."""
    try:
        translated = GoogleTranslator(source="auto", target="fa").translate(text)
        return translated if translated else text
    except Exception as e:
        print(f"خطا در ترجمه‌ی پشتیبان (گوگل): {e}")
        return text


PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def extract_numbers(text):
    """همه‌ی اعداد (فارسی یا انگلیسی) رو از متن استخراج می‌کنه، برای مقایسه‌ی صحت ترجمه."""
    normalized = text.translate(PERSIAN_DIGITS)
    return set(re.findall(r"\d+\.?\d*", normalized))


def numbers_match(original_text, translated_text):
    """چک می‌کنه که اعداد مهم متن اصلی (مثل درصدها و قیمت‌ها) تو ترجمه هم باشن.
    اگه حتی یه عدد مهم گم شده باشه، احتمال توهم/حذف اشتباه مدل رو نمی‌پذیریم و
    ترجیح می‌دیم برگردیم به گوگل ترنسلیت که هیچ‌وقت عدد از خودش نمی‌سازه یا حذف نمی‌کنه."""
    original_numbers = extract_numbers(original_text)
    translated_numbers = extract_numbers(translated_text)
    if not original_numbers:
        return True  # متنی که عدد نداره، نیازی به این چک نداره
    missing = original_numbers - translated_numbers
    return len(missing) == 0


# بازه‌ی یونیکد حروف/اسکریپت‌هایی که نباید تو ترجمه‌ی فارسی ظاهر بشن
# (اگه ظاهر بشن، یعنی مدل داشته توهم می‌زده و زبون قاطی کرده)
UNEXPECTED_SCRIPT_PATTERN = re.compile(
    r'['
    r'\u0400-\u04FF'   # سیریلیک (روسی و مشابه)
    r'\u4E00-\u9FFF'   # چینی
    r'\u3040-\u30FF'   # ژاپنی (هیراگانا/کاتاکانا)
    r'\uAC00-\uD7AF'   # کره‌ای
    r'\u0E00-\u0E7F'   # تایلندی
    r']'
)


def has_unexpected_script(text):
    """تشخیص می‌ده که آیا تو متن، حروف زبان‌های غیرمرتبط (روسی، چینی، ژاپنی، کره‌ای...)
    ظاهر شده یا نه. اگه ظاهر شده باشه، تقریباً قطعیه که مدل داشته توهم می‌زده."""
    return bool(UNEXPECTED_SCRIPT_PATTERN.search(text))


def translate_to_persian(text, max_chars=None):
    """ترجمه‌ی متن به فارسی. اول DeepSeek مستقیم (حساب پولی خودت، قابل‌اعتمادتر)،
    بعد چند مدل رایگان OpenRouter، در نهایت گوگل ترنسلیت (پشتیبان آخر)."""
    if not text:
        return ""
    limit = max_chars if max_chars else MAX_BODY_CHARS
    text = smart_truncate(text, limit)

    system_prompt = (
        "شما یک مترجم و تحلیلگر ارشد اخبار مالی هستید که سال‌ها برای کانال‌های معتبر "
        "تلگرامی و اینستاگرامی تحلیل فارکس، طلا، نفت و ارز دیجیتال فارسی‌زبان محتوا "
        "نوشته‌اید. متن انگلیسی داده‌شده را دقیقاً با همون سطح حرفه‌ای و طبیعی ترجمه کن. "
        "\n\n"
        "**واژگان تخصصی تحلیل تکنیکال** (معادل دقیق و رایج فارسی، نه ترجمه‌ی لفظی):\n"
        "bear flag=پرچم نزولی، bull flag=پرچم صعودی، support=سطح حمایت، "
        "resistance=سطح مقاومت، breakout=شکست قیمتی، breakdown=ریزش زیر حمایت، "
        "trendline=خط روند، moving average=میانگین متحرک، overbought=اشباع خرید، "
        "oversold=اشباع فروش، bullish/bearish=صعودی/نزولی، rally=رشد قیمتی، "
        "correction=اصلاح قیمتی، volatility=نوسان، consolidation=رنج زدن/تثبیت، "
        "double top/bottom=سقف/کف دوقلو، head and shoulders=الگوی سر و شونه.\n"
        "**واژگان مالی/اقتصادی کلان**:\n"
        "fund shuts down=صندوق منحل می‌شه، IPO=عرضه‌ی اولیه سهام، yield=بازده، "
        "rate cut/hike=کاهش/افزایش نرخ بهره، inflation=تورم، default=نکول، "
        "liquidity=نقدینگی، quantitative easing=تسهیل کمّی، hawkish/dovish=سیاست "
        "انقباضی/انبساطی، short squeeze=فشار فروش استقراضی، margin call=اخطار "
        "افزایش وجه تضمین، leverage=اهرم، delisting=لغو پذیرش از بورس.\n"
        "همیشه دقت کن معادل انتخابی، همون معنای دقیق مالی متن اصلی رو برسونه، نه "
        "نزدیک‌ترین کلمه‌ی لغت‌نامه‌ای.\n\n"
        "**لحن:** دقیقاً محاوره‌ای و طبیعی فارسی امروزی، مثل یه تحلیلگر باتجربه که "
        "مستقیم داره برای اعضای کانالش می‌نویسه، نه لحن رسمی خبرگزاری. این‌ها اجباریه: "
        "'را'→'رو'، 'می‌کند/می‌شود/می‌رسد'→'می‌کنه/می‌شه/می‌رسه'، حذف یا تبدیل 'است' "
        "در آخر جمله (مثلاً 'کاهش یافته است'→'کاهش پیدا کرده').\n\n"
        "**مثال سبک مطلوب:**\n"
        "متن انگلیسی: \"Gold extended its gains for a third session, climbing 1.2% "
        "to $4,320 as the dollar weakened ahead of Friday's jobs report.\"\n"
        "ترجمه‌ی خوب: \"طلا برای سومین جلسه‌ی متوالی به رشدش ادامه داد و ۱.۲٪ رشد "
        "کرد و به ۴۳۲۰ دلار رسید، چون دلار قبل از گزارش اشتغال جمعه ضعیف شده.\"\n\n"
        "**ساختار لیست/بولت:** اگه متن به‌صورت لیست یا نقطه‌به‌نقطه با آیتم‌های "
        "جداگانه بود (مثلاً چند خبر کوتاه بازار، هرکدوم با درصد و قیمت جدا)، همون "
        "ساختار خط‌به‌خط یا بولت رو در ترجمه هم دقیقاً حفظ کن؛ هر آیتم رو کاملاً جدا "
        "ترجمه کن و هیچ‌وقت دو آیتم مجزا رو تو یه جمله قاطی نکن، حتی اگه متن اصلی "
        "خط جداکننده نداشت.\n\n"
        "فقط ترجمه را برگردان، بدون هیچ توضیح یا مقدمه‌ی اضافی."
    )

    # مرحله ۱: DeepSeek مستقیم (اگه کلید پولی تنظیم شده باشه)
    if DEEPSEEK_API_KEY:
        try:
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.3,
            }
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            }
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            translated = data["choices"][0]["message"]["content"].strip()
            if translated and numbers_match(text, translated) and not has_unexpected_script(translated):
                return translated
            print("⚠️ DeepSeek جواب مشکوک/خالی/با زبان اشتباه داد، رفتن سراغ گزینه‌های بعدی")
        except Exception as e:
            print(f"خطا با DeepSeek مستقیم: {e}، رفتن سراغ گزینه‌های بعدی")

    # مرحله ۲: مدل‌های رایگان OpenRouter (اگه کلیدش تنظیم شده باشه)
    if OPENROUTER_API_KEY:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        for model_name in OPENROUTER_MODEL_CANDIDATES:
            try:
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.3,
                }
                resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                translated = data["choices"][0]["message"]["content"].strip()

                if not translated:
                    print(f"⚠️ مدل {model_name} خروجی خالی داد، مدل بعدی رو امتحان می‌کنیم")
                    continue

                if not numbers_match(text, translated):
                    print(f"⚠️ اعداد ترجمه‌ی مدل {model_name} با متن اصلی نمی‌خونه، مدل بعدی")
                    continue

                if has_unexpected_script(translated):
                    print(f"⚠️ ترجمه‌ی مدل {model_name} حاوی حروف زبان اشتباه (توهم مدل)، مدل بعدی")
                    continue

                return translated  # موفق بود

            except Exception as e:
                print(f"خطا با مدل {model_name}: {e}، مدل بعدی رو امتحان می‌کنیم")
                continue

    # اگه هیچ‌کدوم از مدل‌ها جواب نداد، گوگل ترنسلیت پشتیبان نهایی
    print("⚠️ هیچ‌کدوم از مدل‌های OpenRouter جواب نداد، رفتن سراغ پشتیبان گوگل")
    return translate_with_google_fallback(text)


def smart_truncate(text, limit):
    """متن رو تا سقف limit کاراکتر می‌بره، ولی سر آخرین جمله یا کلمه‌ی کامل قطعش می‌کنه
    (نه وسط یه کلمه، که باعث می‌شد آخر متن یه تیکه‌ی نصفه‌ول انگلیسی بمونه)."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # اول سعی می‌کنیم سر آخرین نقطه/علامت پایان جمله قطع کنیم
    last_sentence_end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("؟"), cut.rfind("?"))
    if last_sentence_end > limit * 0.5:  # اگه خیلی کوتاه نشه
        return cut[:last_sentence_end + 1]
    # وگرنه حداقل سر آخرین فاصله (پایان یه کلمه) قطع کن
    last_space = cut.rfind(" ")
    if last_space > 0:
        return cut[:last_space]
    return cut


def fix_bidi_text(text):
    """
    دور هر کلمه/عبارت انگلیسی-عددی (مثل IPO, Fed, GDP, 4.3) یک ایزوله‌ی جهتی
    یونیکد می‌کشد تا وسط متن فارسی (راست‌به‌چپ) درست و خوانا نمایش داده شود.
    همچنین اگه خود متن با یه کلمه‌ی انگلیسی شروع بشه (مثلاً اسم برند مثل Circle)،
    یه علامت نامرئی «راست‌به‌چپ» اول متن اضافه می‌کنه تا کل پاراگراف چپ‌به‌راست نشه.
    """
    if not text:
        return text
    LRI = '\u2066'  # Left-to-Right Isolate
    PDI = '\u2069'  # Pop Directional Isolate
    RLM = '\u200f'  # Right-to-Left Mark

    # مرحله ۱-الف: پسوندهای رایج فارسی (ها، های، تر، ترین، ای) که مستقیم به یه
    # کلمه‌ی انگلیسی/عدد چسبیدن (مثل "ETFهای") باید با نیم‌فاصله (نه فاصله‌ی کامل)
    # جدا بشن، وگرنه موقع تعویض خط از هم جدا میفتن و بدجوری به نظر میان
    ZWNJ = '\u200c'  # نیم‌فاصله
    persian_suffixes = r'(های|ها|ترین|تر|ای|یی)'
    text = re.sub(rf'([A-Za-z0-9]){persian_suffixes}(?=[\s\u0600-\u06FF]|$)', rf'\1{ZWNJ}\2', text)

    # مرحله ۱-ب: بقیه‌ی چسبندگی‌های فارسی-انگلیسی (که پسوند نیستن، بلکه دو کلمه‌ی
    # جدان مثل "در"+"ETF") رو با فاصله‌ی کامل از هم جدا می‌کنیم
    text = re.sub(r'([\u0600-\u06FF])([A-Za-z0-9])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z0-9])([\u0600-\u06FF])', r'\1 \2', text)

    # مرحله ۲: بولت‌های خط‌تیره‌ای مارک‌داون ("- ") تو متن راست‌به‌چپ بدجوری نشون
    # داده می‌شن؛ به‌جاش از یه بولت خنثی‌تر و مرسوم‌تر استفاده می‌کنیم
    text = re.sub(r'(?m)^-\s+', '• ', text)

    result = re.sub(
        r'[A-Za-z0-9][A-Za-z0-9.\-%/]*',
        lambda m: f'{LRI}{m.group(0)}{PDI}',
        text,
    )

    # اگه اولین کاراکتر «هر خط» (نه فقط کل متن) انگلیسی/لاتینه، اول اون خط یه RLM
    # می‌ذاریم - وگرنه اون خط به‌طور کامل جهتش برعکس می‌شه و بی‌معنی به نظر می‌رسه
    lines = result.split("\n")
    fixed_lines = []
    for line in lines:
        if re.match(r'^\s*[\u2066]', line):  # اگه از قبل ایزوله شروع شده (یعنی همون کلمه انگلیسی اول خطه)
            line = RLM + line
        fixed_lines.append(line)
    result = "\n".join(fixed_lines)

    return result


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
        resp = requests.get(ECONOMIC_CALENDAR_URL, headers=REQUEST_HEADERS, timeout=20)
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

        title_fa = translate_to_persian(title, max_chars=200)
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
        feed = parse_feed_safely(feed_url)
        if not feed or not getattr(feed, "entries", None):
            print(f"⚠️ فید {source_name} خالی یا غیرقابل‌دسترس بود")
            continue

        for entry in feed.entries[:10]:  # حداکثر ۱۰ خبر آخر هر فید
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            rss_summary = entry.get("summary", entry.get("description", "")).strip()

            if not title or not link:
                continue

            if looks_like_error_page(title, rss_summary):
                print(f"⏭️ رد شد (به‌نظر صفحه‌ی خطا می‌رسه، نه خبر واقعی): {title[:60]}")
                continue

            if is_personal_advice_column(title):
                print(f"⏭️ رد شد (ستون پرسش‌وپاسخ مالی شخصی، نه خبر بازار): {title[:60]}")
                continue

            item_hash = make_hash(link)
            if item_hash in sent_hashes:
                continue  # قبلاً ارسال شده

            if not matches_keywords(title, rss_summary):
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

    # مرحله ۳: ارسال به تلگرام (حداکثر MAX_ITEMS_PER_RUN خبر با محتوای واقعی)
    MIN_BODY_CHARS = 40  # اگه متن استخراج‌شده کمتر از این باشه، خبر بی‌محتوا حساب می‌شه

    for c in candidates:
        if sent_count >= MAX_ITEMS_PER_RUN:
            break

        # سعی می‌کنیم متن کامل مقاله رو از خود سایت بگیریم (طولانی‌تر از خلاصه RSS)
        full_text = fetch_full_article_text(c["link"], c["title"])
        clean_rss_summary = re.sub("<[^<]+?>", "", c["rss_summary"])
        body_text = full_text if len(full_text) > len(clean_rss_summary) else clean_rss_summary
        body_text = smart_truncate(body_text, MAX_BODY_CHARS)

        if len(body_text.strip()) < MIN_BODY_CHARS:
            # نتونستیم محتوای واقعی پیدا کنیم؛ این خبر رو رد می‌کنیم (ولی به‌عنوان دیده‌شده ثبتش می‌کنیم
            # تا هر اجرا دوباره امتحانش نکنیم)
            sent_hashes.add(c["item_hash"])
            print(f"⏭️ رد شد (بدون محتوای کافی): {c['title'][:60]}")
            continue

        if looks_like_error_page(c["title"], body_text):
            sent_hashes.add(c["item_hash"])
            print(f"⏭️ رد شد (لینک به صفحه‌ی خطا رسیده): {c['title'][:60]}")
            continue

        title_fa = translate_to_persian(c["title"], max_chars=300)
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
