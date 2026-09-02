"""
استيراد جهات وأشخاص من CSV.

المشكلة العملية: ملف العملاء يأتي من Excel أو من نظام قديم، وأعمدته
مسمّاة كما اتفق - «الشركة» أو «اسم الجهة» أو Company أو Account. وترميزه
على ويندوز العربي قد يكون cp1256 أو utf-8-sig لا utf-8 نظيفاً.

فالمستورد يخمّن، ولا يطلب من المستخدم إعادة تسمية أعمدته.

قاعدتان تحكمانه:

  · **لا يخترع**. عمود لم يُعرف يُترك، ولا يُملأ حقل بقيمة مستنتَجة.
    الاسم وحده إلزامي - وبدونه يُتخطّى السطر ويُذكر في التقرير.

  · **الدمج لا التكرار**. الجهة الموجودة تُثرى حقولها الفارغة ولا
    تُستنسخ: ملف فيه «وزارة الصحة» وقاعدةٌ فيها «وزارة الصحة - سلطنة
    عُمان» جهةٌ واحدة، والتطبيع العربي في crm.upsert_company يوحّدهما.

يُشغَّل جافّاً أولاً (`--dry`) فيعرض ما سيفعله قبل أن يمسّ القاعدة.
"""
from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import crm

# أسماء الأعمدة المحتملة لكل حقل، عربيةً وإنجليزيةً.
# الترتيب مهم: الأدقّ أولاً كي لا يلتقط "اسم" عمودَ اسم الشخص للشركة.
FIELDS: dict[str, tuple[str, ...]] = {
    # "title" هنا لا في role: تصديرات خرائط جوجل تسمّي اسم المنشأة
    # `title`، وملف أذربيجان الأول ربطه بالمنصب فسقطت 268 صفاً كلها.
    # وفي تصدير CRM حقيقي يوجد عمود company صريح فيفوز بالمطابقة التامة
    # ويبقى title للمنصب - الترتيب يحلّ الحالتين بلا تعارض.
    "company": ("اسم الشركة", "اسم الجهة", "الجهة", "الشركة", "المؤسسة",
                "العميل", "company", "company name", "account", "organization",
                "organisation", "client", "customer", "vendor", "entity",
                "title", "business", "business name", "place", "place name"),
    "contact": ("اسم المسؤول", "الشخص", "جهة الاتصال", "المسؤول", "الاسم",
                "contact", "contact name", "person", "full name", "name"),
    "role":    ("المنصب", "الوظيفة", "الصفة", "role", "title", "position",
                "job title"),
    "email":   ("البريد", "الايميل", "الإيميل", "البريد الالكتروني",
                "البريد الإلكتروني", "email", "emails", "e-mail", "mail",
                "email address", "e mail"),
    "phone":   ("الهاتف", "الجوال", "رقم الهاتف", "التلفون", "phone",
                "mobile", "tel", "telephone", "phone number"),
    "sector":  ("القطاع", "المجال", "النشاط", "sector", "industry",
                "vertical", "category"),
    "country": ("الدولة", "البلد", "الموقع", "المدينة", "country", "location",
                "city", "region"),
    "website": ("الموقع الالكتروني", "الموقع الإلكتروني", "الرابط",
                "website", "url", "site", "web"),
    "address": ("العنوان", "عنوان", "address", "complete address",
                "street", "full address"),
    "notes":   ("ملاحظات", "ملاحظة", "notes", "note", "comment", "remarks",
                "description", "descriptions", "about"),
}

# ترميزات ويندوز العربي الشائعة، بترتيب الاحتمال
ENCODINGS = ("utf-8-sig", "utf-8", "cp1256", "windows-1256", "cp1252", "latin-1")

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _clean(s: str) -> str:
    return " ".join(str(s or "").replace("‏", "").replace("‎", "").split())


def _key(header: str) -> str:
    """يطبّع اسم العمود للمطابقة: حروف صغيرة، بلا رموز، بلا 'ال' البادئة."""
    h = _clean(header).lower()
    h = re.sub(r"[_\-/\\.:*#()\[\]]+", " ", h)
    return " ".join(h.split())


def guess_columns(headers: list[str]) -> dict[str, str]:
    """
    يربط أعمدة الملف بحقولنا.

    المطابقة على مرحلتين: تطابق تام أولاً، ثم احتواء - فعمود اسمه
    «البريد الالكتروني للمسؤول» يُلتقط بالثانية. والحقل يُحجز لأول عمود
    يطابقه فلا يسرقه عمود لاحق.
    """
    keys = {h: _key(h) for h in headers}
    out: dict[str, str] = {}
    taken: set[str] = set()

    for stage in ("exact", "contains"):
        for field, names in FIELDS.items():
            if field in out:
                continue
            for h, k in keys.items():
                if h in taken or not k:
                    continue
                hit = (k in names) if stage == "exact" else \
                      any(n in k or k in n for n in names)
                if hit:
                    out[field] = h
                    taken.add(h)
                    break
    return out


def read_rows(path: str | Path) -> tuple[list[dict], str]:
    """يقرأ الملف بأول ترميز ينجح، ويكتشف الفاصل (فاصلة أو فاصلة منقوطة)."""
    raw = Path(path).read_bytes()
    text = err = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError as e:
            err = e
    if text is None:
        raise UnicodeDecodeError("csv", raw[:16], 0, 1, str(err))

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    return rows, enc


def preview(path: str | Path, limit: int = 5) -> dict:
    """ما سيحدث لو استوردنا - بلا لمس القاعدة."""
    rows, enc = read_rows(path)
    if not rows:
        return {"error": "الملف فارغ أو بلا ترويسة"}

    headers = list(rows[0].keys())
    cols = guess_columns(headers)
    unmapped = [h for h in headers if h not in cols.values()]

    samples = []
    for r in rows[:limit]:
        samples.append({f: _clean(r.get(c, "")) for f, c in cols.items()})

    named = sum(1 for r in rows if _clean(r.get(cols.get("company", ""), "")))
    return {
        "encoding": enc,
        "rows": len(rows),
        "mapped": cols,
        "unmapped": unmapped,
        "with_company": named,
        "skipped": len(rows) - named,
        "sample": samples,
    }


def run(path: str | Path, dry: bool = False, db_path: str = crm.DB) -> dict:
    rows, enc = read_rows(path)
    if not rows:
        return {"error": "الملف فارغ"}

    cols = guess_columns(list(rows[0].keys()))
    if "company" not in cols:
        return {"error": "لم أتعرّف على عمود اسم الشركة. "
                         f"الأعمدة الموجودة: {', '.join(rows[0].keys())}"}

    stats = {"rows": len(rows), "companies": 0, "contacts": 0,
             "skipped": 0, "encoding": enc, "problems": []}

    for i, r in enumerate(rows, 2):          # 2 = أول سطر بيانات في Excel
        name = _clean(r.get(cols["company"], ""))
        if not name:
            stats["skipped"] += 1
            if len(stats["problems"]) < 10:
                stats["problems"].append(f"سطر {i}: بلا اسم جهة")
            continue

        # الخانة قد تحمل أكثر من بريد («pr@x.com, hr@x.com») - نأخذ
        # الأول الصالح. fullmatch كانت ترفض الخانة كلها فيضيع عنوان سليم.
        raw_mail = _clean(r.get(cols.get("email", ""), ""))
        found = _EMAIL.search(raw_mail) if raw_mail else None
        email = found.group(0) if found else ""
        if raw_mail and not email:
            # لا نرفض السطر: الاسم صالح والبريد وحده مشكوك فيه
            if len(stats["problems"]) < 10:
                stats["problems"].append(f"سطر {i}: بريد غير صالح «{raw_mail[:40]}»")

        if dry:
            stats["companies"] += 1
            if email or _clean(r.get(cols.get("contact", ""), "")):
                stats["contacts"] += 1
            continue

        addr = _clean(r.get(cols.get("address", ""), ""))
        cid = crm.upsert_company(
            name,
            sector=_clean(r.get(cols.get("sector", ""), "")),
            country=_clean(r.get(cols.get("country", ""), "")) or addr,
            website=_clean(r.get(cols.get("website", ""), "")),
            path=db_path)
        if not cid:
            stats["skipped"] += 1
            continue
        stats["companies"] += 1

        person = _clean(r.get(cols.get("contact", ""), ""))
        phone = _clean(r.get(cols.get("phone", ""), ""))
        if person or email or phone:
            crm.add_contact(cid, name=person,
                            role=_clean(r.get(cols.get("role", ""), "")),
                            email=email, phone=phone, path=db_path)
            stats["contacts"] += 1

    return stats


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    import json

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv

    if not args:
        print("الاستخدام:")
        print("  python crm_import.py ملف.csv --dry   # معاينة بلا كتابة")
        print("  python crm_import.py ملف.csv         # استيراد فعلي")
        sys.exit(0)

    if dry:
        print(json.dumps(preview(args[0]), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(run(args[0]), ensure_ascii=False, indent=1))
