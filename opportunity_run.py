"""
منفّذ كشف الفرص: جمع ← تصنيف ← تسجيل ← فريق أحمر ← حفظ ← تنبيه.

مفصول عن opportunity.py الذي يحمل النماذج والتسجيل، ليبقى الأخير قابلاً
للاستيراد بلا تحميل crewai.
"""
from __future__ import annotations

import json
import sys

from opportunity import (EVENT_TYPES, INVESTIGATE, WATCH, Event, Scored,
                         _db, _now, band_of, collect, load_profile, score_one)

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
- إن لم تكن فرصة حقيقية لهذه الشركة، لا تُدرجها إطلاقاً."""


def detect(profile: dict | None = None, extra_queries: list[str] | None = None,
           on_stage=None, db: str = "knowledge.db") -> list[Scored]:
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
    items_txt = "\n".join(
        f"[{e.source_id}] {e.title}\n     {e.description[:200]}" for e in raw[:40])

    stage("تصنيف ومطابقة مع ملف شركتك...")
    raw_out = pipeline._run_one(
        "OPP", agents["A1"],
        CLASSIFY_BRIEF.format(profile=prof_txt, items=items_txt,
                              types="، ".join(EVENT_TYPES)),
        mk("A1") if mk else None)

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
                             why=it.detail, action=str(d.get("action", ""))))

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


def persist(scored: list[Scored], db: str = "knowledge.db") -> None:
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
            "evidence,why,action,red_team,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (row["id"] if row else None, e.title, s.score, s.band,
             json.dumps(s.factors, ensure_ascii=False),
             json.dumps(s.evidence, ensure_ascii=False),
             s.why, s.action, s.red_team, _now()))
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


def recent(limit: int = 20, min_score: int = 0, db: str = "knowledge.db") -> list[dict]:
    con = _db(db)
    rows = con.execute(
        "SELECT o.*, e.url, e.company, e.location, e.event_type, e.source_id"
        " FROM opportunities o LEFT JOIN events e ON e.id=o.event_id"
        " WHERE o.score>=? ORDER BY o.score DESC, o.created_at DESC LIMIT ?",
        (min_score, limit)).fetchall()
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

        d["sources"] = srcs
        out.append(d)
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
