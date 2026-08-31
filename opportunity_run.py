"""
منفّذ كشف الفرص: جمع ← تصنيف ← تسجيل ← فريق أحمر ← حفظ ← تنبيه.

مفصول عن opportunity.py الذي يحمل النماذج والتسجيل، ليبقى الأخير قابلاً
للاستيراد بلا تحميل crewai.
"""
from __future__ import annotations

import json
import os
import re
import sys

# المسار المطلق من opportunity: السلسلة النسبية تُحلّ حسب مجلد التشغيل،
# فتشغيل الـEXE من مجلد آخر يفتح قاعدة فارغة بصمت.
from datetime import date, datetime

from opportunity import _DB
from opportunity import (EVENT_TYPES, INVESTIGATE, WATCH, Event, Scored,
                         _db, _now, band_of, collect, load_profile, score_one)

# كم خبراً يرى المصنّف. كان أربعين بينما الجولة تجمع 243 - أي أن 83%
# منها لم تُقرأ أصلاً. سياق Gemini مليون رمز فالسقف لم يعد قيداً تقنياً.
CLASSIFY_CAP = int(os.getenv("CLASSIFY_CAP", "90"))

# ما يدلّ على مناقصة أو مشروع، لا خبراً عاماً
_TENDER = re.compile(
    r"مناقص|عطاء|كراسة|ترسي|طرح|إعلان|توريد|تنفيذ|إنشاء|"
    r"tender|bid|rfp|rfq|procurement|awarded|contract|"
    r"appel d.offres|march[ée] public", re.I)


def _rank_events(events: list) -> list:
    """
    يرتّب الأخبار قبل عرضها على المصنّف.

    السبب: الجولة تجمع مئات الأخبار والمصنّف يرى أوائلها فقط، وترتيب
    الجمع هو ترتيب الاستعلامات لا ترتيب الأهمية - فتصدّرت أخبارٌ عامة
    وضاعت مناقصات في الذيل. جولة جمعت 243 خبراً أنتجت فرصتين.

    الترجيح بسيط ومكشوف: كلمة تدلّ على مناقصة في العنوان تعلو، ووجود
    تاريخ يعلو، والوصف الفارغ يهبط. لا نموذج هنا - الترتيب قرار كود.
    """
    def rank(e) -> tuple:
        title = e.title or ""
        return (
            -2 if _TENDER.search(title) else 0,
            -1 if _TENDER.search(e.description or "") else 0,
            -1 if (e.event_date or "").strip() else 0,
            0 if (e.description or "").strip() else 1,
        )

    return sorted(events, key=rank)


def _clean_deadline(raw) -> str:
    """
    يقبل YYYY-MM-DD فقط ويرفض ما عداه.

    النموذج يكتب أحياناً «قريباً» أو «غير محدد» أو تاريخاً بصيغة أخرى،
    وتخزينها كما هي يجعل حساب الأيام المتبقية مستحيلاً ويُظهر نصّاً
    عشوائياً مكان الموعد. الرفض أنظف من التخمين.
    """
    t = str(raw or "").strip()
    if not t:
        return ""
    try:
        d = date.fromisoformat(t[:10])
    except ValueError:
        return ""
    # موعد في الماضي البعيد أو المستقبل البعيد = هلوسة لا استخراج
    delta = (d - date.today()).days
    return d.isoformat() if -365 < delta < 730 else ""


OPP_RED_BRIEF = """أنت الفريق الأحمر. أمامك فرص صُنّفت عالية لهذه الشركة.

--- ملف الشركة ---
{profile}
--- نهاية ---

--- الفرص ---
{items}
--- نهاية ---

لكل فرصة اسأل: **لماذا قد تفشل؟** ابحث عن:
- سبب يجعل الشركة غير مؤهلة فعلياً (تصنيف، ضمان بنكي، سجل سابق)
- منافس محلي أقوى سيأخذها
- موعد نهائي ضيق لا يكفي للتجهيز
- تكلفة خفية تأكل الهامش

أعد JSON صالحاً فقط:
{{"items": [{{"idea": "عنوان الفرصة كما ورد",
              "detail": "سبب الفشل الأرجح - جملتان",
              "score": 0-100,
              "risks": ["الآلية الدقيقة للفشل"],
              "evidence": [], "counterarguments": []}}]}}"""

CLASSIFY_BRIEF = """أنت محلل فرص. أمامك ملف شركة وقائمة أخبار.

--- ملف الشركة ---
{profile}
--- نهاية ---

--- الأخبار (كل واحدة بمعرّف مصدرها) ---
{items}
--- نهاية ---

لكل خبر يمثّل **فرصة محتملة لهذه الشركة تحديداً**، أعد عنصراً.
تجاهل الأخبار العامة التي لا تفتح باب عمل.

أنواع الأحداث المسموحة: {types}

أعد JSON صالحاً فقط:

{{"items": [
  {{"idea": "عنوان الفرصة في سطر",
    "detail": "لماذا تناسب هذه الشركة تحديداً - جملتان",
    "event_type": "أحد الأنواع أعلاه",
    "company": "الجهة صاحبة الحدث إن ذُكرت وإلا فارغ",
    "location": "الموقع إن ذُكر وإلا فارغ",
    "deadline": "آخر موعد للتقديم بصيغة YYYY-MM-DD إن ذُكر، وإلا فارغ",
    "fit": 0-100, "market": 0-100, "timing": 0-100, "profit": 0-100,
    "competition": 0-100, "risk": 0-100, "score": 0-100,
    "evidence": ["S1"],
    "risks": ["خطر"], "counterarguments": [],
    "action": "الإجراء الموصى به في سطر"}}
]}}

قواعد ملزِمة:
- `fit` ملاءمة قدرات الشركة تحديداً، لا جاذبية المشروع عموماً.
- `competition` و`risk`: الأعلى أسوأ (100 = منافسة خانقة أو خطر عالٍ).
- `evidence` معرّفات من الأخبار أعلاه حصراً - لا تخترع معرّفاً.
- `deadline` من نصّ الخبر حرفياً لا تقديراً. إن ذُكر «خلال أسبوعين» أو
  «نهاية الشهر» فاحسبه من تاريخ اليوم {today}. وإن لم يُذكر موعد إطلاقاً
  فاتركه فارغاً - موعدٌ مخترع أسوأ من غيابه لأنه يُبنى عليه قرار.
- بعض المدخلات فهارس تحمل «محتوى الصفحة»: عدة مناقصات في نصّ واحد.
  استخرج منها **كل مناقصة مطابقة كعنصر مستقل** بعنوانها وجهتها وموعدها،
  ولا تُدرج الفهرس نفسه كفرصة. معرّف المصدر واحد لها جميعاً.
- إن لم تكن فرصة حقيقية لهذه الشركة، لا تُدرجها إطلاقاً."""


def detect(profile: dict | None = None, extra_queries: list[str] | None = None,
           on_stage=None, db: str = _DB) -> list[Scored]:
    import main
    import pipeline

    def stage(msg):
        if on_stage:
            on_stage(msg)

    profile = profile or load_profile()
    if not profile:
        raise RuntimeError("لا يوجد profile.json - عرّف شركتك أولاً")

    stage("جمع الأخبار من مجالك...")
    reg, raw = collect(profile, extra_queries)
    if not raw:
        stage("لم تُجمع أخبار (تحقق من SERPER_API_KEY)")
        return []
    stage(f"{len(raw)} خبراً من {len(reg.items)} مصدراً")

    agents = main.build_agents()
    mk = agents.get("_rebuild")
    prof_txt = json.dumps(profile, ensure_ascii=False, indent=1)[:2500]
    ranked = _rank_events(raw)

    # فتح صفحات الفهارس. المقتطف يقول «موقع فيه مناقصات»، والصفحة نفسها
    # تقول «هذه المناقصة تغلق بعد 23 يوماً» - وهذا الفرق هو سبب أن 243
    # خبراً كانت تُنتج فرصتين.
    pages: dict[str, str] = {}
    try:
        import portals
        pages = portals.expand(ranked)
        if pages:
            stage(f"فُتحت {len(pages)} صفحة فهرس")
    except Exception as e:
        stage(f"تعذّر فتح الفهارس: {type(e).__name__}")

    def _block(e) -> str:
        head = f"[{e.source_id}] {e.title}\n     {e.description[:200]}"
        body = pages.get(e.url or "")
        return head + (f"\n     ── محتوى الصفحة ──\n     {body}" if body else "")

    items_txt = "\n".join(_block(e) for e in ranked[:CLASSIFY_CAP])

    stage("تصنيف ومطابقة مع ملف شركتك...")
    raw_out = pipeline._run_one(
        "OPP", agents["OPP"],
        CLASSIFY_BRIEF.format(profile=prof_txt, items=items_txt,
                              types="، ".join(EVENT_TYPES),
                              today=date.today().isoformat()),
        mk("OPP") if mk else None)

    parsed = pipeline.parse_items(raw_out, "OPP", reg)
    if not parsed:
        stage("لم يُصنَّف أي خبر كفرصة")
        return []

    # الحقول الخام (fit/market/…) لا يحملها Item فنقرأها من JSON مباشرة
    rawd = {d.get("idea", "")[:60]: d
            for d in pipeline._salvage_objects(raw_out, cap=80)}

    stage(f"{len(parsed)} فرصة محتملة - جارٍ التسجيل...")
    scored: list[Scored] = []
    for it in parsed:
        d = rawd.get(it.idea[:60], {})
        total, factors = score_one(d, profile, len(it.evidence))
        src = reg.get(it.evidence[0]) if it.evidence else None
        ev = Event(title=it.idea, description=it.detail,
                   event_type=str(d.get("event_type", "")),
                   company=str(d.get("company", "")),
                   location=str(d.get("location", "")),
                   source_id=it.evidence[0] if it.evidence else "",
                   url=src.url if src else "")
        scored.append(Scored(event=ev, score=total, band=band_of(total),
                             factors=factors, evidence=it.evidence,
                             why=it.detail, action=str(d.get("action", "")),
                             deadline=_clean_deadline(d.get("deadline"))))

    scored.sort(key=lambda s: -s.score)

    # الفريق الأحمر على المرشّحات الجادة فقط - مهاجمة ما سيُهمَل هدر
    serious = [s for s in scored if s.score >= INVESTIGATE][:8]
    if serious:
        stage(f"الفريق الأحمر يهاجم {len(serious)} فرصة...")
        red_raw = pipeline._run_one(
            "OPPRED", agents["RED"],
            OPP_RED_BRIEF.format(
                profile=prof_txt,
                items="\n".join(f"- [{s.score}] {s.event.title}" for s in serious)),
            mk("RED") if mk else None)
        for r in pipeline.parse_items(red_raw, "RED", reg):
            for s in serious:
                if r.idea[:25] in s.event.title or s.event.title[:25] in r.idea:
                    s.red_team = r.detail or "; ".join(r.risks)
                    # نقد الفريق الأحمر يخصم فعلياً - وإلا فهو زينة
                    s.score = max(0, round(s.score * (1 - 0.25 * (r.score / 100))))
                    s.band = band_of(s.score)
                    break
        scored.sort(key=lambda s: -s.score)

    persist(scored, db)
    stage(f"اكتمل: {sum(1 for s in scored if s.band == 'HIGH')} عالية الأولوية")
    return scored


def persist(scored: list[Scored], db: str = _DB) -> None:
    con = _db(db)
    for s in scored:
        e = s.event
        con.execute(
            "INSERT OR IGNORE INTO events(ext_id,title,description,event_type,"
            "company,sector,location,event_date,url,source_id,seen_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (e.ext_id, e.title, e.description, e.event_type, e.company,
             e.sector, e.location, e.event_date, e.url, e.source_id, _now()))
        row = con.execute("SELECT id FROM events WHERE ext_id=?",
                          (e.ext_id,)).fetchone()
        con.execute(
            "INSERT INTO opportunities(event_id,title,score,band,factors,"
            "evidence,why,action,red_team,deadline,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"] if row else None, e.title, s.score, s.band,
             json.dumps(s.factors, ensure_ascii=False),
             json.dumps(s.evidence, ensure_ascii=False),
             s.why, s.action, s.red_team, s.deadline, _now()))
    con.commit()
    con.close()


def _source_map(con) -> dict[str, list[dict]]:
    """
    S12 → الروابط التي حملت هذا المعرّف.

    الفرصة تخزّن أدلّتها كمعرّفات (`["S17"]`) من سجل تشغيلة الكشف، والسجل
    نفسه لا يُحفظ - فالمعرّف بعد انتهاء الجولة رمز أصمّ. لكن كل حدث خُزّن
    ومعه `source_id` ورابطه، فنستخرج الخريطة منها ونستعيد المصدر الحقيقي
    للفرص القديمة كما للجديدة، بلا هجرة قاعدة بيانات.
    """
    try:
        import trust
    except ImportError:
        trust = None

    out: dict[str, list[dict]] = {}
    for sid, url in con.execute(
            "SELECT DISTINCT source_id, url FROM events"
            " WHERE source_id IS NOT NULL AND url IS NOT NULL AND url<>''"):
        site = ""
        try:
            from urllib.parse import urlparse
            site = (urlparse(url).netloc or "").lower().removeprefix("www.")
        except ValueError:
            pass
        out.setdefault(sid, []).append({
            "id": sid, "url": url, "site": site,
            "weight": round(trust.weight(url), 2) if trust else None,
            "tier": trust.TIERS[trust.classify(url)[0]][1] if trust else "",
        })
    return out


def recent(limit: int = 20, min_score: int = 0, db: str = _DB,
           include_expired: bool = False) -> list[dict]:
    """
    الفرص المرصودة، الأحدث والأعلى درجةً أولاً.

    المنتهية مُستبعَدة افتراضاً: مناقصة أُغلق بابها ليست فرصة، ووجودها
    في الرادار يزاحم الحيّ على انتباهك. `include_expired` يعيدها لمن
    أراد المراجعة.
    """
    con = _db(db)
    rows = con.execute(
        "SELECT o.*, e.url, e.company, e.location, e.event_type, e.source_id"
        " FROM opportunities o LEFT JOIN events e ON e.id=o.event_id"
        " WHERE o.score>=? ORDER BY o.score DESC, o.created_at DESC LIMIT ?",
        (min_score, limit * 3)).fetchall()
    smap = _source_map(con)
    con.close()

    # جولتا كشف على نفس الحدث تكتبان فرصتين متطابقتين، فتظهر في الرادار
    # مرّتين وتُوهم بوفرة ليست حقيقية. نُبقي الأعلى درجة لكل حدث.
    out, taken = [], set()
    for r in rows:
        d = dict(r)
        key = d.get("event_id") or d.get("title")
        if key in taken:
            continue
        taken.add(key)
        srcs, seen = [], set()

        # رابط الحدث نفسه أولاً: هو المصدر المباشر لا المستنتَج
        if d.get("url") and d["url"] not in seen:
            seen.add(d["url"])
            srcs += [s for s in smap.get(d.get("source_id") or "", [])
                     if s["url"] == d["url"]] or [{"id": d.get("source_id") or "",
                                                   "url": d["url"], "site": "",
                                                   "weight": None, "tier": ""}]

        try:
            ids = json.loads(d.get("evidence") or "[]")
        except (ValueError, TypeError):
            ids = []
        for sid in ids:
            for s in smap.get(sid, []):
                if s["url"] not in seen:
                    seen.add(s["url"])
                    srcs.append(s)

        # الأيام المتبقية: رقم يُقرأ أسرع من تاريخ يحتاج طرحاً ذهنياً
        d["days_left"] = None
        d["expired"] = False
        if dl := (d.get("deadline") or "").strip():
            try:
                d["days_left"] = (date.fromisoformat(dl) - date.today()).days
                d["expired"] = d["days_left"] < 0
            except ValueError:
                d["deadline"] = ""

        if d["expired"] and not include_expired:
            continue

        d["sources"] = srcs
        out.append(d)
        if len(out) >= limit:
            break
    return out


def alert_text(s: Scored) -> str:
    f = s.factors
    out = ["", "=" * 54,
           f"  فرصة مرصودة — {s.band}   ({s.score}/100)",
           "=" * 54,
           f"  {s.event.title}", "",
           f"  النوع     : {s.event.event_type or '—'}",
           f"  الجهة     : {s.event.company or '—'}",
           f"  الموقع    : {s.event.location or '—'}", "",
           f"  الملاءمة  : {f.get('fit', '—')}",
           f"  السوق     : {f.get('market', '—')}",
           f"  التوقيت   : {f.get('timing', '—')}",
           f"  الربحية   : {f.get('profit', '—')}",
           f"  المنافسة  : {f.get('competition', '—')}   (بعد العكس: الأعلى أفضل)",
           f"  المخاطر   : {f.get('risk', '—')}   (بعد العكس: الأعلى أفضل)",
           f"  الأدلة    : {' '.join('[' + e + ']' for e in s.evidence) or '—'}", "",
           f"  لماذا     : {s.why}"]
    if s.red_team:
        out.append(f"  الفريق الأحمر: {s.red_team}")
    out.append(f"  الإجراء   : {s.action or '—'}")
    if s.event.url:
        out.append(f"  الرابط    : {s.event.url}")
    return "\n".join(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "list":
        for r in recent(min_score=WATCH):
            print(f"[{r['score']:3d}] {r['band']:12s} {r['title'][:64]}")
    else:
        res = detect(extra_queries=args or None,
                     on_stage=lambda m: print("·", m, flush=True))
        for s in res:
            if s.score >= INVESTIGATE:
                print(alert_text(s))
        print(f"\nالمجموع: {len(res)} فرصة | "
              f"عالية {sum(1 for s in res if s.band == 'HIGH')} | "
              f"للتحقيق {sum(1 for s in res if s.band == 'INVESTIGATE')} | "
              f"للمراقبة {sum(1 for s in res if s.band == 'WATCH')}")
