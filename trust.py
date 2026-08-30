"""
طبقات ثقة المصادر.

المشكلة: المحكّم أعطى التقارير **4/10 في الاستناد** وسمّى السبب حرفياً:
«مواقع تسويقية بدل مراجع محكّمة». وهو محقّ - Serper يعيد ما يتصدّر جوجل
لا ما يصحّ فنياً، والنظام كان يعامل كل المصادر بالتساوي.

الحل: وزن لكل مصدر حسب نطاقه، يدخل في تسجيل العناصر. فكرة مستندة إلى
دورية محكّمة تعلو على فكرة مستندة إلى مدونة مورّد - وهذا فرق يجب أن
يظهر في الدرجة لا في نية القارئ.

التصنيف بالنطاق لا بالمحتوى: تقييم المحتوى يحتاج نموذجاً ووقتاً، بينما
النطاق إشارة قوية ومجانية وفورية. ليست مثالية - مدونة ممتازة على نطاق
تجاري ستُظلَم - لكنها أفضل بكثير من المساواة الكاملة.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# الطبقات: (الاسم، الوزن، الوصف)
TIERS = {
    "peer_reviewed": (1.00, "دورية محكّمة أو قاعدة بحثية"),
    "academic":      (0.90, "جامعة أو مركز بحثي"),
    "government":    (0.85, "جهة حكومية أو تنظيمية"),
    "standards":     (0.85, "هيئة معايير أو منظمة دولية"),
    "industry_body": (0.75, "جمعية أو هيئة صناعية متخصصة"),
    "manufacturer":  (0.60, "مصنّع - دقيق فنياً لكنه منحاز لمنتجه"),
    "news":          (0.50, "صحافة عامة"),
    "trade_press":   (0.55, "صحافة متخصصة"),
    "vendor":        (0.35, "موقع مورّد أو تسويقي"),
    "social":        (0.20, "منصة تواصل أو منتدى"),
    "unknown":       (0.45, "غير مصنّف"),
}

# قواعد التصنيف بالترتيب - أول مطابقة تفوز
RULES: list[tuple[str, str]] = [
    ("peer_reviewed", r"(sciencedirect|springer|wiley|tandfonline|mdpi|nature|"
                      r"acs\.org|iwaponline|elsevier|doi\.org|pubmed|scholar\.google|"
                      r"researchgate|arxiv|jstor|sci-hub|hindawi|frontiersin|"
                      r"plos\.org|journals\.sagepub|cambridge\.org|academic\.oup|"
                      r"ieee\.org|asme\.org|rsc\.org|aip\.org|degruyter|"
                      r"semanticscholar|core\.ac\.uk|openalex|europepmc|"
                      r"biomedcentral|dwt\.|desalinationandwatertreatment)"),
    ("academic",      r"(\.edu\b|\.edu\.|\.ac\.[a-z]{2}|university|univ\.|jamia|"
                      r"nu\.edu\.om|squ\.edu\.om|kaust)"),
    # لا `\.om$`: المطابقة تجري على "النطاق + الرابط" فالمرساة تقع في آخر
    # الرابط لا آخر النطاق - قاعدة ميتة لا تُطابق شيئاً. والأهم أنها لو
    # طابقت لصنّفت كل نطاق عُماني تجاري كجهة حكومية.
    ("government",    r"(\.gov\b|\.gov\.|\.gob\.|\.gov\.om|europa\.eu|"
                      r"un\.org|unep|who\.int|worldbank|"
                      r"\bosws\.om|namawater|tenderboard\.gov)"),
    ("standards",     r"(iso\.org|astm\.org|nsf\.org|awwa\.org|ansi\.org|"
                      r"bsigroup|din\.de|iec\.ch)"),
    ("industry_body", r"(idadesal|desalination\.com|globalwaterintel|gwiwater|"
                      r"waterworld|iwa-network|ewa-online)"),
    ("manufacturer",  r"(dupont|filmtec|toray|lgchem|hydranautics|nitto|"
                      r"veolia|suez|xylem|grundfos|danfoss|pentair|toraywater)"),
    ("trade_press",   r"(smartwatermagazine|watertechonline|aquatechtrade|"
                      r"filtsep|attaqa|energy-?magazine)"),
    ("social",        r"(facebook|twitter|x\.com|linkedin|instagram|tiktok|"
                      r"youtube|reddit|quora|pinterest|t\.me)"),
    ("news",          r"(news|aljazeera|bbc|reuters|cnn|alarabiya|omandaily|"
                      r"atheer|shabiba|timesofoman|muscatdaily)"),
]

# نطاقات تجارية عامة → مورّد ما لم تطابق قاعدة أقوى
VENDOR_HINT = re.compile(r"(shop|store|buy|price|منتجات|اسعار|توريد|"
                         r"water\w*\.com|ro\w*\.com)", re.I)


def classify(url: str) -> tuple[str, float]:
    """يعيد (الطبقة، الوزن) لرابط."""
    if not url:
        return "unknown", TIERS["unknown"][0]

    try:
        host = (urlparse(url).netloc or url).lower()
    except ValueError:
        host = url.lower()
    probe = f"{host} {url.lower()}"

    for tier, pattern in RULES:
        if re.search(pattern, probe):
            return tier, TIERS[tier][0]

    if VENDOR_HINT.search(probe):
        return "vendor", TIERS["vendor"][0]
    return "unknown", TIERS["unknown"][0]


def weight(url: str) -> float:
    return classify(url)[1]


def evidence_weight(urls: list[str]) -> float:
    """
    وزن مجموعة أدلة، بين 0 و1.

    نأخذ **أقوى مصدر** لا المتوسط: فكرة مسنَدة بورقة محكّمة وثلاث مدونات
    أقوى من فكرة بأربع مدونات، والمتوسط يخفي ذلك. ثم نكافئ التعدد قليلاً.
    """
    if not urls:
        return 0.0
    ws = [weight(u) for u in urls if u]
    if not ws:
        return 0.0
    best = max(ws)
    diversity = min(0.15, 0.05 * (len(ws) - 1))
    return min(1.0, best + diversity)


def label(url: str) -> str:
    tier, w = classify(url)
    return f"{TIERS[tier][1]} ({w:.2f})"


def audit_registry(reg) -> dict:
    """يصنّف سجل مصادر كاملاً ويعيد التوزيع."""
    dist: dict[str, int] = {}
    weights = []
    for s in getattr(reg, "items", []):
        tier, w = classify(s.url)
        dist[tier] = dist.get(tier, 0) + 1
        weights.append(w)
    return {
        "distribution": dist,
        "count": len(weights),
        "avg_weight": round(sum(weights) / len(weights), 2) if weights else 0,
        "best_weight": round(max(weights), 2) if weights else 0,
        "strong": sum(1 for w in weights if w >= 0.75),
        "weak": sum(1 for w in weights if w <= 0.40),
    }


def as_markdown(a: dict) -> str:
    if not a.get("count"):
        return ""
    rows = "\n".join(
        f"| {TIERS.get(t, ('', t))[1]} | {n} | {TIERS.get(t, (0,))[0]:.2f} |"
        for t, n in sorted(a["distribution"].items(),
                           key=lambda x: -TIERS.get(x[0], (0,))[0]))
    return ("\n\n---\n\n## جودة المصادر\n\n"
            "*مصنّفة بالنطاق: الدوريات المحكّمة والجهات الحكومية تعلو على "
            "مواقع المورّدين.*\n\n"
            "| الطبقة | العدد | الوزن |\n|---|---|---|\n" + rows +
            f"\n\n**متوسط الوزن:** {a['avg_weight']} · "
            f"**قوية:** {a['strong']} · **ضعيفة:** {a['weak']}\n")


if __name__ == "__main__":
    import sys
    urls = sys.argv[1:] or [
        "https://www.sciencedirect.com/science/article/pii/S0011916423001",
        "https://nu.edu.om/blog/desalination",
        "https://www.omandaily.om/economy/news",
        "https://ar.moruiwater.com/products/ro-membrane",
        "https://www.facebook.com/groups/water",
        "https://www.dupont.com/water/filmtec.html",
        "https://iso.org/standard/12345.html",
    ]
    for u in urls:
        t, w = classify(u)
        print(f"  {w:.2f}  {TIERS[t][1]:<32} {u[:56]}")
    print(f"\n  وزن المجموعة: {evidence_weight(urls):.2f}")
