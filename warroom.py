"""
غرفة الحرب - مجلس من سبعة أدوار متعارضة يتناظر على قرار.

                      CEO
        ┌──────┬──────┼──────┬──────┐
       CFO    CTO    COO   السوق  المنافسون
                            المخاطر
                        محامي الشيطان
                            ↓
                        مناظرة
                            ↓
                        قرار نهائي

لماذا سبعة أدوار لا وكيل واحد؟ لأن الوكيل الواحد يوافق نفسه. الأدوار
هنا **متعارضة المصالح بالتصميم**: المالي يريد خفض الإنفاق والتقني يريد
رفعه، ومحامي الشيطان يهاجم ما اتفق عليه الجميع.

قاعدة صارمة: **مخرَج قصير منظّم لكل دور، لا تقرير**. سبعة تقارير طويلة
تُنتج ضجيجاً وتستهلك السقف الزمني - وقد رُصد هذا فعلياً حين أنتج وكيل
132 ألف حرف فضاع عمله كاملاً.

المناظرة تُدار في الكود: نحسب أين اتفقوا وأين اختلفوا رقمياً، ثم نعرض
نقاط الخلاف وحدها على محامي الشيطان. عرض كل شيء عليه يميّع نقده.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass, field

# (رمز، دور، هدف، خلفية، حرارة)
BOARD = [
    ("CEO", "الرئيس التنفيذي",
     "الحكم على القرار من زاوية الاستراتيجية طويلة المدى وموقع الشركة",
     "قائد يفكر بأفق خمس سنوات. يسأل: هل يبني هذا القرار ميزة تدوم أم "
     "يشتري إيراداً مؤقتاً؟", 0.4),

    ("CFO", "المدير المالي",
     "الحكم من زاوية النقد والهامش وفترة الاسترداد",
     "محافظ بطبعه. يكره الافتراضات المتفائلة ويسأل دائماً: من أين يأتي "
     "النقد في الشهر الثالث إن تأخر التحصيل؟", 0.25),

    ("CTO", "المدير التقني",
     "الحكم على الجدوى التقنية وما تتطلبه من قدرات",
     "مهندس عملي. يعرف الفرق بين ما يعمل في العرض التقديمي وما يعمل في "
     "الميدان تحت الغبار والحرارة.", 0.35),

    ("COO", "مدير العمليات",
     "الحكم على القدرة التنفيذية: الفريق، الموردون، سلسلة الإمداد",
     "يفكر بالأشخاص والأيام. يسأل: من سينفّذ هذا فعلاً، ومتى يصل "
     "الجزء الذي يعطّل كل شيء إن تأخر؟", 0.3),

    ("MKT", "محلل السوق",
     "حجم الطلب الفعلي واستعداد العملاء للدفع",
     "يميّز بين سوق موجود وسوق متخيَّل. يسأل: من دفع مقابل هذا فعلاً "
     "من قبل، وكم؟", 0.45),

    ("RISK", "مدير المخاطر والامتثال",
     "المخاطر التنظيمية والقانونية والتشغيلية",
     "يعرف أن المشاريع تموت بالتراخيص والضمانات لا بالتقنية. يسأل: ما "
     "الذي يوقف هذا بقرار إداري واحد؟", 0.25),

    ("DEVIL", "محامي الشيطان",
     "مهاجمة ما اتفق عليه المجلس - خاصة ما بدا بديهياً",
     "لا يملك رأياً خاصاً به. مهمته الوحيدة أن يجعل الإجماع مكلفاً. "
     "كلما زاد اتفاق المجلس زادت شراسته.", 0.6),
]

SCHEMA = """أعد JSON صالحاً فقط، بلا نص قبله أو بعده:

{"recommendation": "BUILD أو WAIT أو DROP",
 "score": 0-100,
 "confidence": 0.0-1.0,
 "claim": "حكمك في سطر واحد",
 "assumptions": ["افتراض تبني عليه"],
 "risks": ["خطر من زاويتك أنت"],
 "evidence": ["S1"]}

قواعد ملزِمة:
- **سطور قصيرة** - لا تكتب تقريراً. المجلس كله يُقرأ معاً.
- `evidence` معرّفات من المصادر المرفقة حصراً إن وُجدت.
- احكم من **زاويتك أنت** لا من زاوية عامة. المالي يحكم بالنقد لا بالجاذبية."""

ROLE_BRIEF = """أنت **{role}** في مجلس يقرّر.

{backstory}

--- القرار المطروح ---
{decision}
--- نهاية ---

{context}

{schema}"""

DEVIL_BRIEF = """أنت **محامي الشيطان**. المجلس قال رأيه، وهذه نقاط اتفاقه.

--- القرار ---
{decision}
--- نهاية ---

--- ما اتفق عليه المجلس (هدفك تدميره) ---
{consensus}
--- نهاية ---

--- حيث اختلفوا ---
{conflicts}
--- نهاية ---

هاجم الإجماع تحديداً. الاتفاق الواسع علامة على افتراض مشترك لم يُفحص،
لا على صحة. ابحث عنه.

{schema}"""


@dataclass
class Opinion:
    code: str
    role: str
    recommendation: str = "WAIT"
    score: int = 0
    confidence: float = 0.5
    claim: str = ""
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _parse(raw: str, code: str, role: str, valid_ids: set[str]) -> Opinion:
    import pipeline

    o = Opinion(code=code, role=role)
    txt = (raw or "").strip()
    data = None
    for cand in (txt, pipeline._first_json_object(txt)):
        if not cand:
            continue
        try:
            data = json.loads(cand.strip("`").replace("json\n", "", 1))
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(data, dict):
        objs = pipeline._salvage_objects(txt, cap=3)
        data = objs[0] if objs else {}

    rec = str(data.get("recommendation", "WAIT")).upper()
    o.recommendation = rec if rec in ("BUILD", "WAIT", "DROP") else "WAIT"
    try:
        o.score = max(0, min(100, int(data.get("score") or 0)))
    except (TypeError, ValueError):
        o.score = 0
    try:
        o.confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        o.confidence = 0.5
    o.claim = str(data.get("claim") or data.get("idea") or "").strip()
    o.assumptions = [str(x) for x in (data.get("assumptions") or [])][:4]
    o.risks = [str(x) for x in (data.get("risks") or [])][:4]
    o.evidence = [str(x).upper().strip("[] ") for x in (data.get("evidence") or [])
                  if str(x).upper().strip("[] ") in valid_ids][:6]
    return o


def analyze(opinions: list[Opinion]) -> dict:
    """
    يحلّل المجلس رقمياً: أين اتفقوا، أين اختلفوا، ومن الشاذّ.

    في الكود لا بالنموذج: هذا حساب تشتّت لا استدلال.

    الأصوات الصامتة تُستبعد من الحساب: عضو أخفق في إنتاج JSON صالح يبقى
    على القيم الافتراضية (score=0, claim فارغ) فيظهر كصوت شاذّ ويجرّ
    المتوسط لأسفل - وهو تشويش لا رأي. رُصد فعلياً حين ظهر محلل السوق
    بدرجة صفر بلا حكم.
    """
    voiced = [o for o in opinions if o.claim.strip()]
    silent = [o.role for o in opinions if not o.claim.strip()]
    opinions = voiced or opinions          # لو صمت الجميع نعرض ما لدينا
    scores = [o.score for o in opinions] or [0]
    recs = [o.recommendation for o in opinions]
    counts = {r: recs.count(r) for r in set(recs)}
    top = max(counts, key=counts.get)

    spread = (statistics.pstdev(scores) if len(scores) > 1 else 0)
    mean = statistics.mean(scores)
    outliers = [o for o in opinions if abs(o.score - mean) > max(15, spread * 1.5)]

    # افتراض تكرّر عند ثلاثة أو أكثر = افتراض جماعي غير مفحوص
    seen: dict[str, int] = {}
    for o in opinions:
        for a in o.assumptions:
            k = a.strip()[:45]
            seen[k] = seen.get(k, 0) + 1
    shared = [k for k, v in seen.items() if v >= 3]

    return {
        "majority": top,
        "split": counts,
        "mean_score": round(mean),
        "spread": round(spread, 1),
        "unanimous": len(counts) == 1,
        "outliers": [{"role": o.role, "score": o.score,
                      "recommendation": o.recommendation, "claim": o.claim}
                     for o in outliers],
        "shared_assumptions": shared,
        "all_risks": [r for o in opinions for r in o.risks],
        "silent": silent,
        "voted": len(opinions),
    }


def convene(decision: str, context: str = "", valid_ids: set[str] | None = None,
            on_stage=None) -> dict:
    """يعقد الجلسة: ستة أدوار متوازية، ثم محامي الشيطان على إجماعهم."""
    import main
    import pipeline

    def stage(m):
        if on_stage:
            on_stage(m)

    valid_ids = valid_ids or set()
    agents = main.build_agents()
    mk = agents.get("_rebuild")

    # ستة أدوار متوازية - محامي الشيطان ينتظر ليرى إجماعهم
    members = [b for b in BOARD if b[0] != "DEVIL"]
    stage(f"{len(members)} أعضاء يحكمون بالتوازي...")

    pool = {"CEO": "A5", "CFO": "A6", "CTO": "A1",
            "COO": "A7", "MKT": "A2", "RISK": "A3"}
    briefs = {code: ROLE_BRIEF.format(role=role, backstory=back,
                                      decision=decision,
                                      context=context, schema=SCHEMA)
              for code, role, _goal, back, _t in members}

    raw = pipeline._run_wave([b[0] for b in members],
                             {b[0]: agents[pool[b[0]]] for b in members},
                             briefs,
                             on_note=None)
    # _run_wave يمرّر مصنع إعادة البناء بمفاتيح الطاقم لا بأدوارنا
    opinions = [_parse(raw.get(code, ""), code, role, valid_ids)
                for code, role, _g, _b, _t in members]

    board = analyze(opinions)
    stage(f"الأغلبية: {board['majority']} · التشتّت {board['spread']}")

    # محامي الشيطان
    stage("محامي الشيطان يهاجم الإجماع...")
    devil_raw = pipeline._run_one(
        "DEVIL", agents["RED"],
        DEVIL_BRIEF.format(
            decision=decision,
            consensus="\n".join(
                [f"- الأغلبية توصي بـ{board['majority']} "
                 f"(متوسط {board['mean_score']})"] +
                [f"- افتراض مشترك: {a}" for a in board["shared_assumptions"]] or
                ["- لا إجماع واضح"]),
            conflicts="\n".join(
                f"- {o['role']}: {o['recommendation']} ({o['score']}) — {o['claim']}"
                for o in board["outliers"]) or "- لا شواذّ",
            schema=SCHEMA),
        mk("RED") if mk else None)
    devil = _parse(devil_raw, "DEVIL", "محامي الشيطان", valid_ids)
    opinions.append(devil)

    # القرار النهائي: متوسط مرجّح بالثقة، مخصوم بقوة اعتراض محامي الشيطان
    voting = [o for o in opinions[:-1] if o.claim.strip()] or opinions[:-1]
    weighted = sum(o.score * o.confidence for o in voting)
    wsum = sum(o.confidence for o in voting) or 1
    final = weighted / wsum

    if devil.recommendation == "DROP":
        final *= 1 - 0.30 * devil.confidence
    elif devil.recommendation == "WAIT":
        final *= 1 - 0.15 * devil.confidence

    # الإجماع التام مريب - يخصم قليلاً لأنه غالباً افتراض مشترك لا فحص
    if board["unanimous"]:
        final *= 0.95

    final = max(0, min(100, round(final)))
    verdict = "BUILD" if final >= 70 else "WAIT" if final >= 45 else "DROP"

    return {"decision": decision, "final_score": final, "verdict": verdict,
            "board": board, "devil": vars(devil),
            "opinions": [vars(o) for o in opinions]}


def render(res: dict) -> str:
    b = res["board"]
    out = ["", "=" * 58,
           f"  غرفة الحرب — {res['decision'][:44]}",
           "=" * 58, ""]
    for o in res["opinions"]:
        mark = "🔴" if o["code"] == "DEVIL" else "  "
        if not o["claim"].strip():
            out.append(f"{mark} {o['role']:<20} — لم يُنتج حكماً (مُستبعَد)")
            continue
        out.append(f"{mark} {o['role']:<20} {o['recommendation']:<6} "
                   f"{o['score']:>3}  (ثقة {o['confidence']:.0%})")
        out.append(f"     {o['claim'][:88]}")
    out += ["",
            f"  الأغلبية    : {b['majority']}   {b['split']}",
            f"  متوسط الدرجة: {b['mean_score']}   التشتّت: {b['spread']}"
            f"   (أصوات محتسبة: {b['voted']})"]
    if b.get("silent"):
        out.append(f"  مُستبعَدون (بلا حكم): {'، '.join(b['silent'])}")
    if b["shared_assumptions"]:
        out.append("  افتراضات جماعية غير مفحوصة:")
        out += [f"     · {a}" for a in b["shared_assumptions"][:3]]
    if b["outliers"]:
        out.append("  أصوات شاذّة:")
        out += [f"     · {o['role']}: {o['recommendation']} ({o['score']})"
                for o in b["outliers"]]
    out += ["", "-" * 58,
            f"  القرار النهائي: **{res['verdict']}**   ({res['final_score']}/100)",
            "-" * 58]
    if res["devil"]["claim"]:
        out.append(f"  اعتراض محامي الشيطان: {res['devil']['claim'][:88]}")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python warroom.py <القرار>")
        print('مثال: python warroom.py "إطلاق خط محطات RO للمزارع الصغيرة"')
        sys.exit(0)
    print(render(convene(" ".join(sys.argv[1:]),
                         on_stage=lambda m: print("·", m, flush=True))))
