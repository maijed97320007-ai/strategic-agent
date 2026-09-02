"""
إدارة العلاقات: شركات، أشخاص، صفقات، مراسلات.

لماذا داخل قاعدة المعرفة لا في نظام منفصل؟ لأن الفرصة والجهة والمناقصة
موجودة أصلاً في `events` و`opportunities` - ثمانون جهة بأسمائها. نظام
CRM خارجي يعني نسخ البيانات ومزامنتها وتباعدها، بينما الربط هنا يجعل
«رادار رصد مناقصة» و«فتحتُ صفقة مع هذه الجهة» حدثين في سجلّ واحد.

المراحل مختارة لطبيعة المناقصات لا لدورة بيع SaaS:

    مرصودة → مؤهَّلة → تواصَلنا → مستندات → عرض مقدَّم → فوز | خسارة

`مستندات` مرحلة قائمة بذاتها لأن كراسة المناقصة تُشترى وتُدرس قبل أي
عرض، وقد تنتهي الصفقة عندها حين يتبيّن شرطٌ يستبعدك.

توحيد الأسماء عبر `memory.normalize`: القاعدة فيها «وزارة الصحة - سلطنة
عُمان» و«وزارة الصحة - سلطنة عمان» - جهة واحدة بهمزتين مختلفتين، وعدّهما
اثنتين يجعل تاريخ التعامل معها مجزّأً.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from memory import DB_DEFAULT as DB
except ImportError:
    DB = "knowledge.db"

# مراحل الصفقة بالترتيب - الفهرس هو التقدّم
STAGES = ["مرصودة", "مؤهَّلة", "تواصَلنا", "مستندات", "عرض مقدَّم",
          "فوز", "خسارة"]
CLOSED = {"فوز", "خسارة"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_companies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    norm        TEXT NOT NULL UNIQUE,   -- الاسم بعد التطبيع العربي
    sector      TEXT,
    country     TEXT,
    website     TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_contacts (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER REFERENCES crm_companies(id),
    name        TEXT,
    role        TEXT,
    email       TEXT,
    phone       TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contact_co ON crm_contacts(company_id);

CREATE TABLE IF NOT EXISTS crm_deals (
    id             INTEGER PRIMARY KEY,
    opportunity_id INTEGER REFERENCES opportunities(id),
    company_id     INTEGER REFERENCES crm_companies(id),
    title          TEXT NOT NULL,
    stage          TEXT NOT NULL DEFAULT 'مرصودة',
    value_omr      REAL,
    deadline       TEXT,
    next_action    TEXT,
    next_at        TEXT,
    outcome_note   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deal_stage ON crm_deals(stage);
CREATE INDEX IF NOT EXISTS idx_deal_next  ON crm_deals(next_at);

CREATE TABLE IF NOT EXISTS crm_messages (
    id          INTEGER PRIMARY KEY,
    deal_id     INTEGER REFERENCES crm_deals(id),
    direction   TEXT NOT NULL,          -- out | in
    kind        TEXT NOT NULL,          -- first | followup | reply | note
    subject     TEXT,
    body        TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | sent
    to_addr     TEXT,
    created_at  TEXT NOT NULL,
    sent_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_deal ON crm_messages(deal_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


# حشو لا يميّز جهة عن أخرى: صفة الكيان، وحروف الربط، ونسبة البلد.
# «وزارة الصحة - سلطنة عمان» و«وزارة الصحة في سلطنة عمان» و«وزارة الصحة
# العمانية» وزارةٌ واحدة، وقد صارت ثلاثة سجلات في أول استيراد فعلي.
_STOP = ("شركه", "شركة", "مؤسسه", "مؤسسة", "مجموعه", "مجموعة", "وزاره",
         "وزارة", "هيئه", "هيئة", "مصنع", "مكتب", "سلطنه", "سلطنة",
         "المحدوده", "المحدودة", "ذ م م", "ش م ع", "ش م م",
         "company", "co", "ltd", "llc", "inc", "corp", "group", "est",
         "في", "من", "و", "the", "of", "for")

# نسبة البلد ليست جزءاً من الاسم: «العمانية» و«عمان» و«السعودية» تصف
# الموقع، وهو عمود مستقل عندنا.
_GEO = ("عمان", "عماني", "عمانيه", "العمانيه", "السعوديه", "سعودي",
        "الاماراتيه", "اماراتي", "البحرين", "الكويت", "قطر", "مصر",
        "oman", "omani", "saudi", "uae", "emirates", "bahrain", "kuwait")


def _norm(name: str) -> str:
    """
    مفتاح المطابقة: تطبيع عربي، ثم إسقاط الحشو والنسبة الجغرافية.

    الأقواس تُحذف أولاً: الرادار يلحق بالاسم مرجعاً - «وزارة الصحة
    (Ref Id: 64716274)» - وهو يخصّ المناقصة لا الجهة.
    """
    import re as _re
    t = _re.sub(r"[\(\[\{].*?[\)\]\}]", " ", name or "")
    try:
        from memory import normalize
        t = normalize(t)
    except Exception:
        t = " ".join(t.split()).lower()

    words = [w for w in t.split() if w not in _STOP and w not in _GEO]
    return " ".join(words) or t


def _fuzzy_match(con, norm: str, raw: str, threshold: float = 0.88) -> int | None:
    """
    يبحث عن جهة قائمة تكاد تطابق الاسم.

    التطبيع وحده لا يكفي: «وزاره صحه» و«وزاره صحه عامه» يختلفان حرفياً
    ويشتركان في الجهة. لكن التشابه وحده يُفرط - «المياه المتقدمة»
    و«المياه المتحدة» تشابههما 0.9 وهما شركتان. فنشترط الاثنين:
    تشابهاً عالياً **و** اتفاق أول كلمة دالّة، وهي جوهر الاسم عادةً.
    """
    if not norm:
        return None
    try:
        from memory import similarity
    except Exception:
        return None

    head = norm.split()[0] if norm.split() else ""
    best, score = None, 0.0
    for r in con.execute("SELECT id, norm FROM crm_companies"):
        other = r["norm"] or ""
        if not other or (head and not other.startswith(head)):
            continue
        s = similarity(norm, other)
        if s > score:
            best, score = r["id"], s
    return best if score >= threshold else None


# ======================
# الشركات
# ======================
def upsert_company(name: str, sector: str = "", country: str = "",
                   website: str = "", path: str = DB) -> int:
    """يضيف جهة أو يعيد القائمة. التطبيع يمنع تكرار «عُمان» و«عمان»."""
    name = " ".join((name or "").split())
    # الأسماء النائبة ليست جهات: المصنّف يكتب «غير محدد» و«غير متوفر»
    # وأحياناً بينهما قوس - «غير محددة (الأردن)» - فتدخل القاعدة كشركة
    # وتُدمَج بغيرها من النوع نفسه، ثم تُراسَل.
    import re as _re
    bare = _re.sub(r"[\(\[].*?[\)\]]", " ", name)
    bare = " ".join(bare.split())
    if not name or _re.match(r"^(غير\s+(محدد|محددة|متوفر|متوفرة|معروف)|"
                             r"unknown|n/?a|none|-+)$", bare, _re.I):
        return 0
    n = _norm(name)
    con = db(path)
    row = con.execute("SELECT id FROM crm_companies WHERE norm=?", (n,)).fetchone()
    if not row and (fid := _fuzzy_match(con, n, name)):
        row = {"id": fid}
    if row:
        # نُثري الحقول الفارغة دون أن نطمس ما أُدخل يدوياً
        con.execute(
            "UPDATE crm_companies SET sector=COALESCE(NULLIF(sector,''),?),"
            " country=COALESCE(NULLIF(country,''),?),"
            " website=COALESCE(NULLIF(website,''),?) WHERE id=?",
            (sector, country, website, row["id"]))
        con.commit()
        cid = row["id"]
    else:
        cur = con.execute(
            "INSERT INTO crm_companies(name,norm,sector,country,website,created_at)"
            " VALUES(?,?,?,?,?,?)", (name, n, sector, country, website, _now()))
        con.commit()
        cid = cur.lastrowid
    con.close()
    return cid


def merge_company(keep: int, drop: int, path: str = DB) -> None:
    """يدمج سجلّين: ينقل الأشخاص والصفقات ثم يحذف الزائد."""
    if keep == drop:
        return
    con = db(path)
    con.execute("UPDATE crm_contacts SET company_id=? WHERE company_id=?", (keep, drop))
    con.execute("UPDATE crm_deals    SET company_id=? WHERE company_id=?", (keep, drop))
    con.execute(
        "UPDATE crm_companies SET"
        " sector=COALESCE(NULLIF(sector,''),(SELECT sector FROM crm_companies WHERE id=?)),"
        " country=COALESCE(NULLIF(country,''),(SELECT country FROM crm_companies WHERE id=?)),"
        " website=COALESCE(NULLIF(website,''),(SELECT website FROM crm_companies WHERE id=?))"
        " WHERE id=?", (drop, drop, drop, keep))
    con.execute("DELETE FROM crm_companies WHERE id=?", (drop,))
    con.commit()
    con.close()


def dedupe(threshold: float = 0.88, path: str = DB) -> list[tuple[str, str]]:
    """
    يدمج الجهات المكرّرة الموجودة مسبقاً.

    الوقاية وحدها لا تكفي: القاعدة امتلأت قبل إصلاح التطبيع، وفيها وزارة
    الصحة بأربعة سجلات. وأربعة سجلات لجهة واحدة تعني أربع رسائل إلى
    الموظف نفسه - وهو ضرر لا يُصلَح باعتذار.

    نُبقي الأقدم: هو غالباً الأصل الذي تعلّقت به الصفقات.
    """
    try:
        from memory import similarity
    except ImportError:
        return []

    # إعادة حساب المفاتيح أولاً: السجلات المُدخَلة قبل تحسين التطبيع تحمل
    # مفتاحاً قديماً، فالمقارنة به تُبقي «وزارة الصحة (Ref Id: 64716274)»
    # منفصلة عن «وزارة الصحة» رغم أن التطبيع الحالي يوحّدهما.
    con = db(path)
    current = [dict(r) for r in con.execute(
        "SELECT id, name, norm FROM crm_companies ORDER BY id")]

    # اصطدام المفتاح الجديد بسجلّ قائم **هو** دليل التكرار لا خطأ يُتجاهل:
    # ابتلاعه في المحاولة الأولى أبقى «وزارة الصحة (Ref Id: …)» منفصلة عن
    # «وزارة الصحة» رغم أن مفتاحيهما متطابقان حرفياً بعد التطبيع.
    owner: dict[str, int] = {}
    collisions: list[tuple[int, int]] = []
    updates: list[tuple[str, int]] = []

    for r in current:
        fresh = _norm(r["name"]) or r["norm"]
        if fresh in owner:
            collisions.append((owner[fresh], r["id"]))     # (نُبقي، نحذف)
        else:
            owner[fresh] = r["id"]
            if fresh != r["norm"]:
                updates.append((fresh, r["id"]))

    for fresh, rid in updates:
        con.execute("UPDATE crm_companies SET norm=? WHERE id=?", (fresh, rid))
    con.commit()
    con.close()

    merged_pre: list[tuple[str, str]] = []
    names = {r["id"]: r["name"] for r in current}
    for keep, drop in collisions:
        merge_company(keep, drop, path)
        merged_pre.append((names.get(keep, ""), names.get(drop, "")))

    con = db(path)
    rows = [dict(r) for r in con.execute(
        "SELECT id, name, norm FROM crm_companies ORDER BY id")]
    con.close()

    merged: list[tuple[str, str]] = list(merged_pre)
    gone: set[int] = set()
    for i, a in enumerate(rows):
        if a["id"] in gone:
            continue
        for b in rows[i + 1:]:
            if b["id"] in gone or not a["norm"] or not b["norm"]:
                continue
            ha, hb = a["norm"].split()[:1], b["norm"].split()[:1]
            if ha != hb:
                continue
            if similarity(a["norm"], b["norm"]) >= threshold:
                merge_company(a["id"], b["id"], path)
                merged.append((a["name"], b["name"]))
                gone.add(b["id"])
    return merged


def contacted(company_id: int, path: str = DB) -> dict | None:
    """
    آخر رسالة صادرة إلى هذه الجهة - أياً كانت الصفقة.

    الحارس على مستوى **الجهة** لا الصفقة: الرادار يرصد للجهة الواحدة عدة
    مناقصات، وإرسال رسالة تعريف لكل واحدة يجعل موظف المشتريات يتلقّى ثلاث
    رسائل متطابقة في أسبوع - وهذا يُفقد الثقة لا يبنيها.
    """
    if not company_id:
        return None
    con = db(path)
    row = con.execute(
        "SELECT m.id, m.subject, m.status, m.created_at, m.sent_at, m.kind"
        " FROM crm_messages m JOIN crm_deals d ON d.id=m.deal_id"
        " WHERE d.company_id=? AND m.direction='out'"
        " ORDER BY COALESCE(m.sent_at, m.created_at) DESC LIMIT 1",
        (company_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def add_contact(company_id: int, name: str = "", role: str = "",
                email: str = "", phone: str = "", path: str = DB) -> int:
    con = db(path)
    cur = con.execute(
        "INSERT INTO crm_contacts(company_id,name,role,email,phone,created_at)"
        " VALUES(?,?,?,?,?,?)", (company_id, name, role, email, phone, _now()))
    con.commit()
    cid = cur.lastrowid
    con.close()
    return cid


# ======================
# الصفقات
# ======================
def open_deal(opportunity_id: int, path: str = DB) -> int:
    """
    يحوّل فرصة مرصودة إلى صفقة متتبَّعة.

    يعيد معرّف الصفقة القائمة إن كانت الفرصة مفتوحة أصلاً - الرادار يعيد
    رصد المناقصة نفسها في جولات متتالية، وفتح صفقة لكل رصد يُغرق اللوحة.
    """
    con = db(path)
    row = con.execute("SELECT id FROM crm_deals WHERE opportunity_id=?",
                      (opportunity_id,)).fetchone()
    if row:
        con.close()
        return row["id"]

    opp = con.execute(
        "SELECT o.*, e.company, e.location, e.url FROM opportunities o"
        " LEFT JOIN events e ON e.id=o.event_id WHERE o.id=?",
        (opportunity_id,)).fetchone()
    if not opp:
        con.close()
        raise KeyError(f"لا فرصة بالمعرّف {opportunity_id}")

    con.close()
    cid = upsert_company(opp["company"] or "", country=opp["location"] or "",
                         website=opp["url"] or "", path=path)

    con = db(path)
    cur = con.execute(
        "INSERT INTO crm_deals(opportunity_id,company_id,title,stage,deadline,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (opportunity_id, cid or None, opp["title"], STAGES[0],
         opp["deadline"], _now(), _now()))
    con.commit()
    did = cur.lastrowid
    con.close()
    return did


def open_deal_for_company(company_id: int, title: str = "",
                          deadline: str = "", path: str = DB) -> int:
    """
    يفتح صفقة لشركة من قائمتك - بلا مناقصة.

    أغلب البيع لا يبدأ من مناقصة منشورة بل من عميل في قائمتك تعرف أن
    لديه محطة. فالصفقة هنا لا تحتاج `opportunity_id`، وعنوانها الافتراضي
    اسم الشركة نفسه حتى تُسمّيه بما يناسب.
    """
    con = db(path)
    co = con.execute("SELECT name FROM crm_companies WHERE id=?",
                     (company_id,)).fetchone()
    if not co:
        con.close()
        raise KeyError(f"لا شركة بالمعرّف {company_id}")

    # صفقة مفتوحة قائمة لنفس الشركة تُعاد بدل فتح ثانية
    row = con.execute(
        "SELECT id FROM crm_deals WHERE company_id=? AND stage NOT IN ('فوز','خسارة')"
        " ORDER BY id DESC LIMIT 1", (company_id,)).fetchone()
    if row and not title:
        con.close()
        return row["id"]

    cur = con.execute(
        "INSERT INTO crm_deals(opportunity_id,company_id,title,stage,deadline,"
        "created_at,updated_at) VALUES(NULL,?,?,?,?,?,?)",
        (company_id, title or co["name"], STAGES[0], deadline or None,
         _now(), _now()))
    con.commit()
    did = cur.lastrowid
    con.close()
    return did


def companies(with_email_only: bool = False, path: str = DB) -> list[dict]:
    """قائمتك، ومعها حالة التواصل — وهي ما يحدّد من يُراسَل تالياً."""
    con = db(path)
    rows = [dict(r) for r in con.execute(
        "SELECT c.*, "
        " (SELECT COUNT(*) FROM crm_contacts k WHERE k.company_id=c.id"
        "   AND k.email<>'') n_emails,"
        " (SELECT k.email FROM crm_contacts k WHERE k.company_id=c.id"
        "   AND k.email<>'' ORDER BY k.id LIMIT 1) email,"
        " (SELECT k.name FROM crm_contacts k WHERE k.company_id=c.id"
        "   ORDER BY k.id LIMIT 1) contact_name"
        " FROM crm_companies c ORDER BY c.name")]
    con.close()
    for r in rows:
        prev = contacted(r["id"], path)
        r["last_contact"] = (prev or {}).get("sent_at") or (prev or {}).get("created_at")
        r["contacted"] = bool(prev)
    if with_email_only:
        rows = [r for r in rows if r.get("email")]
    return rows


def set_stage(deal_id: int, stage: str, note: str = "", path: str = DB) -> None:
    if stage not in STAGES:
        raise ValueError(f"مرحلة غير معروفة: {stage} · المتاح: {', '.join(STAGES)}")
    con = db(path)
    con.execute("UPDATE crm_deals SET stage=?, outcome_note=COALESCE(?,outcome_note),"
                " updated_at=? WHERE id=?",
                (stage, note or None, _now(), deal_id))
    con.commit()
    con.close()


def schedule_next(deal_id: int, action: str, days: int, path: str = DB) -> None:
    when = (date.today() + timedelta(days=days)).isoformat()
    con = db(path)
    con.execute("UPDATE crm_deals SET next_action=?, next_at=?, updated_at=?"
                " WHERE id=?", (action, when, _now(), deal_id))
    con.commit()
    con.close()


def deals(stage: str | None = None, path: str = DB) -> list[dict]:
    con = db(path)
    sql = ("SELECT d.*, c.name company_name FROM crm_deals d"
           " LEFT JOIN crm_companies c ON c.id=d.company_id")
    args: tuple = ()
    if stage:
        sql += " WHERE d.stage=?"
        args = (stage,)
    sql += " ORDER BY d.updated_at DESC"
    rows = [dict(r) for r in con.execute(sql, args)]
    con.close()

    today = date.today()
    for r in rows:
        r["days_left"] = None
        if dl := (r.get("deadline") or "").strip():
            try:
                r["days_left"] = (date.fromisoformat(dl) - today).days
            except ValueError:
                pass
        r["due"] = bool(r.get("next_at") and r["next_at"] <= today.isoformat())
    return rows


def due_actions(path: str = DB) -> list[dict]:
    """ما حان موعده اليوم أو فات - هذا ما يمنع الصفقة من النسيان."""
    return [d for d in deals(path=path) if d["due"] and d["stage"] not in CLOSED]


# ======================
# الاستيراد من الرادار
# ======================
def backfill_companies(path: str = DB) -> dict:
    """
    يستورد الجهات الموجودة في الأحداث.

    ثمانون جهة مرصودة في `events` بلا ربط، وفيها تكرار بالهمزات: «وزارة
    الصحة - سلطنة عُمان» و«... عمان». التطبيع يدمجهما فيصير تاريخ التعامل
    معها واحداً لا مجزّأً.
    """
    con = db(path)
    rows = con.execute(
        "SELECT company, location, COUNT(*) n FROM events"
        " WHERE company IS NOT NULL AND company<>'' GROUP BY company").fetchall()
    con.close()

    added = merged = 0
    for r in rows:
        before = _count(path)
        upsert_company(r["company"], country=r["location"] or "", path=path)
        if _count(path) > before:
            added += 1
        else:
            merged += 1
    return {"raw": len(rows), "added": added, "merged_or_existing": merged}


def _count(path: str = DB) -> int:
    con = db(path)
    n = con.execute("SELECT COUNT(*) FROM crm_companies").fetchone()[0]
    con.close()
    return n


def pipeline(path: str = DB) -> dict:
    con = db(path)
    out = {s: 0 for s in STAGES}
    for r in con.execute("SELECT stage, COUNT(*) n FROM crm_deals GROUP BY stage"):
        out[r["stage"]] = r["n"]
    con.close()
    return out


def render(path: str = DB) -> str:
    """عرض نصّي للطرفية."""
    pipe = pipeline(path)
    rows = deals(path=path)
    due = due_actions(path)

    out = ["", "=" * 60, "  إدارة العلاقات", "=" * 60, ""]
    out.append("  المسار: " + " · ".join(f"{s} {pipe[s]}" for s in STAGES if pipe[s]))
    out.append(f"  شركات مسجّلة: {_count(path)}")

    if due:
        out += ["", "  ⏰ إجراءات حان موعدها", "  " + "-" * 56]
        for d in due[:8]:
            out.append(f"   {d['next_at']}  {d['next_action'][:40]}")
            out.append(f"            {d['title'][:50]}")

    live = [d for d in rows if d["stage"] not in CLOSED]
    if live:
        out += ["", "  صفقات مفتوحة", "  " + "-" * 56]
        for d in live[:12]:
            n = d["days_left"]
            when = "" if n is None else (f"  ⏳ {n}ي" if n >= 0 else f"  ✗ -{-n}ي")
            out.append(f"   {d['stage']:<11} {d['title'][:40]}{when}")
            if d.get("company_name"):
                out.append(f"               {d['company_name'][:46]}")
    return "\n".join(out)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    args = sys.argv[1:]
    if args and args[0] == "backfill":
        print(json.dumps(backfill_companies(), ensure_ascii=False, indent=1))
    elif args and args[0] == "open" and len(args) > 1:
        print("صفقة رقم", open_deal(int(args[1])))
    elif args and args[0] == "stage" and len(args) > 2:
        set_stage(int(args[1]), args[2])
        print("حُدّثت")
    else:
        print(render())
