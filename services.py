"""
سجل الخدمات - كل ما بُني، في مكان واحد.

المشكلة: أربع عشرة وحدة تعمل ولا يراها أحد. رادار الفرص يرصد مناقصات
حقيقية، وغرفة الحرب تناظر قراراً بسبعة أدوار، ومحرّك التنبؤ يعرف أنه
«متفائل بـ18 نقطة» - وكلها لا تُستدعى إلا بأمر طرفية يحفظه المستخدم غيباً.
أداة لا تُرى غير موجودة عملياً.

التقسيم إلى `fast` و`slow` ليس تصنيفاً تجميلياً: السريعة قراءة من قاعدة
البيانات تُرجع فوراً، والبطيئة تستدعي النموذج وتستغرق دقائق - فتحتاج قفل
التشغيل نفسه الذي يحمي التقارير من التداخل، وإلا تسابقت تشغيلتان على
نفس الموارد (وهو العطل الذي أنتج RuntimeError: Executor is already running).

كل خدمة تُرجع نصاً جاهزاً للعرض. الوحدات نفسها تملك دوال render() تكتب
للطرفية، فنعيد استخدامها بدل كتابة عرض ثانٍ يتفرّع عنها ويتخلّف.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Service:
    id: str
    label: str
    desc: str
    kind: str                      # fast | slow
    run: Callable[[dict], str]
    needs: str = ""                # مدخل نصّي مطلوب، إن وُجد
    placeholder: str = ""


# ======================
# الخدمات السريعة - قراءة فقط
# ======================
def _dashboard(_a: dict) -> str:
    import dashboard
    return dashboard.render(dashboard.snapshot())


def _opportunities(a: dict) -> str:
    import opportunity_run as opp
    rows = opp.recent(limit=int(a.get("limit") or 25))
    if not rows:
        return "لا فرص مرصودة بعد. شغّل «كشف الفرص» ليملأ الرادار."
    out = []
    for r in rows:
        out.append(f"[{r['score']:>3}] {r['band']:<7} {r['title']}")
        meta = " · ".join(x for x in (r.get("company"), r.get("location"),
                                      r.get("event_type")) if x)
        if meta:
            out.append(f"        {meta}")
        if r.get("action"):
            out.append(f"        ← {r['action']}")
        for s in r.get("sources") or []:
            w = f"{s['weight']:.2f}" if s.get("weight") is not None else "  — "
            out.append(f"        مصدر {w}  {s['url']}")
        out.append("")
    return "\n".join(out)


def _predictions(_a: dict) -> str:
    import predictions
    return predictions.render_scoreboard()


def _competitors(_a: dict) -> str:
    import competitors
    rows = competitors.all_competitors()
    if not rows:
        return "لا منافسون مُتابَعون بعد."
    out = []
    for c in rows:
        out.append(f"تهديد {c['threat']:>3} · {c['name']} ({c.get('sector') or '—'})")
        p = competitors.profile(c["id"])
        for f in (p.get("facts") or [])[:6]:
            out.append(f"        {f['attribute']}: {f['value']}")
    return "\n".join(out)


def _feedback(_a: dict) -> str:
    import feedback
    # stats() تُرجع {نوع: {total, promoted}} لا مجاميع مسطّحة
    s = feedback.stats()
    total = sum(v.get("total", 0) for v in s.values())
    promoted = sum(v.get("promoted", 0) for v in s.values())
    items = feedback.all_items()
    out = [f"تصحيحات: {total} · مُرقّاة إلى مهارات: {promoted} · "
           f"معلّقة: {total - promoted}", ""]
    for it in items[:20]:
        mark = "✓" if it.get("promoted") else "·"
        out.append(f" {mark} [{it.get('kind')}] {it.get('subject')}")
        out.append(f"     {it.get('correction')}")
    return "\n".join(out) if items else "لا تصحيحات مسجّلة بعد."


def _skills(_a: dict) -> str:
    import skills
    found = skills.discover()
    if not found:
        return "لا مهارات محمّلة."
    out = []
    for s in found:
        out.append(f"● {s.name} — {s.description}")
        out.append(f"     {len(s.body)} حرف · {s.path}")
    return "\n".join(out)


def _knowledge(a: dict) -> str:
    import memory
    g = memory.ContextGraph()
    q = (a.get("input") or "").strip()
    if not q:
        st = g.stats()
        return "\n".join(f"{k}: {v}" for k, v in st.items())
    hits = g.search(q, limit=25)
    if not hits:
        return f"لا نتائج لـ «{q}»."
    out = []
    for h in hits:
        out.append(f"● {h.get('entity')} · {h.get('field')} = {h.get('value')}")
        if h.get("source_id"):
            out.append(f"     مصدر: {h['source_id']}")
    return "\n".join(out)


def _sources(_a: dict) -> str:
    import memory
    g = memory.ContextGraph()
    rows = g.sources()
    if not rows:
        return "لا مصادر مخزّنة."

    import trust
    out = []
    for r in rows[:120]:
        url = r.get("url") or ""
        out.append(f"{trust.weight(url):.2f}  {r.get('site') or '—':<28} "
                   f"{r.get('n', 0):>3} حقيقة")
        out.append(f"      {url}")
        if r.get("topic"):
            out.append(f"      {r['topic']}")
    return "\n".join(out)


def _trust(_a: dict) -> str:
    import memory
    import trust
    g = memory.ContextGraph()
    urls = [r.get("url") for r in g.sources() if r.get("url")]
    if not urls:
        return "لا مصادر مخزّنة لتقييمها."
    dist: dict[str, int] = {}
    for u in urls:
        t, _w = trust.classify(u)
        dist[t] = dist.get(t, 0) + 1
    ws = [trust.weight(u) for u in urls]
    out = [f"مصادر مخزّنة: {len(urls)} · متوسط الوزن: {sum(ws)/len(ws):.2f}", ""]
    for t, n in sorted(dist.items(), key=lambda x: -trust.TIERS.get(x[0], (0,))[0]):
        w, name = trust.TIERS.get(t, (0.45, t))
        out.append(f" {w:.2f}  {name:<34} {n}")
    return "\n".join(out)


def _cache(_a: dict) -> str:
    import cache
    s = cache.stats()
    return "\n".join(f"{k}: {v}" for k, v in s.items())


# ======================
# الخدمات البطيئة - تستدعي النموذج
# ======================
def _detect_opportunities(_a: dict) -> str:
    import scheduler
    scheduler.run_once(force=True)          # يمرّ بالجدولة ليُحدَّث موعدها
    return _opportunities({"limit": 25})


def _schedule(_a: dict) -> str:
    import scheduler
    return scheduler.render()


def _warroom(a: dict) -> str:
    import warroom
    return warroom.render(warroom.convene(a["input"]))


def _blindspots(a: dict) -> str:
    import blindspots
    return blindspots.render(blindspots.find(a["input"]))


def _board(_a: dict) -> str:
    import board
    res = board.convene()
    path = board.save(res)
    return board.render(res) + f"\n\nحُفظ في: {path}"


def _scenario(a: dict) -> str:
    import scenario
    return scenario.render(scenario.run(a["input"]))


def _twin(a: dict) -> str:
    import twin
    raw = a["input"]
    if "|" not in raw:
        return ("اكتب: اسم المنافس | الإجراء الذي ستتخذه\n"
                "مثال: شركة المياه المتقدمة | خفض السعر 15%")
    name, action = (x.strip() for x in raw.split("|", 1))
    return twin.render(twin.predict_response(name, action))


def _judge(_a: dict) -> str:
    import judge
    if not judge.is_up() and not judge.ensure_server():
        return "محكّم Mastra غير متاح — يحتاج Node.js وتشغيل mastra/server.mjs."
    from pathlib import Path
    from main import out_dir_default
    reports = sorted(Path(out_dir_default()).glob("*.md"),
                     key=lambda p: -p.stat().st_mtime)
    if not reports:
        return "لا تقارير لتحكيمها."
    md = reports[0].read_text(encoding="utf-8")
    return judge.as_markdown(judge.evaluate(md, reports[0].stem)) or "تعذّر التحكيم."


REGISTRY: list[Service] = [
    Service("dashboard", "لوحة المعلومات",
            "كل ما تراكم: الفرص والمنافسون والتنبؤات والتعارضات",
            "fast", _dashboard),
    Service("opportunities", "رادار الفرص",
            "المناقصات والفرص المرصودة مرتّبة بالدرجة", "fast", _opportunities),
    Service("detect", "كشف فرص جديدة",
            "جولة بحث جديدة: جمع ← تصنيف ← تسجيل ← فريق أحمر",
            "slow", _detect_opportunities),
    Service("competitors", "ملفات المنافسين",
            "من نتابعه وما تغيّر لديه", "fast", _competitors),
    Service("twin", "التوأم الرقمي",
            "كيف سيردّ منافس على إجراء تتخذه", "slow", _twin,
            needs="اسم المنافس | الإجراء",
            placeholder="شركة المياه المتقدمة | خفض السعر 15%"),
    Service("scenario", "محاكي السيناريوهات",
            "أثر تغيّر كل متغيّر على احتمال النجاح", "slow", _scenario,
            needs="القرار المراد محاكاته",
            placeholder="دخول سوق صيانة محطات RO للمصانع الصغيرة"),
    Service("warroom", "غرفة الحرب",
            "سبعة أدوار متعارضة تتناظر حول قرار واحد", "slow", _warroom,
            needs="القرار المطروح",
            placeholder="هل نبيع خدمة اشتراك مراقبة بدل بيع المحطات؟"),
    Service("blindspots", "النقاط العمياء",
            "ما الذي يفوتني في هذا التحليل؟", "slow", _blindspots,
            needs="التحليل أو الخطة",
            placeholder="خطتنا: بيع عقود صيانة سنوية لمحطات RO في ظفار"),
    Service("board", "مجلس الإدارة",
            "يقرأ ما تراكم ويوصي - جلسة شهرية", "slow", _board),
    Service("predictions", "لوح التنبؤات",
            "الدقة والمعايرة: هل نحن متفائلون؟", "fast", _predictions),
    Service("knowledge", "قاعدة المعرفة",
            "بحث في الحقائق المخزّنة، أو الإحصاء إن تركت الحقل فارغاً",
            "fast", _knowledge, needs="", placeholder="أغشية · ضغط تفاضلي · CIP"),
    Service("sources", "المصادر المخزّنة",
            "الروابط المحذوفة من التقارير ومحفوظة هنا", "fast", _sources),
    Service("trust", "جودة المصادر",
            "توزيع المصادر على طبقات الثقة", "fast", _trust),
    Service("feedback", "حلقة التغذية الراجعة",
            "تصحيحاتك وما رُقّي منها إلى مهارات دائمة", "fast", _feedback),
    Service("skills", "المهارات",
            "معرفة النطاق المحمّلة تلقائياً حسب الموضوع", "fast", _skills),
    Service("judge", "المحكّم المستقل",
            "تقييم آخر تقرير عبر Mastra (يحتاج Node)", "slow", _judge),
    Service("cache", "ذاكرة البحث",
            "ما وفّرته من نداءات بحث مكرّرة", "fast", _cache),
    Service("schedule", "التحديث التلقائي",
            "متى عمل الرادار آخر مرة ومتى يعمل تالياً", "fast", _schedule),
]

BY_ID = {s.id: s for s in REGISTRY}


def catalog() -> list[dict]:
    return [{"id": s.id, "label": s.label, "desc": s.desc, "kind": s.kind,
             "needs": s.needs, "placeholder": s.placeholder} for s in REGISTRY]


def run(sid: str, args: dict | None = None) -> str:
    svc = BY_ID.get(sid)
    if svc is None:
        raise KeyError(f"خدمة غير معروفة: {sid}")
    args = args or {}
    if svc.needs and not (args.get("input") or "").strip():
        return f"هذه الخدمة تحتاج مدخلاً: {svc.needs}"
    return svc.run(args)


if __name__ == "__main__":
    import sys

    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    if len(sys.argv) < 2:
        print("الخدمات المتاحة:\n")
        for s in REGISTRY:
            tag = "بطيئة" if s.kind == "slow" else "سريعة"
            print(f"  {s.id:<15} [{tag}]  {s.label} — {s.desc}")
        print("\nالاستخدام: python services.py <id> [مدخل]")
        sys.exit(0)

    print(run(sys.argv[1], {"input": " ".join(sys.argv[2:])}))
