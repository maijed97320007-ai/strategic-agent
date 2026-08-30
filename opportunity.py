"""
كاشف الفرص - يحوّل الأداة من «تجيب حين أسأل» إلى «تخبرني قبل أن أسأل».

    مصادر → جمع → تطبيع → كشف الحدث → مطابقة الملف الشخصي
          → تسجيل → فريق أحمر → تنبيه

توزيع المسؤولية (قاعدة: لا تجعل النموذج مسؤولاً عن كل شيء):
    الكود  : الجدولة، الجلب، التحليل، التخزين، التسجيل، العتبات، التكرار
    النموذج: التصنيف، استخراج الكيانات، الحكم على الملاءمة، النقد

الدرجة مركّبة من سبعة عوامل مرجّحة. الملاءمة والأدلة يأتيان من النموذج،
والباقي يُحسب في الكود من الملف الشخصي والتاريخ - فلا يستطيع النموذج
تضخيم درجة بمجرد التفاؤل.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# مسار قاعدة المعرفة من memory: السلسلة النسبية تُحلّ حسب مجلد التشغيل،
# فتشغيل الـEXE من مجلد آخر يفتح قاعدة فارغة بصمت.
try:
    from memory import DB_DEFAULT as _DB
except ImportError:
    _DB = "knowledge.db"

# أنواع الأحداث المرصودة
EVENT_TYPES = [
    "مناقصة جديدة", "مشروع جديد", "شركة جديدة", "منتج جديد",
    "فوز منافس", "تغيّر سعر", "تغيّر تنظيمي", "شراكة",
    "توسّع", "توظيف", "عقد", "استحواذ",
]

# عتبات القرار
HIGH, INVESTIGATE, WATCH = 85, 70, 50

# النطاق حين لا يحدّده profile.json
DEFAULT_REGIONS = ("الخليج", "السعودية", "الإمارات", "قطر", "الكويت",
                   "شمال أفريقيا", "مصر", "الأردن")

# صلاحية ذاكرة الرادار. أطول من الافتراضي (6) عمداً: المناقصة تبقى
# مفتوحة أسابيع، والجولة كل ست ساعات بـ33 استعلاماً تستنزف الحصة
# المجانية في تسعة عشر يوماً. بـ24 ساعة تُنفَّذ جولة حقيقية من كل أربع.
RADAR_TTL = float(os.getenv("RADAR_CACHE_HOURS", "24"))

# دول تُسأل بالإنجليزية: أسواق تحلية كبرى لا تُوثّق مناقصاتها بالعربية
EN_COUNTRIES = ("Saudi Arabia", "UAE", "Egypt", "Morocco", "India",
                "Spain", "Australia", "Chile")


def _root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent


def load_profile(path: str | None = None) -> dict:
    p = Path(path or os.getenv("PROFILE", _root() / "profile.json"))
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ======================
# التخزين
# ======================
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    ext_id      TEXT UNIQUE,
    title       TEXT NOT NULL,
    description TEXT,
    event_type  TEXT,
    company     TEXT,
    sector      TEXT,
    location    TEXT,
    event_date  TEXT,
    url         TEXT,
    source_id   TEXT,
    seen_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_type ON events(event_type);

CREATE TABLE IF NOT EXISTS opportunities (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER REFERENCES events(id),
    title       TEXT NOT NULL,
    score       INTEGER NOT NULL,
    band        TEXT NOT NULL,
    factors     TEXT,
    evidence    TEXT,
    why         TEXT,
    action      TEXT,
    red_team    TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_op_score ON opportunities(score DESC);
"""


def _db(path: str = _DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ======================
# النماذج
# ======================
@dataclass
class Event:
    title: str
    description: str = ""
    url: str = ""
    source_id: str = ""
    event_type: str = ""
    company: str = ""
    sector: str = ""
    location: str = ""
    event_date: str = ""

    @property
    def ext_id(self) -> str:
        import hashlib
        return hashlib.sha256(
            f"{self.url}|{self.title}".encode("utf-8")).hexdigest()[:20]


@dataclass
class Scored:
    event: Event
    score: int
    band: str
    factors: dict[str, int] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    why: str = ""
    action: str = ""
    red_team: str = ""


# ======================
# 1) الجمع
# ======================
def collect(profile: dict, extra_queries: list[str] | None = None,
            per_query: int = 8) -> "object":
    """
    يجمع أخباراً حديثة تخص كلمات الملف الشخصي، ويعيد (سجل مصادر، أحداث خام).

    البحث مبني على كلمات الملف لا على سؤال المستخدم - هذا جوهر الفرق:
    النظام يراقب مجاله بلا انتظار.
    """
    import main
    import sources as S

    reg = S.Registry()
    raw: list[Event] = []

    tool = main._search_tool()
    if tool is None:
        return reg, raw

    country = profile.get("country", "")
    kws = profile.get("keywords", [])[:6]

    # ── النطاق الجغرافي ──
    # الافتراضي عالمي لا محلي: حصر البحث ببلد واحد كان يجعل الرادار يرى
    # مناقصات السلطنة وحدها بينما أكبر مشاريع التحلية تُطرح في الخليج
    # وشمال أفريقيا وآسيا. البلد يبقى أولاً لأن القرب ميزة تنفيذية
    # حقيقية، لكنه لم يعد سقفاً.
    regions = profile.get("regions") or list(DEFAULT_REGIONS)
    regions = [r for r in dict.fromkeys([country] + list(regions)) if r]
    span = int(os.getenv("RADAR_REGIONS", "6"))

    def _arabic(k: str) -> bool:
        return any("؀" <= c <= "ۿ" for c in k)

    ar_kws = [k for k in kws if _arabic(k)]

    # الفحص بالأبجدية لا بـ«ليست في أول ستّ»: القائمة الأولى مقصوصة عند
    # ستّ كلمات، فكانت «مياه جوفية» و«ملوحة» تسقط خارجها وتُعامَل
    # كإنجليزية - فتخرج استعلامات مثل «مياه جوفية tender 2026».
    # وشرط أربعة أحرف يُسقط الاختصارات: «RO tender» تعيد رومانيا.
    en_kws = [k for k in profile.get("keywords", [])
              if not _arabic(k) and len(k) >= 4]

    year = date.today().year
    queries = list(extra_queries or [])

    # ── العربية: الخليج والمشرق ──
    for r in regions[:span]:
        queries += [f"{k} {r} مناقصة" for k in ar_kws[:2]]
        queries += [f"{k} {r} مشروع جديد" for k in ar_kws[:1]]

    # ── الإنجليزية: البوابات الدولية ونشرات الترسية ──
    # لا تُنشر بالعربية أصلاً، وهي مصدر أغلب المشاريع خارج المنطقة.
    for k in (en_kws or ["desalination", "water treatment"])[:2]:
        queries += [f"{k} tender {year}",
                    f"{k} project awarded {year}",
                    f"{k} plant contract international tender"]
    for c in EN_COUNTRIES[:span]:
        queries.append(f"{(en_kws or ['desalination'])[0]} tender {c} {year}")

    # ── الفرنسية: السوق المغاربي ──
    # تونس والمغرب والجزائر توثّق مناقصاتها بالفرنسية، ولا تظهر في البحث
    # العربي ولا الإنجليزي. ثلاثة استعلامات تفتح ثلاث دول.
    if os.getenv("RADAR_FRENCH", "1").strip().lower() not in ("0", "false", "no"):
        for c in ("Maroc", "Tunisie", "Algérie"):
            queries.append(f"appel d'offres dessalement eau {c} {year}")

    import cache

    for q in queries:
        res = cache.cached_search(tool, q, ttl_hours=RADAR_TTL)
        if res is None:
            continue
        organic = (res or {}).get("organic", []) if isinstance(res, dict) else []
        for it in organic[:per_query]:
            if not it.get("link"):
                continue
            src = reg.add(it.get("title", ""), it["link"], it.get("snippet", ""), q)
            raw.append(Event(title=it.get("title", ""),
                             description=it.get("snippet", ""),
                             url=it["link"], source_id=src.id,
                             event_date=str(it.get("date", ""))))
    return reg, raw


# ======================
# 3) التسجيل (كود)
# ======================
WEIGHTS = {"fit": 0.30, "market": 0.15, "timing": 0.15,
           "profit": 0.15, "competition": 0.10, "risk": 0.10,
           "evidence": 0.05}


def score_one(d: dict, profile: dict, n_evidence: int) -> tuple[int, dict]:
    """
    يحسب الدرجة النهائية من سبعة عوامل مرجّحة.

    التسجيل في الكود لا في النموذج عمداً: النموذج يعطي إشارات خام (ملاءمة،
    سوق، توقيت) ونحن نرجّحها ونعكس ما يجب عكسه. لو تركنا الدرجة له لأعطى
    كل فرصة 90 - وقد رُصد هذا السلوك في وكلاء آخرين بالمشروع.
    """
    g = lambda k: max(0, min(100, int(d.get(k) or 0)))

    f = {
        "fit": g("fit"),
        "market": g("market"),
        "timing": g("timing"),
        "profit": g("profit"),
        "competition": 100 - g("competition"),   # الأعلى أسوأ فنعكسه
        "risk": 100 - g("risk"),
        "evidence": min(100, n_evidence * 40),   # مصدران يكفيان للامتلاء
    }

    total = sum(f[k] * w for k, w in WEIGHTS.items())

    # عقوبات من الملف الشخصي - يعرفها الكود ولا يعرفها النموذج
    text = f"{d.get('idea','')} {d.get('detail','')}".lower()
    for bad in profile.get("avoid", []):
        toks = [t for t in re.split(r"\s+", bad.lower()) if len(t) > 3][:3]
        if toks and sum(t in text for t in toks) >= 2:
            total *= 0.6                          # يمسّ ما استبعدته صراحة
            break
    if not n_evidence:
        total *= 0.5                              # فرصة بلا سند ليست فرصة

    return max(0, min(100, round(total))), f


def band_of(score: int) -> str:
    if score >= HIGH:
        return "HIGH"
    if score >= INVESTIGATE:
        return "INVESTIGATE"
    if score >= WATCH:
        return "WATCH"
    return "IGNORE"
