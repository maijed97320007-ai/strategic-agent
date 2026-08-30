"""
محاكي السيناريوهات - ماذا يحدث لو تغيّر متغيّر؟

    الأساس: احتمال النجاح 78%
    سعر المنافس -20%  →  61%
    الطلب +20%        →  87%
    رأس المال +25%    →  65%

الحساب في الكود لا في النموذج: مرونات معروفة تُطبَّق على متغيّرات مالية،
فالنتيجة قابلة لإعادة الإنتاج ولا تتغيّر بين تشغيلتين. النموذج يشرح
النتيجة ويضيف ما يفوت الحساب، لكنه لا يخترع الأرقام.

الأوزان مصدرها مرونات شائعة في تحليل الجدوى، لا قياس ميداني - فهي
تقديرات صريحة لا حقائق. غيّرها في ELASTICITY لو كان لديك أفضل.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

# أثر تغيّر 1% في كل متغيّر على احتمال النجاح (نقطة مئوية).
# سالب = يضرّ. القيم تقديرية معلنة، لا مقيسة.
ELASTICITY = {
    # الإشارة تعني: أثر ارتفاع المتغيّر 1% على احتمال نجاحنا.
    # سعر المنافس موجب عمداً: ارتفاعه يفيدنا وانخفاضه يضرّنا. كانت
    # الإشارة سالبة فأنتجت نتيجة معكوسة - "المنافس خفض 35%" رفع
    # احتمال نجاحنا إلى 99% بدل أن يخفضه.
    "competitor_price": +0.85,
    "demand": +0.45,
    "our_price": -0.35,          # رفع سعرنا يضرّ لكن أقل
    "capex": -0.55,
    "opex": -0.40,
    "margin": +0.50,
    "market_size": +0.30,
    "lead_time": -0.25,
}

LABELS = {
    "competitor_price": "سعر المنافس",
    "demand": "الطلب",
    "our_price": "سعرنا",
    "capex": "رأس المال",
    "opex": "التشغيل",
    "margin": "الهامش",
    "market_size": "حجم السوق",
    "lead_time": "مدة التنفيذ",
}


@dataclass
class Scenario:
    label: str
    changes: dict[str, float]          # اسم المتغيّر → نسبة التغيّر %
    probability: int = 0
    delta: int = 0
    drivers: list[str] = field(default_factory=list)


def simulate(base: int, changes: dict[str, float]) -> tuple[int, list[str]]:
    """
    يطبّق التغيّرات على احتمال الأساس ويعيد (الاحتمال، أسباب التغيّر).

    التشبّع مقصود: تغيّر 200% لا يعني ضعف أثر 100%، فنخمد ما تجاوز 50%
    حتى لا تنتج أرقام سخيفة من مدخلات متطرفة.
    """
    prob = float(base)
    drivers = []
    for var, pct in changes.items():
        e = ELASTICITY.get(var)
        if e is None:
            continue
        damped = pct if abs(pct) <= 50 else (50 + (abs(pct) - 50) * 0.4) * (1 if pct > 0 else -1)
        impact = damped * e
        prob += impact
        if abs(impact) >= 1:
            sign = "+" if impact > 0 else ""
            drivers.append(f"{LABELS.get(var, var)} {pct:+.0f}% → {sign}{impact:.0f} نقطة")
    return max(1, min(99, round(prob))), drivers


def standard_set(base: int) -> list[Scenario]:
    """السيناريوهات القياسية التي تُسأل دائماً."""
    presets = [
        ("سعر المنافس -20%", {"competitor_price": -20}),
        ("سعر المنافس -35%", {"competitor_price": -35}),
        ("الطلب +20%", {"demand": +20}),
        ("الطلب -30%", {"demand": -30}),
        ("رأس المال +25%", {"capex": +25}),
        ("تشغيل +15% ومدة +20%", {"opex": +15, "lead_time": +20}),
        ("الأسوأ: منافس -25% وطلب -20%",
         {"competitor_price": -25, "demand": -20}),
        ("الأفضل: طلب +25% وهامش +15%", {"demand": +25, "margin": +15}),
    ]
    out = []
    for label, ch in presets:
        p, d = simulate(base, ch)
        out.append(Scenario(label=label, changes=ch, probability=p,
                            delta=p - base, drivers=d))
    return out


EXPLAIN_BRIEF = """أمامك محاكاة سيناريوهات لقرار تجاري. الأرقام محسوبة
بمرونات ثابتة في الكود - لا تعدّلها ولا تعِد حسابها.

--- القرار ---
{decision}

--- الأساس ---
احتمال النجاح: {base}%

--- السيناريوهات المحسوبة ---
{table}
--- نهاية ---

مهمتك: ما الذي يفوت هذا الحساب؟

أعد JSON صالحاً فقط:
{{"items": [
  {{"idea": "عامل لم يدخل الحساب",
    "detail": "كيف يغيّر الصورة - جملتان",
    "score": 0-100,
    "risks": ["الأثر إن تحقق"],
    "evidence": [], "counterarguments": []}}
]}}

ركّز على: عوامل غير خطية، عتبات انهيار، ردود فعل متسلسلة، وقيود محلية
لا تظهر في مرونة رقمية. `score` = أهمية العامل."""


def run(decision: str, base: int = 70, extra: dict[str, float] | None = None,
        explain: bool = True, on_stage=None) -> dict:
    """محاكاة كاملة: حساب + شرح لما يفوت الحساب."""
    def stage(m):
        if on_stage:
            on_stage(m)

    scenarios = standard_set(base)
    if extra:
        p, d = simulate(base, extra)
        scenarios.insert(0, Scenario(label="سيناريوك", changes=extra,
                                     probability=p, delta=p - base, drivers=d))

    scenarios.sort(key=lambda s: -s.probability)
    missing = []

    if explain:
        stage("البحث عمّا يفوت الحساب...")
        try:
            import main
            import pipeline
            import sources as S

            table = "\n".join(f"- {s.label}: {s.probability}% ({s.delta:+d})"
                              for s in scenarios)
            agents = main.build_agents()
            mk = agents.get("_rebuild")
            raw = pipeline._run_one(
                "SIM", agents["A6"],
                EXPLAIN_BRIEF.format(decision=decision, base=base, table=table),
                mk("A6") if mk else None)
            missing = [{"factor": i.idea, "why": i.detail,
                        "importance": i.score, "impact": i.risks}
                       for i in sorted(pipeline.parse_items(raw, "SIM", S.Registry()),
                                       key=lambda x: -x.score)]
        except Exception as e:
            missing = [{"factor": f"تعذّر الشرح: {type(e).__name__}",
                        "why": "", "importance": 0, "impact": []}]

    return {"decision": decision, "base": base,
            "scenarios": [vars(s) for s in scenarios],
            "missing_factors": missing}


def render(res: dict) -> str:
    out = ["", "=" * 56,
           f"  محاكاة — {res['decision']}",
           "=" * 56,
           f"  الأساس: {res['base']}% احتمال نجاح",
           "",
           f"  {'السيناريو':<34} {'النتيجة':>8} {'الفرق':>7}",
           f"  {'-'*34} {'-'*8} {'-'*7}"]
    for s in res["scenarios"]:
        out.append(f"  {s['label'][:34]:<34} {s['probability']:>7}% {s['delta']:>+7}")
    out.append("")
    if res["missing_factors"]:
        out.append("  ما يفوت الحساب:")
        for m in res["missing_factors"][:5]:
            out.append(f"     [{m['importance']:3d}] {m['factor']}")
            if m["why"]:
                out.append(f"           {m['why'][:96]}")
    out.append("")
    out.append("  ⚠ المرونات تقديرية معلنة لا مقيسة — عدّلها في ELASTICITY")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python scenario.py <القرار> [احتمال الأساس]")
        print('مثال: python scenario.py "إطلاق خط محطات RO للمزارع" 75')
        sys.exit(0)
    decision = sys.argv[1]
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 70
    print(render(run(decision, base, on_stage=lambda m: print("·", m, flush=True))))
