"""
التوأم الرقمي للمنافس ومحاكي السيناريوهات.

التوأم يتنبأ بردّ فعل منافس على حركة تنويها، والمحاكي يقيس أثر تغيّر
متغيّر على احتمال النجاح.

قاعدة صارمة في العرض: **لا يُقدَّم التوقع كحقيقة أبداً**. كل مخرَج يحمل
احتمالاً وثقة وأدلة و"ما لا نعرفه" - لأن التوقع بلا هذه الأربعة يتحوّل
إلى ادعاء، وادعاء خاطئ في قرار تجاري أسوأ من صمت.

الاحتمال الأولي يُحسب في الكود من الأنماط المرصودة (كم مرة خفّض السعر
سابقاً؟)، ثم يعدّله النموذج بالسياق. هكذا لا يخترع النموذج احتمالاً من
فراغ ولا نتجاهل ما يعرفه عن السوق.
"""
from __future__ import annotations

import json
import sys

import competitors as C

# ثقة التنبؤ حسب كثافة السجل - رقم قليل الشواهد لا يستحق ثقة عالية
def confidence_from(n_moves: int, n_facts: int) -> tuple[str, float]:
    weight = n_moves * 2 + n_facts
    if weight >= 12:
        return "عالية", 0.8
    if weight >= 5:
        return "متوسطة", 0.55
    if weight >= 1:
        return "منخفضة", 0.3
    return "لا تكفي", 0.1


def base_probability(pats: dict, move_type: str) -> int:
    """
    احتمال أولي من التكرار المرصود.

    إن خفّض السعر في 67% من حركاته السابقة، فهذا أساس أصدق من تخمين
    النموذج. نبدأ منه ثم نترك للنموذج تعديله بالسياق.
    """
    share = pats.get("move_share", {}).get(move_type)
    if share is None:
        return 25 if pats.get("total_moves") else 15
    return max(5, min(90, int(share)))


TWIN_BRIEF = """أنت محاكي سلوك منافس. لا تخترع - استند لسجلّه المرصود.

--- ملف المنافس ---
{profile}
--- نهاية ---

--- أنماطه المرصودة (محسوبة من سجلّه، لا من تخمين) ---
{patterns}
--- نهاية ---

--- حركتنا المزمعة ---
{action}
--- نهاية ---

--- الاحتمالات الأولية المحسوبة من تكرار سلوكه السابق ---
{priors}
--- نهاية ---

توقّع ردّ فعله. لكل ردّ محتمل أعد عنصراً.

عدّل الاحتمال الأولي صعوداً أو هبوطاً بحسب السياق، واذكر سبب التعديل.
إن لم تجد سبباً للتعديل، أبقِ الاحتمال كما هو.

أعد JSON صالحاً فقط:
{{"items": [
  {{"idea": "ردّ الفعل المتوقع في سطر",
    "detail": "لماذا هذا الردّ تحديداً - استند لسجلّه",
    "score": 0-100,
    "risks": ["ما الذي يجعل هذا الردّ مؤذياً لنا"],
    "evidence": [],
    "counterarguments": ["ما الذي قد يمنعه من هذا الردّ"]}}
]}}

`score` = احتمال حدوث هذا الردّ من 100."""


def predict_response(competitor: str, our_action: str,
                     on_stage=None) -> dict:
    """
    يتنبأ بردّ فعل منافس. يعيد dict فيه التوقعات والثقة وما لا نعرفه.
    """
    import main
    import pipeline
    import sources as S

    def stage(m):
        if on_stage:
            on_stage(m)

    prof = C.profile(competitor)
    if not prof:
        return {"error": f"لا يوجد ملف للمنافس «{competitor}» - أضفه أولاً"}

    pats = C.patterns(prof["id"])
    conf_label, conf_val = confidence_from(len(prof["moves"]),
                                           len(prof["current"]))

    priors = {m: base_probability(pats, m) for m in C.MOVE_TYPES}
    priors = {k: v for k, v in sorted(priors.items(), key=lambda x: -x[1])[:6]}

    stage("محاكاة ردّ الفعل...")
    agents = main.build_agents()
    mk = agents.get("_rebuild")
    raw = pipeline._run_one(
        "TWIN", agents["A4"],
        TWIN_BRIEF.format(
            profile=json.dumps({k: prof[k] for k in
                                ("name", "sector", "country", "current")},
                               ensure_ascii=False, indent=1)[:1800],
            patterns=json.dumps(pats, ensure_ascii=False)[:900],
            action=our_action,
            priors=json.dumps(priors, ensure_ascii=False)),
        mk("A4") if mk else None)

    reg = S.Registry()
    items = pipeline.parse_items(raw, "TWIN", reg)

    # ما لا نعرفه - يُبنى في الكود من فجوات السجل لا من النموذج
    unknowns = []
    if not prof["moves"]:
        unknowns.append("لا توجد حركات مرصودة لهذا المنافس إطلاقاً")
    if "سعر الوحدة" not in prof["current"]:
        unknowns.append("سعره الحالي غير معروف")
    if len(prof["current"]) < 4:
        unknowns.append(f"ملفه ناقص - {len(prof['current'])} سمة فقط")

    return {
        "competitor": prof["name"],
        "our_action": our_action,
        "confidence": conf_label,
        "confidence_value": conf_val,
        "evidence_base": {"moves": len(prof["moves"]),
                          "facts": len(prof["current"]),
                          "history_entries": len(prof["history"])},
        "priors": priors,
        "predictions": [
            {"response": it.idea, "probability": it.score,
             "why": it.detail, "impact_on_us": it.risks,
             "what_could_prevent": it.counterarguments}
            for it in sorted(items, key=lambda x: -x.score)
        ],
        "unknowns": unknowns,
    }


def render(pred: dict) -> str:
    """عرض نصّي يفصل التوقع عن الحقيقة بوضوح."""
    if "error" in pred:
        return pred["error"]

    eb = pred["evidence_base"]
    out = ["", "=" * 56,
           f"  توأم رقمي — {pred['competitor']}",
           "=" * 56,
           f"  حركتنا: {pred['our_action']}",
           "",
           f"  ⚠ هذه **توقعات لا حقائق**.",
           f"  الثقة: {pred['confidence']} ({pred['confidence_value']:.0%})",
           f"  قاعدة الأدلة: {eb['moves']} حركة · {eb['facts']} سمة · "
           f"{eb['history_entries']} تغيّر مسجّل",
           ""]

    for i, p in enumerate(pred["predictions"], 1):
        out += [f"  {i}. {p['response']}",
                f"     الاحتمال : {p['probability']}%",
                f"     السبب    : {p['why']}"]
        if p["impact_on_us"]:
            out.append(f"     أثره علينا: {'؛ '.join(p['impact_on_us'][:2])}")
        if p["what_could_prevent"]:
            out.append(f"     ما يمنعه  : {'؛ '.join(p['what_could_prevent'][:2])}")
        out.append("")

    if pred["unknowns"]:
        out.append("  ما لا نعرفه:")
        out += [f"     · {u}" for u in pred["unknowns"]]
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("الاستخدام: python twin.py <اسم المنافس> <حركتنا المزمعة>")
        print("مثال: python twin.py \"شركة المياه المتقدمة\" \"إطلاق منتج RO أرخص 20%\"")
        sys.exit(0)
    print(render(predict_response(sys.argv[1], " ".join(sys.argv[2:]),
                                  on_stage=lambda m: print("·", m, flush=True))))
