"""
فتح صفحات فهارس المناقصات واستخراج ما فيها.

المشكلة المقيسة: جولة الرادار تجمع 243 خبراً فيخرج منها 2-3 فرص. والسبب
ليس ضعف التصنيف - بل أن البحث يعيد **صفحات فهارس** لا مناقصات بعينها:
«العطاءات المطروحة حالياً»، «مناقصات وأنظمة السعودية»، «arabtender.com».
والمصنّف يرفضها بحقّ لأن العنوان والمقتطف لا يحملان مناقصة واحدة محددة.

الفهرس يحمل عشرين مناقصة في جدوله، والمقتطف يعرض سطراً منه. فتحُ الصفحة
هو الفرق بين «موقع فيه مناقصات» و«هذه المناقصة تغلق بعد 23 يوماً».

مستويان بترتيب التكلفة:

  1. جلب نصّي مباشر - ثانية إلى ثانيتين، بلا نموذج ولا متصفح. يكفي
     للصفحات المُخدَّمة من الخادم، وهي أغلب بوابات المناقصات.
  2. وكيل التصفح (browser_use) - 20-60 ثانية ويقوده نموذج. لا يُستدعى
     إلا حين يعجز الأول، ولا يُفعَّل إلا بـ BROWSER_AGENT=1.

القاعدة: لا نستدعي الثقيل قبل أن يفشل الرخيص. زيارة ستّ صفحات بالوكيل
تكلّف أكثر من الجولة كلها.
"""
from __future__ import annotations

import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ما يدلّ على صفحة فهرس لا خبراً واحداً
PORTAL = re.compile(
    r"مناقصات|العطاءات|المطروحة|مزايدات|إعلانات|قائمة|"
    r"tenders?\b|bids?\b|procurement|opportunities|listings?",
    re.I)

# نطاقات لا تُفتح: منصات تواصل تعيد صفحة تسجيل دخول لا محتوى
SKIP = re.compile(r"(instagram|facebook|twitter|x\.com|linkedin|tiktok|"
                  r"youtube|t\.me|pinterest)", re.I)

MAX_PAGES = int(os.getenv("PORTAL_PAGES", "6"))
MIN_TEXT = 800          # أقل من هذا يعني صفحة لم تُخدَّم من الخادم
KEEP = 4000             # ما يُمرَّر للمصنّف من كل صفحة
TIMEOUT = int(os.getenv("PORTAL_TIMEOUT", "20"))

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def looks_like_portal(title: str, url: str = "") -> bool:
    if SKIP.search(url or ""):
        return False
    return bool(PORTAL.search(title or ""))


def fetch_text(url: str) -> str:
    """
    نصّ الصفحة بلا وسوم. يعيد "" عند الفشل - صفحة متعذّرة لا تُسقط جولة.

    الحذف يشمل script وstyle وnav وfooter: قوائم التنقّل تتكرر في كل
    صفحة وتزاحم الجدول الحقيقي على الحدّ المسموح.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                return ""
            html = r.read(600_000).decode("utf-8", "replace")
    except Exception:
        return ""

    html = re.sub(r"(?is)<(script|style|nav|footer|header|svg)[^>]*>.*?</\1>",
                  " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _heavy(url: str) -> str:
    """وكيل التصفح - للصفحات التي لا تُخدَّم من الخادم."""
    try:
        import browser_agent
        if not browser_agent.BROWSER_ENABLED:
            return ""
        return browser_agent.visit(
            f"افتح {url} واستخرج قائمة المناقصات المعروضة: لكل واحدة "
            f"العنوان والجهة وآخر موعد للتقديم إن ذُكر. أعدها نصاً مختصراً.")
    except Exception:
        return ""


def expand(events, limit: int | None = None, workers: int = 4) -> dict[str, str]:
    """
    يفتح صفحات الفهارس ويعيد {رابط: نصّ}.

    الترتيب محفوظ: نأخذ أوائل الفهارس كما رتّبها المتصل، فالأعلى ترجيحاً
    يُفتح أولاً وسقف الصفحات يقطع الذيل.
    """
    limit = MAX_PAGES if limit is None else limit
    urls, seen = [], set()
    for e in events:
        u = getattr(e, "url", "") or ""
        if not u or u in seen:
            continue
        if not looks_like_portal(getattr(e, "title", ""), u):
            continue
        seen.add(u)
        urls.append(u)
        if len(urls) >= limit:
            break

    if not urls:
        return {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pages = dict(zip(urls, pool.map(fetch_text, urls)))

    # الثقيل للعاجز عنه الرخيص فقط
    for u, t in list(pages.items()):
        if len(t) < MIN_TEXT:
            pages[u] = _heavy(u) or t

    return {u: t[:KEEP] for u, t in pages.items() if len(t) >= MIN_TEXT}


if __name__ == "__main__":
    import sys

    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    for u in sys.argv[1:] or ["https://arabtender.com/tenders/"]:
        t = fetch_text(u)
        print(f"\n{u}\n  {len(t):,} حرف")
        print(f"  {t[:400]}")
