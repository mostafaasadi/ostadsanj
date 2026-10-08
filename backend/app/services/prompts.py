CANONICAL_TOPICS = [
    "کیفیت تدریس",
    "نمره‌دهی",
    "جزوه و منابع",
    "حجم تکالیف",
    "حضور و غیاب",
    "برگزاری امتحان",
    "پاسخگویی",
    "اخلاق و احترام",
    "عدالت و انصاف",
    "تبعیض",
    "انعطاف‌پذیری",
    "دانش و تسلط",
    "سایر",
]

ASPECT_KEYS = [
    "teaching_quality",
    "knowledge",
    "grading",
    "fairness",
    "responsiveness",
    "respect",
    "materials",
    "workload",
    "attendance",
    "exam_conduct",
    "flexibility",
    "discrimination",
]

TOKEN_RATIO = 0.85

ANALYSIS_SYSTEM_PROMPT = (
    "تو تحلیل‌گر نظرات دانشجویان درباره اساتید دانشگاه هستی.\n"
    "وظیفه تو تشخیص ارتباط پیام به استاد و استخراج ساختاریافته احساس، ابعاد عملکرد، موضوعات و خلاصه نظر است.\n\n"
    "قانون‌ها:\n"
    "۱. اگر پیام سوال، اطلاع‌رسانی، تبلیغ یا بی‌ربط به استاد و درس اوست، is_relevant را false بگذار.\n"
    "۲. فقط وقتی پیام حاوی نظر، ارزیابی، تجربه، شکایت یا توصیه صریح درباره استاد یا درس اوست، contains_opinion را true بگذار.\n"
    "۲.۵. اگر پیام نام این استاد را فقط می‌آورد ولی صریحاً می‌گوید با او کلاس نداشته است "
    "(مثل «با استاد ... نداشتم»، «از ... خبر ندارم»، «باهاش کلاس نگرفتم»), "
    "یا محتوای نظر واقعاً درباره استاد دیگری است که در همان پیام نام برده شده، "
    "is_relevant را برای این استاد روی false بگذار.\n"
    "۲.۶. هم‌نامی به‌تنهایی کافی نیست: اگر واژهٔ مشابه نام استاد در معنای رایج به کار رفته "
    "(مثل «سروری» به معنی سرور/میزبان) و بافت پیام دربارهٔ شخص استاد یا درس او نیست "
    "(مثلاً دربارهٔ سامانه، سرور، سایت یا امور دانشگاه است)، is_relevant=false بگذار. "
    "مثال: «وقتی سروری ضعیفه و نمیتونه میزبان این همه درخواست باشه...» دربارهٔ سرور است، نه استاد سروری.\n"
    "۲.۷. اگر نظرِ پیام دربارهٔ شخصِ نام‌بردهٔ دیگری است (نام استاد دیگری در پیام آمده) "
    "و هیچ ارجاعی به استادِ مرتبط نشده، برای این استاد is_relevant=false بگذار؛ "
    "حتی اگر پیام در زنجیرهٔ پاسخ به رشته‌ای دربارهٔ استاد مرتبط باشد. "
    "مثال: «با هدایی اصن برندار چون امتحانش سخته» برای استاد سروری → is_relevant=false، ولی برای هدایی یک نظر معتبر است.\n"
    "۲.۸. نام درس‌ها را با ابعاد عملکرد اشتباه نگیر: در گروه معارف، واژه‌هایی مثل «اخلاق»، «اندیشه»، "
    "«تفسیر»، «انقلاب»، «معارف»، «حکمت» نام درس است، نه بُعد «اخلاق و احترام». "
    "بعد respect فقط وقتی می‌آید که منش و رفتار استاد با دانشجو مطرح باشد؛ "
    "اشاره به درس اخلاق (مثل «اخلاق میانترم»، «با او اخلاق داشته باشه») به‌خودی‌خود نظر دربارهٔ اخلاق استاد نیست "
    "و اگر صحبت دربارهٔ میانترم یا امتحانِ همان درس است، بُعد exam_conduct را بیاور.\n"
    "۲.۹. سوالِ مشورتی یا تجربه‌پرسی («کسی هست با X اخلاق داشته باشه؟»، «کسی با X کلاس داشته؟») نظر نیست: is_relevant=false. "
    "تکه‌پیام یا برچسب بدون بار ارزیابی («اخلاق میانترم #X»، «X ک پره») نظر نیست: contains_opinion=false "
    "و اگر هیچ ارزیابی ضمنی ندارد is_relevant=false. "
    "توجه: توصیهٔ صریح همراه با دلیل («با X برندار چون...») همچنان نظر است.\n"
    "۳. sentiment فقط یکی از: positive, negative, neutral, mixed\n"
    "۴. sentiment_score عددی بین -1 تا 1 است و هرگز نباید null باشد.\n"
    "۵. کلیدهای مجاز aspects فقط این‌ها هستند:\n" + ", ".join(ASPECT_KEYS) + "\n"
    "۶. هر عضو aspects شامل: aspect, sentiment, score بین -1 تا 1, evidence برابر عین عبارت پیام\n"
    "۷. topics فقط از این فهرست استاندارد انتخاب شود:\n" + "، ".join(CANONICAL_TOPICS) + "\n"
    "۸. key_point یک جمله فارسی و خلاصه از نظر است، بدون نام استاد.\n"
    "۹. severity یکی از: none, low, medium, high؛ مقدار medium یا high فقط برای اتهام جدی مانند تبعیض، رشوه، توهین یا تخلف اداری.\n"
    "۱۰. confidence عددی بین 0 تا 1 است و هرگز نباید null باشد.\n"
    "۱۱. واکنش‌های گروه روی پیام را به‌عنوان سیگنال موافقت یا مخالفت در نظر بگیر: "
    "حتی یک 👍 یا ❤ یعنی دست‌کم یک نفر با آن نظر موافق بوده و نویز نیست. "
    "واکنش‌های بیشتر (👍×۳ یا ❤×۲ و بالاتر) نشانهٔ هم‌صدایی قوی‌تر گروه هستند؛ "
    "واکنش‌های مخالف (😡، 👎، 🤮) نشانهٔ مخالفت گروه با آن نظر هستند. "
    "واکنش‌ها جایگزین قضاوت تو بر اساس متن پیام نمی‌شوند، "
    "ولی اگر جهت متن و جهت واکنش‌ها هم‌خوان بود، مقدار confidence را کمی بالاتر بگیر.\n"
)


def build_analysis_user_prompt(message_text: str, professor_name: str, parent_text: str = None, reactions: dict = None) -> str:
    parts = [f"استاد مرتبط: {professor_name}"]
    if parent_text:
        parts.append("زمینه پیام والد (پیام فعلی پاسخ به آن است):\n" + parent_text[:200])
    if reactions:
        parts.append("واکنش‌های ثبت‌شده روی این پیام: " + "، ".join(f"{emo} ×{cnt}" for emo, cnt in reactions.items()))
    parts.append("متن پیام:\n" + message_text)
    parts.append("خروجی JSON:")
    return "\n\n".join(parts)


SAMPLE_ANALYSIS_OUTPUT = """{
  "is_relevant": true,
  "message_type": "evaluation",
  "contains_opinion": true,
  "sentiment": "negative",
  "sentiment_score": -0.6,
  "aspects": [
    {"aspect": "teaching_quality", "sentiment": "negative", "score": -0.7, "evidence": "تدریس خوب نیست"},
    {"aspect": "grading", "sentiment": "neutral", "score": 0.0, "evidence": "نمره منصفانه"}
  ],
  "topics": ["کیفیت تدریس", "نمره‌دهی"],
  "key_point": "کیفیت تدریس استاد پایین است اما نمره‌دهی منصفانه دارد",
  "severity": "low",
  "confidence": 0.85
}"""

SAMPLE_SUMMARY_OUTPUT = """{
  "summary_text": "این استاد در کیفیت تدریس و تسلط بر موضوع بازخورد مثبت گرفته است، اما در پاسخگویی بیرون کلاس و شفافیت نمره‌دهی نقد جدی دارد. مجموع نظرات نشان می‌دهد تجربه کلاس برای بیشتر دانشجویان قابل قبول بوده است.",
  "strengths": ["تسلط بالا بر موضوع درس", "ارائه جزوه‌های کامل"],
  "weaknesses": ["پاسخگویی کند به پیام‌ها", "عدم شفافیت در نمره‌دهی"],
  "warnings": []
}"""

SUMMARY_SYSTEM_PROMPT = (
    "تو گزارش‌نویس تحلیلی سامانه ارزیابی اساتید هستی.\n"
    "با توجه به آمار و خلاصه نظرات دانشجویان درباره یک استاد، گزارشی منصفانه، بدون بزرگ‌نمایی و بدون اختراع نظر جدید می‌نویسی.\n"
    "خروجی فقط یک JSON معتبر با کلیدهای زیر است:\n"
    "summary_text: سه تا پنج جمله فارسی پیوسته\n"
    "strengths: فهرست تا چهار مورد\n"
    "weaknesses: فهرست تا چهار مورد\n"
    "warnings: فهرست اتهام‌های جدی در صورت وجود، وگرنه فهرست خالی"
)


def build_summary_user_prompt(payload: dict) -> str:
    stats = payload.get("stats") or {}
    lines = [f"گزارش تحلیلی درباره استاد «{payload.get('name', '')}» بنویس."]
    lines.append("آمار کمی:")
    lines.append(
        f"امتیاز کلی: {payload.get('overall_score') or 'نامشخص'} از 100- تا 100+ | "
        f"تعداد نظرات واقعی: {stats.get('total', 0)} "
        f"({stats.get('positive', 0)} مثبت، {stats.get('negative', 0)} منفی، {stats.get('mixed', 0)} مختلط) | "
        f"تعداد کاربران یکتا: {stats.get('unique_users', 0)} | "
        f"بازه زمانی: {payload.get('date_from') or 'نامشخص'} تا {payload.get('date_to') or 'نامشخص'}"
    )
    lines.append("ابعاد عملکرد (میانگین امتیاز از -1 تا +1):")
    aspects = payload.get("aspects") or []
    if aspects:
        lines.extend(
            f"- {a.get('label', a.get('aspect'))}: {a.get('avg', 0):+} ({a.get('count', 0)} نظر)"
            for a in aspects
        )
    else:
        lines.append("- بعدی ثبت نشده")
    topics = payload.get("topics") or []
    lines.append("موضوعات پرتکرار: " + ("، ".join(f"{t[0]} ({t[1]})" for t in topics) if topics else "-"))
    lines.append(f"تعداد پیام‌های جدی (شدت متوسط/بالا): {payload.get('severity_count', 0)}")
    reactions_total = payload.get("reactions_total")
    if reactions_total:
        lines.append(f"مجموع واکنش‌های اعضای گروه روی نظرات این استاد: {reactions_total}")
    lines.append("نمونه خلاصه نظرات دانشجویان:")
    key_points = payload.get("key_points") or []
    if key_points:
        lines.extend(f"{i + 1}. {kp}" for i, kp in enumerate(key_points))
    else:
        lines.append("- نمونه‌ای موجود نیست")
    lines.append("خروجی JSON:")
    lines.append('{"summary_text": "...", "strengths": ["..."], "weaknesses": ["..."], "warnings": ["..."]}')
    lines.append("قوانین: منصفانه بنویس؛ بزرگ‌نمایی نکن؛ اگر داده‌ای کم است احتیاط کن؛ فقط بر اساس داده‌های بالا بنویس.")
    return "\n".join(lines)


def approx_tokens(text: str, ratio: float = TOKEN_RATIO) -> int:
    if not text:
        return 0
    return int(len(text) * ratio)