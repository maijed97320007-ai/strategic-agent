"""
كاشف النقاط العمياء - «ما الذي يفوتني؟»

    تحليل مكتمل
        ↓
    ما المعلومة الناقصة التي قد تقلب القرار؟
        ↓
    البحث عنها فعلياً
        ↓
    إعادة التقييم بالأدلة الجديدة

الفرق بين هذا وبين «اذكر المخاطر»: المخاطر أشياء نعرف أننا لا نعرفها،
والنقطة العمياء شيء **لم يخطر ببالنا أصلاً**. لذلك الترتيب مهم: نطلب
تحديد الفجوة أولاً، ثم نبحث عنها بالكود، ثم نعيد العرض بما وجدناه -
بدل أن نطلب من النموذج تخمين ما ينقصه وهو نفسه من كتبه.

مثال من مجالك: تحليل مشروع تحلية مكتمل، لكن **تكلفة التخلص من المحلول
المركّز** غائبة - وهي وحدها قد تقلب الجدوى.
"""
from __future__ import annotations

import json
import sys

GAPS_BRIEF = """أمامك تحليل مكتمل. مهمتك ليست نقده بل تحديد **ما غاب عنه**.

--- التحليل ---
{analysis}
--- نهاية ---

اسأل: ما المعلومة التي لو عرفناها لتغيّر القرار، ولم يذكرها التحليل إطلاقاً؟

ركّز على فئات تُنسى عادة:
- تكاليف نهاية الدورة (التخلص، التفكيك، المعالجة اللاحقة)
- متطلبات تنظيمية أو تراخيص
- تكاليف تشغيل خفية (طاقة، عمالة متخصصة، قطع غيار نادرة)
- بدائل لم تُقارَن
- من فشل في هذا سابقاً ولماذا
- قيود محلية (مناخ، سلسلة إمداد، ندرة كفاءات)

لا تذكر ما ورد في التحليل ولو بصيغة أخرى.

أعد JSON صالحاً فقط:
{{"items": [
  {{"idea": "المعلومة الناقصة في سطر",
    "detail": "لماذا قد تقلب القرار",
    "score": 0-100,
    "risks": ["أثر جهلها"],
    "evidence": [],
    "counterarguments": ["استعلام بحث دقيق للعثور عليها"]}}
]}}

`score` = أثر هذه الفجوة على القرار. ضع في `counterarguments` **استعلام
بحث واحد** جاهز للاستخدام."""

REASSESS_BRIEF = """راجعت تحليلاً وبحثت عن فجواته. هذه نتائج البحث الجديدة.

--- الفجوات التي رُصدت ---
{gaps}
--- نهاية ---

--- ما وجده البحث ---
{findings}
--- نهاية ---

هل تغيّر شيء جوهري؟ أعد JSON صالحاً فقط:
{{"items": [
  {{"idea": "ما تغيّر أو تأكّد",
    "detail": "الأثر على القرار الأصلي",
    "score": 0-100,
    "risks": ["ما يجب فعله نتيجة ذلك"],
    "evidence": ["S1"],
    "counterarguments": []}}
]}}

`score` = قوة أثر هذا على القرار. إن لم يتغيّر شيء، قل ذلك صراحة بعنصر واحد."""


def find(analysis: str, search: bool = True, on_stage=None,
         max_queries: int = 4) -> dict:
    """
    يرصد الفجوات، يبحث عنها، ثم يعيد التقييم.

    يعيد dict فيه الفجوات والنتائج الجديدة وإعادة التقييم.
    """
    import main
    import pipeline
    import sources as S

    def stage(m):
        if on_stage:
            on_stage(m)

    agents = main.build_agents()
    mk = agents.get("_rebuild")

    # 1) رصد الفجوات
    stage("البحث عمّا غاب عن التحليل...")
    raw = pipeline._run_one(
        "GAPS", agents["A3"],
        GAPS_BRIEF.format(analysis=analysis[:12000]),
        mk("A3") if mk else None)
    gaps = sorted(pipeline.parse_items(raw, "GAPS", S.Registry()),
                  key=lambda x: -x.score)
    if not gaps:
        return {"gaps": [], "findings": [], "reassessment": [],
                "note": "لم تُرصد فجوات - قد يكون التحليل شاملاً أو النموذج أخفق"}

    stage(f"{len(gaps)} فجوة مرصودة")
    out = {"gaps": [{"gap": g.idea, "why": g.detail, "impact": g.score,
                     "risk": g.risks} for g in gaps]}

    if not search:
        out["findings"] = []
        out["reassessment"] = []
        return out

    # 2) البحث الفعلي عن أعلى الفجوات أثراً
    tool = main._search_tool()
    if tool is None:
        out["findings"] = []
        out["reassessment"] = []
        out["note"] = "لا مفتاح SERPER - رُصدت الفجوات ولم يُبحث عنها"
        return out

    import cache

    reg = S.Registry()
    stage(f"البحث عن أهم {min(max_queries, len(gaps))} فجوة...")
    for g in gaps[:max_queries]:
        query = (g.counterarguments[0] if g.counterarguments else g.idea)[:120]
        res = cache.cached_search(tool, query)
        organic = (res or {}).get("organic", []) if isinstance(res, dict) else []
        for it in organic[:4]:
            if it.get("link"):
                reg.add(it.get("title", ""), it["link"], it.get("snippet", ""), query)

    out["findings"] = [{"id": s.id, "title": s.title, "url": s.url,
                        "snippet": s.snippet} for s in reg.items]
    if not reg.items:
        out["reassessment"] = []
        out["note"] = "لم يعثر البحث على شيء عن الفجوات"
        return out

    # 3) إعادة التقييم
    stage(f"إعادة التقييم بـ{len(reg.items)} مصدراً جديداً...")
    raw2 = pipeline._run_one(
        "REASSESS", agents["A5"],
        REASSESS_BRIEF.format(
            gaps="\n".join(f"- [{g.score}] {g.idea}" for g in gaps[:max_queries]),
            findings=reg.as_block()),
        mk("A5") if mk else None)
    changes = sorted(pipeline.parse_items(raw2, "REASSESS", reg),
                     key=lambda x: -x.score)
    out["reassessment"] = [{"change": c.idea, "impact": c.detail,
                            "weight": c.score, "actions": c.risks,
                            "evidence": c.evidence} for c in changes]
    return out


def render(res: dict) -> str:
    out = ["", "=" * 58, "  النقاط العمياء", "=" * 58]
    if res.get("note"):
        out.append(f"  ⚠ {res['note']}")

    if gaps := res.get("gaps"):
        out += ["", "  ما غاب عن التحليل:", "  " + "-" * 54]
        for g in gaps[:7]:
            out.append(f"   [{g['impact']:3d}] {g['gap'][:52]}")
            if g["why"]:
                out.append(f"         {g['why'][:78]}")

    if f := res.get("findings"):
        out += ["", f"  ما وجده البحث ({len(f)} مصدر):", "  " + "-" * 54]
        for s in f[:6]:
            out.append(f"   [{s['id']}] {s['title'][:56]}")

    if r := res.get("reassessment"):
        out += ["", "  بعد إعادة التقييم:", "  " + "-" * 54]
        for c in r[:5]:
            ev = " ".join(f"[{e}]" for e in c["evidence"]) or ""
            out.append(f"   [{c['weight']:3d}] {c['change'][:50]} {ev}")
            if c["impact"]:
                out.append(f"         {c['impact'][:78]}")
            for a in c["actions"][:1]:
                out.append(f"         ← {a[:74]}")
    return "\n".join(out)


if __name__ == "__main__":
    import glob
    from pathlib import Path

    args = sys.argv[1:]
    if args and Path(args[0]).is_file():
        path = args[0]
    else:
        files = sorted(glob.glob("output/2026-*.md"),
                       key=lambda f: Path(f).stat().st_mtime, reverse=True)
        if not files:
            print("لا توجد تقارير. الاستخدام: python blindspots.py <ملف.md>")
            sys.exit(1)
        path = files[0]

    print(f"التحليل: {Path(path).name}\n")
    text = Path(path).read_text(encoding="utf-8")
    print(render(find(text, on_stage=lambda m: print("·", m, flush=True))))
