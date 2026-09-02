"""
صيد مواعيد الإغلاق للفرص التي وصلت بلا موعد.

المشكلة: الفهرس يعرض عنوان المناقصة وجهتها، والموعد داخل صفحتها المفردة.
آخر جولة أعطت ثلاثة مواعيد من خمس وعشرين فرصة - والموعد هو ما يحوّل
«فرصة مثيرة» إلى «اشتغل عليها هذا الأسبوع».

لماذا لا نفتح كل مناقصة بوكيل التصفح؟ مئة ثانية للصفحة × خمس وعشرين =
أربعون دقيقة للجولة. فالمسار هنا أرخص بعشرين ضعفاً:

    بحث موجَّه بعنوان المناقصة + كلمة الموعد   (~2 ثانية)
      ↓ إن ظهر تاريخ في المقتطف نفسه، انتهينا
    جلب أفضل صفحتين نصّياً                      (~2 ثانية)
      ↓ استخراج التاريخ المجاور لعبارة الموعد
    تحقّق: تاريخ صالح وفي نافذة معقولة

النافذة المعقولة تمنع أشيع خطأ: الصفحة مليئة بتواريخ - تاريخ النشر،
سنة التأسيس، أرشيف - وأخذ أوّل تاريخ يُنتج موعداً في 2019. فنشترط
**قرباً من عبارة الموعد** و**وقوعاً في المستقبل القريب**.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, timedelta

# عبارات تسبق الموعد أو تليه
_CUE = (r"آخر\s*موعد|الموعد\s*النهائي|تاريخ\s*الإقفال|موعد\s*الإقفال|"
        r"آخر\s*أجل|انتهاء\s*التقديم|إقفال\s*المناقصة|تاريخ\s*الإغلاق|"
        r"deadline|closing\s*date|submission\s*deadline|last\s*date|"
        r"date\s*limite|clôture")

# صيغ التاريخ الشائعة عربياً وإنجليزياً
_DATE = (r"(\d{4}-\d{1,2}-\d{1,2})|"
         r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})|"
         r"(\d{1,2}\s+(?:يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|"
         r"أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر)\s+\d{4})|"
         r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
         r"[a-z]*\.?\s+\d{4})")

# نافذة القبول: الماضي القريب مسموح (موعد فات قبل أيام يفيد أنها أُغلقت)
PAST_DAYS = int(os.getenv("DEADLINE_PAST_DAYS", "14"))
FUTURE_DAYS = int(os.getenv("DEADLINE_FUTURE_DAYS", "400"))

# كم فرصة نطارد لها موعداً في الجولة
HUNT_TOP = int(os.getenv("DEADLINE_HUNT", "8"))

_AR_MONTH = {"يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4,
             "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
             "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11,
             "ديسمبر": 12}
_EN_MONTH = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(raw: str) -> str:
    """يحوّل أي صيغة مدعومة إلى ISO، أو "" إن تعذّر."""
    t = " ".join((raw or "").split())
    if not t:
        return ""

    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        y, mo, d = map(int, m.groups())
    elif m := re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t):
        # يوم/شهر/سنة هو الشائع عربياً؛ نصحّح إن تجاوز الأول اثني عشر
        a, b, y = map(int, m.groups())
        d, mo = (a, b) if a > 12 or b <= 12 else (b, a)
    elif m := re.fullmatch(r"(\d{1,2})\s+(\S+)\s+(\d{4})", t):
        d, name, y = int(m.group(1)), m.group(2).lower().strip("."), int(m.group(3))
        mo = _AR_MONTH.get(m.group(2)) or _EN_MONTH.get(name[:3], 0)
    else:
        return ""

    try:
        return date(y, mo, d).isoformat()
    except (ValueError, TypeError):
        return ""


def plausible(iso: str) -> bool:
    """تاريخ في نافذة معقولة - يمنع أخذ سنة التأسيس أو أرشيف قديم."""
    if not iso:
        return False
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return False
    delta = (d - date.today()).days
    return -PAST_DAYS <= delta <= FUTURE_DAYS


def extract(text: str, window: int = 140) -> str:
    """
    أقرب تاريخ صالح إلى عبارة موعد.

    نبحث حول العبارة لا في الصفحة كلها: صفحة المناقصة فيها تاريخ نشر
    وتاريخ تحديث وسنوات في الأرشيف، وأخذ أوّل تاريخ يعطي 2019.
    """
    if not text:
        return ""
    best = ""
    for cue in re.finditer(_CUE, text, re.I):
        a = max(0, cue.start() - 40)
        b = min(len(text), cue.end() + window)
        for m in re.finditer(_DATE, text[a:b]):
            iso = parse_date(next(g for g in m.groups() if g))
            if plausible(iso) and (not best or iso < best):
                best = iso          # الأقرب زمنياً أرجح أن يكون الإقفال
    return best


def hunt_one(title: str, company: str = "", url: str = "") -> dict:
    """
    يطارد موعد مناقصة واحدة.

    الترتيب بالتكلفة: صفحة المصدر أولاً إن كانت مفردة (مجانية عملياً)،
    ثم بحث موجَّه، ثم جلب أفضل نتيجتين.
    """
    import portals

    # 1) صفحة المصدر نفسها - قد تكون صفحة المناقصة لا الفهرس
    if url and not portals.looks_like_portal("", url):
        if iso := extract(portals.fetch_text(url)):
            return {"deadline": iso, "via": "صفحة المصدر", "url": url}

    # 2) بحث موجَّه بعنوان المناقصة
    try:
        import opportunity as O
    except ImportError:
        return {"deadline": ""}

    words = " ".join((title or "").split()[:9])
    q = f'{words} {company} "آخر موعد" OR "تاريخ الإقفال" OR deadline'.strip()
    res = O._serper(q, "", 6) or {}
    organic = res.get("organic") or []

    # المقتطف قد يحمل التاريخ فنوفّر جلب الصفحة
    for it in organic:
        blob = f"{it.get('title','')} {it.get('snippet','')}"
        if iso := extract(blob):
            return {"deadline": iso, "via": "مقتطف البحث",
                    "url": it.get("link", "")}

    # 3) جلب أفضل نتيجتين
    for it in organic[:2]:
        link = it.get("link") or ""
        if not link:
            continue
        if iso := extract(portals.fetch_text(link)):
            return {"deadline": iso, "via": "صفحة المناقصة", "url": link}

    return {"deadline": ""}


def hunt(limit: int | None = None, path: str | None = None) -> dict:
    """
    يملأ المواعيد الناقصة لأعلى الفرص درجةً.

    نبدأ بالأعلى: الموعد يخدم القرار، والقرار يُتّخذ على الفرص الجادّة -
    ومطاردة موعد فرصةٍ بدرجة 40 إنفاقٌ بلا عائد.
    """
    import opportunity_run as opp

    limit = HUNT_TOP if limit is None else limit
    db_path = path or opp._DB
    rows = [r for r in opp.recent(limit=60, db=db_path)
            if not (r.get("deadline") or "").strip()][:limit]

    found, misses = [], 0
    for r in rows:
        got = hunt_one(r["title"], r.get("company") or "", r.get("url") or "")
        if not got.get("deadline"):
            misses += 1
            continue

        con = opp._db(db_path)
        con.execute("UPDATE opportunities SET deadline=? WHERE id=?",
                    (got["deadline"], r["id"]))
        con.commit()
        con.close()

        left = (date.fromisoformat(got["deadline"]) - date.today()).days
        found.append({"id": r["id"], "title": r["title"][:52],
                      "deadline": got["deadline"], "days_left": left,
                      "via": got["via"]})

    return {"checked": len(rows), "found": len(found), "missed": misses,
            "results": found}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    import main  # يحمّل .env  # noqa: F401

    n = int(sys.argv[1]) if len(sys.argv) > 1 else HUNT_TOP
    out = hunt(limit=n)
    print(f"فُحصت {out['checked']} فرصة · وُجد {out['found']} موعد\n")
    for r in out["results"]:
        print(f"  {r['deadline']}  (بقي {r['days_left']} يوم)  {r['via']}")
        print(f"      {r['title']}")
