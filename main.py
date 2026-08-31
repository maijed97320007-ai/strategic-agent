"""
وكيل الأفكار الاستراتيجية - نواة التشغيل.

سبعة وكلاء متسلسلين يحوّلون موضوعاً واحداً إلى تقرير موثّق ينتهي بحلول
ينفّذها فرد واحد.

ملاحظة معمارية: استيراد crewai مؤجَّل داخل الدوال لا في رأس الملف.
استيراده يكلّف ~10 ثوانٍ (وأضعافها داخل EXE)، وتأجيله يجعل الواجهة
تظهر فوراً بدل انتظار المستخدم أمام شاشة سوداء.
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
import threading
from datetime import date

# إيقاف إرسال بيانات التتبّع لخوادم crewai الخارجية (يجب أن يسبق أي استيراد لها)
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

# ويندوز يستخدم cp1252 افتراضياً ولا يطبع العربية - نجبره على utf-8.
# stdin مشمول: بدونه يُقرأ الموضوع العربي مشوّهاً فيخرج اسم ملف مثل
# "2026-08-27_Ø_Ø²Ø_Ù_..." بدل الاسم الصحيح.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from pathlib import Path

from dotenv import load_dotenv


def app_dir() -> Path:
    """
    مجلد البرنامج - لا مجلد التشغيل.

    الفرق يظهر في الـEXE وحده: يُشغَّل من حيث شاء المستخدم، و`load_dotenv()`
    المجرّد يبحث من مجلد التشغيل صعوداً. تشغيله من جذر القرص لا يجد .env
    الموضوع بجانبه، فيقول «OPENROUTER_API_KEY غير موجود» ولا يعمل أصلاً.
    رُصد فعلياً عند اختبار النسخة المبنيّة.
    """
    return Path(sys.executable).parent if getattr(sys, "frozen", False)         else Path(__file__).parent


load_dotenv(app_dir() / ".env")
load_dotenv()          # واحتياطاً: مجلد التشغيل، لمن يضع .env هناك

TODAY = date.today()
# فارغ = تلقائي: موجّه المزوّدين يختار حسب نوع المهمة. تحديد نموذج هنا
# يثبّت الوكلاء السبعة عليه ويُلغي التوجيه.
MODEL = (os.getenv("MODEL") or "").strip()


def _flag(name: str, default: str) -> bool:
    """
    قراءة علم منطقي من البيئة.

    القيمة الفارغة تعني «غير محدَّد» لا «مفعّل»: سطر `MAKE_PDF=` في .env
    كان يُقرأ كتفعيل لأن "" ليست ضمن قائمة النفي - فيولّد PDF لمن كتب
    السطر ليعطّله.
    """
    raw = (os.getenv(name) or "").strip()
    return (raw or default).lower() not in ("0", "false", "no")


# سقف زمني صلب للتشغيلة كاملة. بلا هذا تعلّق التشغيلة أبداً عند تعثّر
# مزوّد النموذج، لأن max_retries/timeout يغطّيان المكالمة الواحدة فقط.
RUN_TIMEOUT = int(os.getenv("RUN_TIMEOUT", "1500"))

# سقف رموز مخرَج الوكيل الواحد. المركّب يحتاج أكثر لأنه يكتب التقرير.
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "3500"))
SYNTH_MAX_TOKENS = int(os.getenv("SYNTH_MAX_TOKENS", "9000"))

# مصنّف الفرص يُنتج عنصراً منظّماً لكل خبر مطابق - مخرَج طويل بطبعه
OPP_MAX_TOKENS = int(os.getenv("OPP_MAX_TOKENS", "8000"))

# صفر افتراضاً: التقرير يُعرض في الصفحة نفسها الآن، وتوليد PDF يشغّل
# Edge بلا واجهة ويضيف ثوانيَ لكل تشغيلة لملفٍ قد لا يُفتح. زرّ «تصدير
# PDF» في بطاقة النتيجة يولّده عند الطلب.
MAKE_PDF = _flag("MAKE_PDF", "0")

# إخفاء المصادر من نص التقرير. تبقى في knowledge.db وتُسترجع بـ:
#   python memory.py sources
HIDE_SOURCES = _flag("HIDE_SOURCES", "1")

# خط الأنابيب:
#
#   SEARCH → ┌─A1─A2─A3─A4─┐ → ┌─A5─A6─A7─┐ → Evidence → Scoring
#            └─ الموجة 1 ──┘   └─ الموجة 2┘        ↓
#                                                RED TEAM → Synthesis
#                                                    ↓
#                                        Citation Guardrail → Final Report
STAGES = [
    ("البحث وترقيم المصادر", "S1..Sn بعنوان ورابط ومقتطف"),
    ("تحميل المهارات", "خبرة المجال المطابقة"),
    ("الموجة الأولى", "A1-A4 متوازية من المصادر"),
    ("جمع الأدلة", "تحليل JSON وفرز الإسناد"),
    ("الموجة الثانية", "A5-A7 متوازية"),
    ("التسجيل", "مكافأة المُسنَد وعقاب الملفَّق"),
    ("الفريق الأحمر", "لماذا قد يفشل هذا؟"),
    ("إعادة التسجيل", "بعد نقاط الفشل"),
    ("التركيب", "دمج المرتّب في تقرير"),
    ("تحقّق الإسناد", "رفض المعرّفات المخترعة"),
]

# نوع المهمة لكل وكيل - يحدّد المزوّد المفضّل (انظر providers.ROUTES).
# A1 استخراج قصير فيذهب للأسرع؛ الباقي تحليل؛ التركيب طويل السياق.
KIND_OF = {"A1": "fast", "A2": "analytic", "A3": "analytic", "A4": "analytic",
           "A5": "analytic", "A6": "analytic", "A7": "analytic",
           "RED": "analytic", "SYN": "broad"}

# توزيع الوكلاء على المزوّدين: لكل وكيل إزاحة ثابتة في سلسلة نوعه، فلا
# يتكدّس السبعة على مزوّد واحد ويصطفّوا في طابور حصّته المجانية.
#
# ثابتة لا عشوائية: التشغيلة تبقى قابلة للتكرار، وإعادة البناء بعد فشل
# تنتقل للتالي بانتظام بدل القفز عشوائياً.
#
# الموجة الأولى (A1-A4) تأخذ إزاحات مختلفة كلها لأنها تعمل معاً، وكذلك
# الثانية (A5-A7). والمركّب والفريق الأحمر منفردان فتكفيهما الصفر.
#
# **متى يضرّ التوزيع؟** الموجة تنتهي بانتهاء أبطأ عضو فيها، فتوزيعها على
# مزوّدين متفاوتَي السرعة يجرّها إلى زمن الأبطأ. قياس فعلي: Gemini 10.7
# ثانية للنداء مقابل 19.3 لـOpenRouter - أي أن وكيلاً على الثاني يضيف
# نحو تسع ثوانٍ للموجة كلها. التوزيع يكسب حين يتقارب المزوّدون أو حين
# يخنق أحدهم التزامن، ويخسر حين يتباعدون.
#
# SPREAD_AGENTS=0 يوقفه فيعمل الجميع على أسرع مزوّد في السلسلة.
SPREAD_ON = (os.getenv("SPREAD_AGENTS", "1").strip().lower()
             not in ("0", "false", "no"))

AGENT_SPREAD = ({"A1": 0, "A2": 1, "A3": 2, "A4": 3,
                 "A5": 1, "A6": 2, "A7": 3, "RED": 0, "SYN": 0}
                if SPREAD_ON else {})

_KEY_ENV = {"openrouter/": "OPENROUTER_API_KEY", "gpt-": "OPENAI_API_KEY",
            "openai/": "OPENAI_API_KEY", "anthropic/": "ANTHROPIC_API_KEY"}


class RunTimeout(Exception):
    """تجاوزت التشغيلة سقفها الزمني - ما أُنجز يُحفظ كتقرير جزئي."""


def required_key() -> str:
    """اسم متغيّر البيئة الذي يحتاجه النموذج المختار."""
    return next((v for k, v in _KEY_ENV.items() if MODEL.startswith(k)), "OPENAI_API_KEY")


def is_local() -> bool:
    """هل النموذج يعمل محلياً عبر Ollama؟"""
    return MODEL.startswith(("ollama/", "ollama_chat/"))


def check_key() -> str | None:
    """
    يعيد رسالة خطأ إن تعذّر التشغيل، أو None إن كان جاهزاً.

    بلا MODEL محدَّد نسأل موجّه المزوّدين: هل من مزوّد جاهز أصلاً؟ الفحص
    القديم كان مكتوباً لعالم النموذج الواحد فيبحث عن مفتاح MODEL، وحين
    فُرّغ ليعمل التوجيه سقط على OPENAI_API_KEY ومنع التشغيل كلّه رغم
    وجود مفتاح Gemini صالح.
    """
    if not MODEL:
        try:
            import providers
            if ready := providers.available():
                return None if any(p.name != "ollama" for p in ready)                     or providers.ollama_up() else                     "لا مزوّد جاهز: Ollama هو الوحيد المتاح وخادمه لا يستجيب."
            return ("لا مفتاح لأي مزوّد في .env — أضف GEMINI_API_KEY أو "
                    "GROQ_API_KEY أو OPENROUTER_API_KEY. "
                    "لعرض الحالة: python providers.py")
        except ImportError:
            return "تعذّر تحميل موجّه المزوّدين."

    if is_local():
        # النماذج المحلية لا تحتاج مفتاحاً، لكنها تحتاج خادم Ollama حيّاً
        import urllib.error
        import urllib.request
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
        except (urllib.error.URLError, OSError):
            return ("خادم Ollama لا يستجيب على 11434. شغّله بـ: ollama serve  "
                    f"(النموذج المختار: {MODEL})")
        return None

    need = required_key()
    if not os.getenv(need):
        return f"{need} غير موجود في ملف .env (النموذج المختار: {MODEL})"
    return None


# ======================
# البحث الحقيقي (يُنفَّذ في الكود، لا يُترك لمزاج النموذج)
# ======================
def _search_tool():
    if not os.getenv("SERPER_API_KEY"):
        return None
    from crewai_tools import SerperDevTool
    return SerperDevTool()


def _browser_tool():
    """
    أداة تصفح حقيقي - معطّلة افتراضياً.

    زيارة صفحة تكلّف 20-60 ثانية مقابل ثانية لبحث Serper، فتشغيلها في كل
    تشغيلة ينسف مكسب السرعة. تُفعَّل بـ BROWSER_AGENT=1 حين تحتاج رقماً
    داخل جدول أو تقرير لا يظهر في مقتطف البحث.
    """
    try:
        import browser_agent
        return browser_agent.tool()
    except Exception:
        return None


# مصطلحات فرنسية للسوق المغاربي. صغير عمداً: يكفي لتوجيه استعلام واحد،
# والترجمة الكاملة تحتاج نداء نموذج لا يستحقّه عائدُ لغةٍ ثالثة.
_FR = {
    "desalination": "dessalement", "water": "eau", "treatment": "traitement",
    "membrane": "membrane", "wastewater": "eaux usées", "reuse": "réutilisation",
    "reverse": "osmose", "osmosis": "inverse", "plant": "station",
    "drinking": "potable", "groundwater": "nappe", "brine": "saumure",
    "energy": "énergie", "industrial": "industriel", "irrigation": "irrigation",
}
_FR_ON = os.getenv("SEARCH_FRENCH", "1").strip().lower() not in ("0", "false", "no")


def prefetch_research(topic: str, max_queries: int = 4):
    """
    ينفّذ بحثاً فعلياً ويعيد سجل مصادر مرقّمة (S1, S2, …).

    الترقيم يجعل الإسناد قابلاً للتحقق آلياً: نتأكد أن كل معرّف يذكره وكيل
    موجود فعلاً في السجل. عدّ الروابط الخام لا يكشف رابطاً مخترعاً.
    """
    import sources

    reg = sources.Registry()
    tool = _search_tool()
    if tool is None:
        return reg

    # ── استعلامات متعددة اللغات ──
    #
    # حصر البحث بالعربية يقصّ أكثر ممّا يجمع: أدبيات معالجة المياه
    # إنجليزية في أغلبها، وسوق شمال أفريقيا فرنسي التوثيق. والمقتطف بأي
    # لغة يقرأه النموذج ويكتب منه بالعربية، فاللغة قيدُ بحثٍ لا قيدُ فهم.
    #
    # الاستعلام الإنجليزي محسوب أصلاً للقواعد العلمية (scholar) - نعيد
    # استعماله هنا بلا نداء إضافي.
    try:
        import scholar
        en = scholar.english_query(topic)
    except Exception:
        en = ""

    # استعلامات موجَّهة نحو المصادر القوية.
    #
    # السبب: البحث العام يعيد ما يتصدّر جوجل - مواقع مورّدين ومنصات تواصل.
    # قياس فعلي على تشغيلة كاملة أعطى متوسط وزن 0.34 وصفر مصدر قوي من 15.
    scholarly = [
        f"{en or topic} site:sciencedirect.com OR site:mdpi.com OR site:springer.com",
        f"{en or topic} filetype:pdf research study",
        f"{topic} site:.gov OR site:.edu OR site:iso.org",
    ]

    arabic = [topic, f"{topic} تحديات", f"{topic} {TODAY.year}"]

    english = [f"{en} {TODAY.year}", f"{en} challenges case study",
               f"{en} cost energy consumption"] if en else               [f"{topic} latest developments"]

    # الفرنسية للسوق المغاربي: تونس والمغرب والجزائر توثّق مشاريعها بها،
    # ولا تظهر في البحث العربي ولا الإنجليزي.
    french = [f"{_FR.get(w, w)} {TODAY.year}" for w in (en.split()[:3] or [])]
    french = [" ".join(french)] if en and _FR_ON else []

    # الترتيب مقصود: القوي أولاً، فلو نفدت الحصة بقي الأجود
    queries = scholarly + english + arabic + french

    import cache

    # ── كل النداءات معاً ──
    #
    # كان هذا تسلسلياً فكلّف 25.5 ثانية مقيسة: ترجمة الاستعلام 13.8 ث،
    # ثم القواعد العلمية 3.6 ث، ثم سبعة استعلامات بحث × 2.1 ث. وكلها
    # **انتظار شبكة لا حساب**، فتسلسلها إهدار خالص - المجموع يساوي أبطأ
    # نداء وحده حين تتوازى.
    #
    # الترتيب في السجل يبقى ثابتاً رغم التوازي: نجمع النتائج ثم نضيفها
    # بترتيب مقصود. العلمية أولاً لأن الوكلاء يستشهدون بأوائل السجل أكثر.
    from concurrent.futures import ThreadPoolExecutor

    def _scholar():
        try:
            import scholar
            return scholar.search(topic, k=8)
        except Exception:
            return []              # المصدر العلمي إضافة لا شرط

    with ThreadPoolExecutor(max_workers=len(queries) + 1) as pool:
        fut_sch = pool.submit(_scholar)
        futs = [(q, pool.submit(cache.cached_search, tool, q)) for q in queries]
        results = [(q, f.result()) for q, f in futs]
        scholarly_hits = fut_sch.result()

    for it in scholarly_hits:
        reg.add(it["title"], it["url"], it["snippet"], "قاعدة علمية")

    # ── البحث العام ──
    # سقف نطاقين لكل موقع: تسع صفحات من مورّد واحد كانت تُغرق السجل وتُوهم
    # «تعدّد الأدلة» في الترجيح، وهي في الحقيقة مصدر واحد يكرّر نفسه.
    from urllib.parse import urlparse

    per_host: dict[str, int] = {}
    for q, raw in results:
        if raw is None:
            continue
        organic = (raw or {}).get("organic", []) if isinstance(raw, dict) else []
        for it in organic[:6]:
            link = it.get("link")
            if not link:
                continue
            try:
                host = (urlparse(link).netloc or "").lower().removeprefix("www.")
            except ValueError:
                host = ""
            if per_host.get(host, 0) >= 2:
                continue
            per_host[host] = per_host.get(host, 0) + 1
            reg.add(it.get("title", ""), link, it.get("snippet", ""), q)

    return _prune_weak(reg)


def _prune_weak(reg, floor: int = 12, cap: int = 30):
    """
    يسقط أضعف المصادر ويضع سقفاً للعدد.

    شيئان مختلفان يعالجهما:

    · **الضعيف**: منصات التواصل والمنتديات كانت تدخل السجل بوزن 0.20
      وتُستشهد بها كأي مصدر. حذفها حين يتوفّر البديل يرفع المتوسط بلا أن
      يترك وكيلاً بلا سند - ولذلك نُبقيها إن قلّ السجل عن `floor`.

    · **الكثير**: البحث بثلاث لغات رفع الحصيلة من ثلاثين مصدراً إلى
      أربعة وخمسين، والسجل كاملاً يُحقن في مُوجَّه **كل** وكيل. الزيادة
      تُثقل السياق وتُميّع الانتباه أكثر ممّا تُثري. نُبقي الأقوى وزناً
      مع حفظ الترتيب - العلمية تبقى في المقدّمة لأن الوكلاء يستشهدون
      بأوائل السجل أكثر.
    """
    try:
        import trust
    except ImportError:
        return reg

    keep = [s for s in reg.items if trust.weight(s.url) > 0.25]
    if len(keep) < floor:
        keep = list(reg.items)

    if len(keep) > cap:
        # حصّة محجوزة للمصدر الميداني.
        #
        # الاختيار بالوزن وحده جعل السجل ثلاثين ورقة محكّمة بمتوسط 0.88
        # وصفر مصدر تجاري - وهذا إفراط لا إتقان: الورقة تجيب «ما الذي
        # يحدث فيزيائياً» ولا تجيب «كم سعره في عُمان اليوم ومن يورّده».
        # التقرير يحتاج الاثنين، والفرق أن الترجيح يعرف أيّهما أثقل.
        order = {id(s): i for i, s in enumerate(keep)}
        strong = [s for s in keep if trust.weight(s.url) >= 0.75]
        field = [s for s in keep if trust.weight(s.url) < 0.75]

        quota = min(len(field), max(6, cap // 4))
        picked = (sorted(strong, key=lambda s: -trust.weight(s.url))[:cap - quota]
                  + sorted(field, key=lambda s: -trust.weight(s.url))[:quota])
        keep = sorted(picked, key=lambda s: order[id(s)])

    if len(keep) == len(reg.items):
        return reg

    import sources

    out = sources.Registry()
    for s in keep:
        out.add(s.title, s.url, s.snippet, s.query)
    return out


# ======================
# بناء الوكلاء
# ======================
def build_agents() -> dict:
    """
    ينشئ الوكلاء السبعة + المركّب من ROSTER - نسخة جديدة لكل تشغيلة.

    نموذج مستقل لكل وكيل: الموجات متوازية، ومشاركة كائن LLM بين خيوط
    متزامنة هي بالضبط صنف العطل الذي كلّفنا سابقاً
    (RuntimeError: Executor is already running).
    """
    from crewai import Agent, LLM

    import pipeline

    # النموذج المحلي يولّد 2-6 رموز/ثانية على معالج بلا تسريع رسومي،
    # فمهلة 180 ثانية تقطعه في منتصف كل مهمة.
    opts = dict(max_retries=2, timeout=1200) if is_local() \
        else dict(max_retries=6, timeout=180)

    # سقف المخرَج. بدونه ينفلت وكيل ويولّد عشرات آلاف الرموز حتى ينقطع،
    # فيخرج JSON مبتوراً ويضيع عمله كاملاً - رُصد فعلياً حين أنتج A7
    # 132 ألف حرف لم يُحلَّل منها شيء. العناصر المطلوبة قصيرة ومنظّمة،
    # فسقف 3500 رمز سخيّ عليها ويقطع الانفلات.
    opts["max_tokens"] = AGENT_MAX_TOKENS

    import providers

    tools = [t for t in (_search_tool(), _browser_tool()) if t]

    # MODEL المضبوط يدوياً يعلو على التوجيه التلقائي
    forced = MODEL if os.getenv("MODEL") else None

    SPREAD = AGENT_SPREAD

    def make(role, goal, backstory, temp, kind, with_tools=False, attempt=0,
             spread=0):
        try:
            llm, _p = providers.make_llm(kind, attempt, temperature=temp,
                                         forced=forced, spread=spread, **opts)
        except IndexError:
            raise
        except Exception:
            llm = LLM(model=MODEL, temperature=temp, **opts)
        return Agent(role=role, goal=goal, backstory=backstory, llm=llm,
                     tools=list(tools) if with_tools else [],
                     verbose=False, allow_delegation=False, inject_date=True)

    agents = {code: make(role, goal, back, temp, KIND_OF[code],
                         with_tools=(code == "A1"), spread=SPREAD.get(code, 0))
              for code, role, goal, back, temp, _ in pipeline.ROSTER}

    agents["RED"] = make(
        "الفريق الأحمر",
        "تدمير أقوى الأفكار قبل أن يدمّرها السوق",
        "مهاجم محترف. لا يقترح تحسينات ولا يجامل - يبحث عن الافتراض "
        "الضمني الذي ينهار، والرقم الذي لا يصمد أمام الحساب، والسبب الذي "
        "أفشل مشاريع مشابهة. يعرف أن الفكرة التي تنجو منه تستحق التنفيذ.",
        0.35, providers.ANALYTIC)

    agents["SYN"] = make(
        "المركّب النهائي",
        "دمج مخرجات الوكلاء السبعة في تقرير واحد مرتّب بالأولوية",
        "قائد استراتيجي يجمع الإبداع بالواقعية. لا ينقل رقماً بلا معرّف "
        "مصدره، ويكتب (تقدير) صراحة بجانب ما لا سند له.", 0.45, providers.BROAD)
    agents["SYN"].llm.max_tokens = SYNTH_MAX_TOKENS

    # مصنّف الفرص: كان يستعير A1 المخصّص للاستخراج القصير - نموذج خفيف
    # وسقف 3500 رمز. لكن تصنيف تسعين خبراً بمخرَج JSON منظّم لكل واحد
    # مهمة طويلة لا قصيرة، فكان المخرَج يُبتر عند العنصر الأول أو الثاني
    # ويعود بفرصة واحدة من مئتين وثلاثة وأربعين خبراً.
    agents["OPP"] = make(
        "محلّل الفرص",
        "فرز الأخبار وتحديد ما يفتح باب عمل لهذا الملف تحديداً",
        "يقرأ عشرات الأخبار ويميّز المناقصة الحقيقية من الخبر العام. "
        "لا يجامل: ما لا يفتح باب عمل لا يُدرجه.",
        0.3, providers.BROAD)
    agents["OPP"].llm.max_tokens = OPP_MAX_TOKENS

    # مصنع لإعادة البناء بمزوّد بديل عند فشل الأول
    def rebuild(code: str):
        spec = next((r for r in pipeline.ROSTER if r[0] == code), None)
        if code in ("SYN", "RED", "OPP"):
            kind = providers.ANALYTIC if code == "RED" else providers.BROAD
            temp = {"SYN": 0.45, "RED": 0.35, "OPP": 0.3}[code]
            return lambda n: make(agents[code].role, agents[code].goal,
                                  agents[code].backstory, temp, kind,
                                  attempt=n, spread=SPREAD.get(code, 0))
        if not spec:
            return None
        _, role, goal, back, temp, _ = spec
        return lambda n: make(role, goal, back, temp, KIND_OF[code],
                              with_tools=(code == "A1"), attempt=n,
                              spread=SPREAD.get(code, 0))

    agents["_rebuild"] = rebuild
    return agents


# ======================
# التشغيل
# ======================
def _patch_guardrail_source() -> None:
    """
    يمنع انهيار الـEXE عند أول guardrail.

    crewai يستدعي inspect.getsource() على أي guardrail قابل للاستدعاء ليسجّل
    نصّه في حدث - بلا try/except. داخل EXE مبني بـPyInstaller لا توجد ملفات
    .py على القرص، فيرمي OSError وتسقط التشغيلة كاملة.

    نلفّ getsource داخل تلك الوحدة وحدها. (جُرّب أولاً تسجيل مصدر بديل في
    linecache فلم ينجح.) يُطبَّق دائماً - بلا أثر حين يعمل getsource طبيعياً.
    """
    try:
        from crewai.events.types import llm_guardrail_events as ev
    except ImportError:
        return
    if getattr(ev.getsource, "__name__", "") == "safe_getsource":
        return

    original = ev.getsource

    def safe_getsource(obj):
        try:
            return original(obj)
        except (OSError, TypeError):
            return f"<{getattr(obj, '__name__', 'guardrail')}: المصدر غير متاح>"

    ev.getsource = safe_getsource


def _silence_crewai() -> None:
    """
    يُسكِت إخراج crewai المتدفق.

    ضروري لأن ConsoleFormatter يُنشأ بـ verbose=True مثبّتاً في المصدر
    (crewai/events/event_listener.py) ولا يحترم Crew(verbose=False).
    """
    import logging

    try:
        from crewai.events.event_listener import event_listener
        event_listener.formatter.verbose = False
        event_listener.logger.verbose = False
    except Exception:
        pass                      # نسخة crewai مختلفة

    for name in ("crewai", "LiteLLM", "httpx", "opentelemetry", "chromadb"):
        logging.getLogger(name).setLevel(logging.ERROR)


def run_creative_agent(topic: str, on_stage=None):
    """
    ينفّذ خط الأنابيب: بحث ← موجتان ← أدلة ← تسجيل ← تركيب ← تحقق إسناد.

    يعيد (تقرير، نتيجة، توقّف؟). عند انتهاء المهلة أو فشل مرحلة نعيد ما
    أُنجز مع سببه بدل محو العمل.
    """
    import pipeline

    def stage(i, note=""):
        if on_stage:
            on_stage(i, note)

    _patch_guardrail_source()
    _silence_crewai()

    stage(0)
    reg = prefetch_research(topic)
    stage(1, f"{len(reg.items)} مصدر مرقّم" if reg.items
             else "بلا مفتاح SERPER - لا مصادر")

    # مهارات المجال المطابقة للموضوع (Agent Skills)
    skill_ctx = ""
    try:
        import skills
        if picked := skills.select(topic):
            skill_ctx = skills.as_context(picked)
            stage(1, f"{len(reg.items)} مصدر · {len(picked)} مهارة مُحمَّلة")
    except Exception:
        pass                        # غياب المهارات لا يوقف التشغيل

    agents = build_agents()
    box: dict = {}

    def work():
        try:
            box["res"] = pipeline.run(topic, reg, agents, skills=skill_ctx,
                                      on_stage=stage, today=TODAY.isoformat(),
                                      year=TODAY.year, model=MODEL)
        except Exception as e:
            box["err"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=work, daemon=True, name="pipeline")
    t.start()
    t.join(RUN_TIMEOUT)

    if t.is_alive():
        _silence_crewai()
        head = f"> ⚠ **انتهت المهلة** ({RUN_TIMEOUT // 60} دقيقة).\n\n"
        return head + reg.as_markdown(), None, True

    if err := box.get("err"):
        head = (f"> ⛔ **توقفت التشغيلة:** {err}\n>\n"
                "> إن كنت على نموذج محلي صغير، فالأرجح أنه عجز عن إنتاج JSON "
                "صالح أو الالتزام بمعرّفات المصادر.\n\n")
        return head + reg.as_markdown(), None, True

    res = box["res"]
    res["registry"] = reg
    return res["report"], res, False


# ======================
# المخرجات
# ======================
def strip_sources(md: str) -> str:
    """
    يزيل الروابط من نص التقرير ويحذف قسم المصادر.

    الوكلاء يواصلون إنتاج المصادر - الـguardrail يفرضها - لأنها هي التي
    تمنع اختراع الأرقام. نحذفها من العرض فقط بعد تخزينها في knowledge.db.

    الرسومات محميّة: تُستبدل بعلامات قبل الحذف وتُعاد بعده. بدون هذا كان
    `xmlns="http://www.w3.org/2000/svg"` يُعدّ رابطاً فيُحذف، فتنكسر كل
    مخططات التقرير صامتةً.
    """
    keep: list[str] = []

    def _shield(m):
        keep.append(m.group(0))
        return f"\x00SVG{len(keep) - 1}\x00"

    md = re.sub(r"<svg\b.*?</svg>", _shield, md, flags=re.S | re.I)

    # [#\s]* لأن النموذج يكتب أحياناً "## ## المصادر" بعلامات مكرّرة
    md = re.sub(r"\n#{1,4}[#\s]*(?:[📚🔗]\s*)?(?:المصادر|المراجع|قائمة المصادر)\b"
                r".*?(?=\n#{1,4}[ #]|\Z)", "\n", md, flags=re.S)

    # رموز الإسناد. النموذج يكتبها مفردة [S1] ومجمّعة [S3, S11, S12]
    # وبفاصلة عربية - والنمط القديم كان يلتقط المفردة وحدها فبقي 47 رمزاً
    # في آخر تقرير. تبقى محفوظة في knowledge.db: python memory.py sources
    sep = r"(?:\s*(?:[-–—/]|إلى|[,،]|و|vs\.?|مقابل)\s*)"
    grp = rf"S\d+(?:{sep}S\d+)*"
    md = re.sub(rf"\s*(?:\[\s*{grp}\s*\]\s*)+", " ", md)
    md = re.sub(rf"\(\s*{grp}\s*\)", "", md)

    # الوكيل يتحدّث أحياناً *عن* المصادر داخل الجملة: «المصادر S1, S2 تركز
    # على التصميم، بينما S11-S14 تتناول ما بعد التشغيل». حذف الرمز وحده
    # يترك «المصادر تركز» مبتورة، فنستبدل المدى بعبارة تحمل نفس المعنى.
    def _prose(m):
        n = len(re.findall(r"S\d+", m.group("ids")))
        if m.group("lead"):
            return m.group("lead")            # «المصادر S1, S2» ← «المصادر»
        return "أحد المصادر" if n == 1 else "بعض المصادر"

    md = re.sub(rf"(?P<lead>(?:ال)?(?:مصادر|مراجع)\s+)?"
                rf"(?P<ids>(?<![A-Za-z0-9]){grp})", _prose, md)

    # «[تقدير بناءً على الفجوة بين S11 وS12]» - رمز داخل قوس نصّي لا يطابق
    # ما سبق، فكان يبقى وحيداً بلا قائمة مصادر تفسّره. ننظّف داخل القوس
    # ونحذفه كلّه إن لم يبقَ فيه إلا الرموز.
    def _debracket(m):
        # بلا \b: الواو العربية حرف كلمة، فلا حدّ بينها وبين S في «وS12»
        inner = re.sub(r"\s*و?(?<![A-Za-z0-9])S\d+", "", m.group(1))
        inner = re.sub(r"\s*(?:بين|من|في|على|حسب|وفق|وفقاً|بناءً على|و)\s*$",
                       "", inner.strip())
        inner = inner.strip(" \u060c,-\u2013\u2014")
        return f"[{inner}]" if inner else ""

    md = re.sub(r"\[([^\[\]]*S\d+[^\[\]]*)\]", _debracket, md)

    md = re.sub(r"\[([^\]]+)\]\(\s*https?://[^)]+\)", r"\1", md)   # [نص](رابط) ← نص
    md = re.sub(r"\s*\((?:[^()]*?:\s*)?https?://[^)]*\)", "", md)  # (رابط) ← يُحذف
    md = re.sub(r"https?://\S+", "", md)                           # رابط عارٍ
    md = re.sub(r"^\s*[-*]?\s*(?:المصدر|المرجع|Source)\s*:?\s*$", "", md, flags=re.M)

    # ([مصدر](رابط)) يخلّف "(مصدر)" فارغة بعد استخراج نص الرابط
    md = re.sub(r"\s*[\(\[]\s*(?:مصدر|المصدر|مرجع|المرجع|[Ss]ource|[Rr]ef)\s*[\)\]]", "", md)
    md = re.sub(r"\(\s*[،,؛;\-–—]*\s*\)", "", md)

    # حذف الرمز يخلّف فراغاً قبل الفاصلة أو النقطة: «ضخمة" ، بينما»
    md = re.sub(r"[ \t]+([،,؛;.!؟])", r"\1", md)
    md = re.sub(r"[ \t]{2,}", " ", md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"

    return re.sub(r"\x00SVG(\d+)\x00", lambda m: keep[int(m.group(1))], md)


def out_dir_default() -> str:
    """
    مجلد التقارير بجانب البرنامج - وإلا تناثرت في مجلد التشغيل.

    OUTPUT_DIR يوحّده بين النسخة العادية والـEXE كما يفعل KNOWLEDGE_DB
    بقاعدة المعرفة: بدونه لكلٍّ مجلدها، فتعرض اللوحة قائمة تقارير مختلفة
    حسب أيّ نسخة فتحتها. النسبي يُحلّ من مجلد البرنامج لا مجلد التشغيل.
    """
    if env := (os.getenv("OUTPUT_DIR") or "").strip():
        q = Path(env)
        return str(q if q.is_absolute() else (app_dir() / q).resolve())
    return str(app_dir() / "output")


def save_report(report: str, topic: str, out_dir: str | None = None) -> str:
    out_dir = out_dir or out_dir_default()
    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r"[^\w؀-ۿ]+", "_", topic)[:40].strip("_")
    path = os.path.join(out_dir, f"{TODAY.isoformat()}_{slug}.md")
    header = f"# {topic}\n\n*أُنشئ في {TODAY.isoformat()} · النموذج: {MODEL}*\n\n---\n\n"
    for p in (path, os.path.join(out_dir, "final_report.md")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(header + report)
    return path


def finish_run(report: str, topic: str) -> tuple[str, dict, list[str]]:
    """
    يخزّن الحقائق ثم يحفظ التقرير (نظيفاً) ويولّد PDF.

    الترتيب مقصود: نبتلع النسخة الموثّقة أولاً لتُحفظ المصادر في
    knowledge.db، ثم نحفظ النسخة النظيفة للعرض.
    """
    warnings: list[str] = []

    stats: dict = {}
    try:
        import memory
        stats = memory.ingest_report(report, topic)
    except Exception as e:
        warnings.append(f"تعذّر تخزين الحقائق: {type(e).__name__}: {e}")

    body = strip_sources(report) if HIDE_SOURCES else report

    # تقييم مستقل عبر Mastra (معطّل افتراضياً). يُحكَّم على النسخة الموثّقة
    # لا المجرّدة - وإلا عاقبنا التقرير على مصادر نحن حذفناها.
    try:
        import judge
        if verdict := judge.evaluate(report, topic):
            body += judge.as_markdown(verdict)
            stats["judge_total"] = verdict.get("total")
    except Exception as e:
        warnings.append(f"تعذّر التقييم المستقل: {type(e).__name__}: {e}")

    path = save_report(body, topic)
    md_path = path

    if MAKE_PDF:
        try:
            import pdf
            path = str(pdf.markdown_to_pdf(path))
        except Exception as e:
            warnings.append(f"تعذّر توليد PDF: {e}")

    stats["md_path"] = md_path        # الواجهة تعرض Markdown لا PDF
    return path, stats, warnings


# ======================
# واجهة الطرفية (احتياطية - الافتراضي هو واجهة الويب في app.py)
# ======================
def main() -> int:
    import time

    import ui

    if err := check_key():
        ui.show_error(RuntimeError(err))
        return 1

    topic = ui.ask_topic()
    if not topic:
        ui.warn("لازم تدخل موضوع.")
        return 1

    started = time.perf_counter()
    stages = [ui.Stage(label=a, hint=b) for a, b in STAGES]

    with ui.Dashboard(model=MODEL, topic=topic, stages=stages) as dash:
        report, _tasks, timed_out = run_creative_agent(
            topic, on_stage=lambda i, note="": dash.start(i, note))

    if timed_out:
        ui.warn(f"انتهت المهلة ({RUN_TIMEOUT // 60} دقيقة) — حُفظ ما أُنجز كتقرير جزئي.")

    path, stats, warns = finish_run(report, topic)
    for w in warns:
        ui.warn(w)

    ui.show_result(path, report, stats, time.perf_counter() - started)

    if timed_out:
        # الخيط المهجور لا يزال حيّاً على مقبس شبكة. الخروج الطبيعي يوقظ
        # آلية إغلاق crewai فتطبع أخطاءً مقلقة فوق نتيجة المستخدم.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
        sys.exit(130)
    except Exception as e:
        try:
            import ui
            ui.show_error(e)
        except Exception:
            print(f"\nفشل التشغيل: {type(e).__name__}: {e}")
        input("\nاضغط Enter للإغلاق...")   # يمنع اختفاء نافذة الـEXE فوراً
        sys.exit(1)
