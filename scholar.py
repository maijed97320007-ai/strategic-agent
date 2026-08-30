"""
مصادر علمية مجانية بلا مفاتيح.

المشكلة المقيسة: Serper يعيد ما يتصدّر جوجل - مواقع مورّدين ومنصات تواصل.
حتى بعد حصر النطاق بـ `site:` بقي ثلث السجل تسويقياً، لأن الدوريات المحكّمة
لا تتصدّر البحث العام أصلاً. حصر النطاق يعالج العَرَض لا السبب.

الحل: نسأل قواعد البيانات العلمية مباشرة بدل أن نرجو محرّك بحث تجارياً أن
يعيدها. OpenAlex وCrossref مفتوحتان بلا مفتاح ولا حصّة، وكل نتيجة منهما
تحمل DOI - أي وزن 1.00 في trust.py بلا تخمين.

قيد مهم رُصد بالقياس: الاستعلام العربي على OpenAlex أعاد **صفر** نتيجة
(«التحديات التشغيلية بعد تركيب أغشية التناضح العكسي» → 0 من 250 مليون عمل).
الفهرسة إنجليزية، فنترجم الموضوع بنداء واحد رخيص ونخزّنه.

لماذا لا نكتفي بها ونحذف Serper؟ لأن الورقة المحكّمة تجيب «ما الذي يحدث
فيزيائياً» ولا تجيب «كم سعره في عُمان اليوم». التقرير يحتاج الاثنين -
الفرق أن الترجيح صار يعرف أيّهما أثقل.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# البريد يضع الطلب في «المسار المهذّب» لدى OpenAlex وCrossref: حصّة أعلى
# واستجابة أسرع. هو شرط استخدامهما المعلن لا بيانات شخصية تُسرّب.
UA = "IdeaAgent/1.0 (https://github.com/; mailto:research@localhost)"
TIMEOUT = 20

# روابط أصول لا أوراق: صور الملخّصات المصوّرة ومرفقات لا نصّ فيها
ASSET = re.compile(r"\.(jpe?g|png|gif|svg|webp|tiff?|mp4|zip)(\?|$)", re.I)


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _abstract(inverted: dict | None, limit: int = 320) -> str:
    """
    OpenAlex يخزّن الملخّص كفهرس معكوس (كلمة → مواضعها) لأسباب ترخيص.
    نعيد بناء النصّ بترتيب المواضع.
    """
    if not isinstance(inverted, dict) or not inverted:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for p in positions or []:
            slots.append((p, word))
    text = " ".join(w for _, w in sorted(slots))
    return text[:limit].strip()


def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(c.isascii() for c in letters) / len(letters) > 0.6


# معجم مصطلحات المجال: عربي → إنجليزي.
#
# لماذا معجم ونحن نملك نموذجاً؟ لأن نداء الترجمة كلّف **13.8 ثانية** مقيسة
# على النموذج المجاني - أبطأ خطوة مفردة في التشغيلة كلها، وهي ترجمة ستّين
# رمزاً. المعجم يغطّي المواضيع المتكرّرة بصفر مللي ثانية، والنموذج يبقى
# احتياطاً لما لا يغطّيه.
#
# الترتيب مقصود: المركّبات قبل المفردات، وإلا التهمت «مياه» عبارة
# «مياه الصرف الصحي» فضاعت الدقّة.
TERMS: list[tuple[str, str]] = [
    ("التناضح العكسي", "reverse osmosis"),
    ("التناضح الأمامي", "forward osmosis"),
    ("مياه الصرف الصحي", "wastewater"),
    ("مياه الصرف", "wastewater"),
    ("المياه المصاحبة", "produced water"),
    ("مياه البحر", "seawater"),
    ("المياه الجوفية", "groundwater"),
    ("مياه الآبار", "well water"),
    ("مياه الشرب", "drinking water"),
    ("إعادة الاستخدام", "water reuse"),
    ("إعادة استخدام", "water reuse"),
    ("الصناعي", "industrial"),
    ("الصناعية", "industrial"),
    ("البلدية", "municipal"),
    ("الزراعي", "agricultural"),
    ("الري", "irrigation"),
    ("الغسيل الكيميائي", "chemical cleaning"),
    ("المعالجة المسبقة", "pretreatment"),
    ("الضغط التفاضلي", "differential pressure"),
    ("الرفض الملحي", "salt rejection"),
    ("الانسداد الحيوي", "biofouling"),
    ("الترسيب الكلسي", "scaling"),
    ("المحلول المركّز", "brine"),
    ("التحلية", "desalination"),
    ("تحلية", "desalination"),
    ("الأغشية", "membrane"),
    ("أغشية", "membrane"),
    ("غشاء", "membrane"),
    ("الترشيح الفائق", "ultrafiltration"),
    ("الترشيح النانوي", "nanofiltration"),
    ("النترات", "nitrate"),
    ("الفلورايد", "fluoride"),
    ("الزرنيخ", "arsenic"),
    ("الليثيوم", "lithium"),
    ("الملوحة", "salinity"),
    ("العكارة", "turbidity"),
    ("التلوث", "fouling"),
    ("الطاقة الشمسية", "solar"),
    ("استهلاك الطاقة", "energy consumption"),
    ("الصيانة", "maintenance"),
    ("التشغيل", "operation"),
    ("التصميم", "design"),
    ("المحطات", "plant"),
    ("محطة", "plant"),
    ("المعالجة", "treatment"),
    ("معالجة", "treatment"),
    ("المياه", "water"),
    ("مياه", "water"),
]


def glossary_query(topic: str) -> str:
    """
    استعلام إنجليزي من المعجم بلا نموذج.

    يعيد "" إن لم يطابق مصطلحين فأكثر - مصطلح واحد استعلام أعمى يجرّ
    أوراقاً بعيدة، وقد رُصد ذلك: استعلام فضفاض أعاد ورقة عن Pinch Analysis.
    """
    t = " ".join(topic.split())
    out: list[str] = []
    for ar, en in TERMS:
        if ar in t and en not in out:
            out.append(en)
            t = t.replace(ar, " ")       # يمنع «مياه» من التقاط ما استُهلك
        if len(out) >= 5:
            break
    return " ".join(out) if len(out) >= 2 else ""


def english_query(topic: str) -> str:
    """
    يحوّل الموضوع إلى استعلام أكاديمي إنجليزي.

    نداء واحد بأسرع نموذج متاح، مُخزَّن في cache: تكلفته أقل من نتيجة بحث
    واحدة، وبدونه تعود قواعد البيانات فارغة تماماً لا ضعيفة.
    """
    if _is_latin(topic):
        return topic

    import cache

    # سقف طويل عمداً: الترجمة لا تتقادم كما تتقادم نتيجة بحث، وإعادتها
    # تكلّف 13.8 ثانية مقيسة - أبطأ خطوة مفردة في التشغيلة.
    ck = f"__scholar_q__::{' '.join(topic.split())}"
    if (hit := cache.get(ck, ttl_hours=24 * 365)) and isinstance(hit, str):
        return hit

    # المعجم أولاً: يغطّي المواضيع المتكرّرة فوراً ويوفّر نداءً كاملاً
    if q := glossary_query(topic):
        cache.put(ck, q)
        return q

    try:
        import providers
        llm, _ = providers.make_llm(providers.FAST, temperature=0.0,
                                    max_tokens=60, timeout=45, max_retries=2)
        # ثلاث إلى ستّ كلمات لا ثماني: استعلام أطول يجرّ أوراقاً بعيدة.
        # «operational challenges RO membrane retrofit new plant design» أعاد
        # ورقة عن Pinch Analysis وأخرى عن إزالة الكربون من الصناعة.
        out = llm.call(
            "حوّل الموضوع التالي إلى استعلام بحث أكاديمي بالإنجليزية.\n"
            "من ثلاث إلى ستّ كلمات مفتاحية تقنية تصف **الظاهرة الأساسية** "
            "فقط - احذف كلمات الأعمال والسياق (السوق، العملاء، الفرص، "
            "التصميم الجديد) فهي تجرّ أوراقاً بعيدة عن الموضوع.\n"
            "بلا علامات ترقيم وبلا شرح. أعِد الاستعلام وحده.\n\n"
            f"الموضوع: {topic}")
        q = " ".join(str(out or "").split())
        q = re.sub(r'^["\'`]+|["\'`]+$', "", q).strip()
        # النموذج يضيف أحياناً «Query:» أو سطراً تمهيدياً
        q = q.split("\n")[0].removeprefix("Query:").removeprefix("query:").strip()
        if q and _is_latin(q) and 2 <= len(q.split()) <= 14:
            cache.put(ck, q)
            return q
    except Exception:
        pass

    # الاحتياط: المصطلحات اللاتينية المكتوبة داخل الموضوع العربي
    latin = re.findall(r"[A-Za-z][A-Za-z0-9/+-]{2,}", topic)
    return " ".join(latin[:6]) if latin else ""


def openalex(query: str, k: int = 6) -> list[dict]:
    """أعمال محكّمة مع ملخّص. نفضّل المتاح مجاناً ليقرأه الوكيل فعلاً."""
    if not query:
        return []
    url = ("https://api.openalex.org/works?search="
           + urllib.parse.quote(query)
           + f"&per-page={min(25, k * 3)}"
           + "&filter=has_abstract:true,type:article"
           + "&sort=relevance_score:desc")
    data = _get(url)
    if not data:
        return []

    out = []
    for w in (data.get("results") or [])[:k * 3]:
        title = (w.get("title") or "").strip()
        if not title:
            continue
        # oa_url يشير أحياناً إلى «الملخّص المصوّر» لا إلى الورقة:
        # ars.els-cdn.com/…/ga1_lrg.jpg مصدرٌ لا يُقرأ ولا يُستشهد به.
        oa = (w.get("open_access") or {}).get("oa_url")
        land = (w.get("primary_location") or {}).get("landing_page_url")
        if oa and ASSET.search(oa):
            oa = None
        link = oa or land or w.get("doi")
        if not link or ASSET.search(link):
            continue
        venue = (((w.get("primary_location") or {}).get("source") or {})
                 .get("display_name") or "")
        year = w.get("publication_year") or ""
        snippet = _abstract(w.get("abstract_inverted_index"))
        meta = " · ".join(str(x) for x in (venue, year) if x)
        out.append({"title": title, "url": link,
                    "snippet": f"{meta} — {snippet}" if meta else snippet})
        if len(out) >= k:
            break
    return out


def crossref(query: str, k: int = 4) -> list[dict]:
    """احتياط: تغطية Crossref أوسع للكتب والفصول، وملخّصاتها أقل توفراً."""
    if not query:
        return []
    url = ("https://api.crossref.org/works?query="
           + urllib.parse.quote(query)
           + f"&rows={k * 2}&select=title,URL,abstract,container-title,issued")
    data = _get(url)
    if not data:
        return []

    out = []
    for w in ((data.get("message") or {}).get("items") or []):
        title = (w.get("title") or [""])[0].strip()
        link = w.get("URL")
        if not title or not link:
            continue
        venue = (w.get("container-title") or [""])[0]
        abstract = re.sub(r"<[^>]+>", " ", w.get("abstract") or "")
        abstract = " ".join(abstract.split())[:320]
        out.append({"title": title, "url": link,
                    "snippet": f"{venue} — {abstract}" if venue else abstract})
        if len(out) >= k:
            break
    return out


def search(topic: str, k: int = 8) -> list[dict]:
    """
    مصادر محكّمة للموضوع، مرتّبة بالأقوى.

    تفشل بهدوء: انقطاع الشبكة أو تعذّر الترجمة يعيد قائمة فارغة، فيمضي
    البحث العام كما كان. المصدر العلمي إضافة لا شرط.
    """
    q = english_query(topic)
    if not q:
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for item in openalex(q, k) + crossref(q, max(2, k // 2)):
        key = re.sub(r"^https?://(dx\.)?doi\.org/", "", item["url"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:k]


if __name__ == "__main__":
    for st in (sys.stdout, sys.stderr):
        if hasattr(st, "reconfigure"):
            st.reconfigure(encoding="utf-8", errors="replace")

    topic = " ".join(sys.argv[1:]) or "أغشية التناضح العكسي وتحديات التلوث"
    q = english_query(topic)
    print(f"الاستعلام الإنجليزي: {q!r}\n")
    try:
        import trust
    except ImportError:
        trust = None
    for i, s in enumerate(search(topic), 1):
        w = f"{trust.weight(s['url']):.2f}" if trust else "?"
        print(f"[{i}] ({w}) {s['title'][:78]}")
        print(f"      {s['url']}")
        print(f"      {s['snippet'][:110]}…\n")
