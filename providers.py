"""
موجّه المزوّدين - يختار النموذج حسب نوع المهمة ويتجاوز الفشل تلقائياً.

    مهمة قصيرة    → Groq / Cerebras      (أسرع استدلال متاح مجاناً)
    مهمة تحليلية  → Mistral / Z.ai       (استدلال أعمق)
    عدة نماذج     → OpenRouter           (أوسع تشكيلة تحت مفتاح واحد)
    بلا إنترنت    → Ollama               (محلي بالكامل)
    فشل المزوّد   → تجاوز تلقائي للتالي

لماذا موجّه أصلي لا FreeLLMAPI؟ الأخير خدمة وسيطة بـDocker/Node تفعل الشيء
نفسه، لكنها تضيف نظام تشغيل ثانياً وحاوية ومفتاح تشفير. المنطق هنا ~150
سطراً بلا تبعيات. ومن أراد الوسيط فليضبط `LLM_BASE_URL` عليه وسيُعامَل
كمزوّد متوافق مع OpenAI.

كل المزوّدين هنا متوافقون مع واجهة OpenAI، فالفرق بينهم عنوانٌ ومفتاح.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:                              # ليعمل مستقلاً كأداة تشخيص أيضاً
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# أنواع المهام
FAST = "fast"            # استخراج، تصنيف، مخرَج قصير
ANALYTIC = "analytic"    # نقد، تحليل، استدلال
BROAD = "broad"          # تركيب طويل السياق
LOCAL = "local"          # بلا إنترنت


@dataclass
class Provider:
    name: str
    base_url: str
    key_env: str
    models: dict[str, str]           # نوع المهمة → معرّف النموذج
    kinds: tuple[str, ...] = ()      # الأنواع التي يجيدها
    needs_key: bool = True
    note: str = ""

    @property
    def ready(self) -> bool:
        return bool(os.getenv(self.key_env)) if self.needs_key else True

    def model_for(self, kind: str) -> str | None:
        return self.models.get(kind) or self.models.get(ANALYTIC) or \
            next(iter(self.models.values()), None)

    def llm_id(self, kind: str) -> str | None:
        """المعرّف بصيغة crewai: <مزوّد>/<نموذج>"""
        m = self.model_for(kind)
        return f"{self.name}/{m}" if m else None


# النماذج مختارة من الطبقات المجانية المعلنة لكل مزوّد.
# قد تتغيّر - إن فشل نموذج، فالتجاوز التلقائي ينقلنا للتالي.
PROVIDERS: list[Provider] = [
    Provider(
        "groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
        {FAST: "llama-3.3-70b-versatile", ANALYTIC: "llama-3.3-70b-versatile"},
        kinds=(FAST,), note="أسرع استدلال - مناسب للمهام القصيرة"),
    Provider(
        "cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
        {FAST: "llama-3.3-70b", ANALYTIC: "qwen-3-235b-a22b-instruct"},
        kinds=(FAST,), note="استدلال فائق السرعة"),
    Provider(
        "mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
        {ANALYTIC: "mistral-large-latest", FAST: "mistral-small-latest"},
        kinds=(ANALYTIC,), note="تحليل واستدلال"),
    Provider(
        "zai", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY",
        {ANALYTIC: "glm-4.6", FAST: "glm-4-flash"},
        kinds=(ANALYTIC,), note="GLM - تحليل عميق"),
    Provider(
        "openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        {BROAD: "minimax/minimax-m3:free",
         ANALYTIC: "minimax/minimax-m3:free",
         FAST: "minimax/minimax-m3:free"},
        kinds=(BROAD, ANALYTIC, FAST),
        note="أوسع تشكيلة تحت مفتاح واحد"),
    Provider(
        "ollama", "http://localhost:11434/v1", "OLLAMA_API_KEY",
        {FAST: "qwen2.5:3b", ANALYTIC: "qwen2.5:latest", BROAD: "qwen3:8b"},
        kinds=(LOCAL,), needs_key=False, note="محلي - بلا إنترنت ولا تكلفة"),
]

# ترتيب التفضيل لكل نوع مهمة. الأول هو الأنسب، والبقية احتياط.
ROUTES: dict[str, tuple[str, ...]] = {
    FAST:     ("groq", "cerebras", "openrouter", "ollama"),
    ANALYTIC: ("mistral", "zai", "openrouter", "cerebras", "ollama"),
    BROAD:    ("openrouter", "zai", "mistral", "ollama"),
    LOCAL:    ("ollama",),
}


def by_name(name: str) -> Provider | None:
    return next((p for p in PROVIDERS if p.name == name), None)


def ollama_up(timeout: float = 1.5) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def available() -> list[Provider]:
    """المزوّدون الجاهزون فعلاً (مفتاح موجود، أو Ollama يعمل)."""
    out = []
    for p in PROVIDERS:
        if p.name == "ollama":
            if ollama_up():
                out.append(p)
        elif p.ready:
            out.append(p)
    return out


def chain(kind: str, forced: str | None = None) -> list[tuple[Provider, str]]:
    """
    سلسلة المرشّحين لنوع مهمة: [(مزوّد، معرّف النموذج), …]

    الأول هو الخيار، والبقية ترتيب التجاوز عند الفشل.
    """
    if forced:
        # المستخدم ثبّت نموذجاً بعينه - نحترمه ونضع الباقي احتياطاً
        head = [(by_name(forced.split("/")[0]) or PROVIDERS[-2], forced)]
        rest = [(p, p.llm_id(kind)) for p in available()
                if p.llm_id(kind) and not forced.startswith(p.name + "/")]
        return head + rest

    ready = {p.name: p for p in available()}
    out = []
    for name in ROUTES.get(kind, ROUTES[ANALYTIC]):
        p = ready.get(name)
        if p and (mid := p.llm_id(kind)):
            out.append((p, mid))
    if not out:                       # لا شيء جاهز - نعيد كل شيء ليظهر الخطأ الحقيقي
        out = [(p, p.llm_id(kind)) for p in PROVIDERS if p.llm_id(kind)]
    return out


def make_llm(kind: str, index: int = 0, temperature: float = 0.5,
             forced: str | None = None, **opts):
    """
    ينشئ LLM للمرشّح رقم `index` في سلسلة النوع.

    يرمي IndexError حين تنفد المرشّحات - إشارة للمتصل بأن التجاوز انتهى.
    """
    from crewai import LLM

    cands = chain(kind, forced)
    if index >= len(cands):
        raise IndexError(f"نفدت المزوّدات لنوع {kind} بعد {len(cands)} محاولة")

    provider, model_id = cands[index]
    kw = dict(opts)
    if base := os.getenv("LLM_BASE_URL"):
        kw["base_url"] = base          # وسيط موحّد مثل FreeLLMAPI
    return LLM(model=model_id, temperature=temperature, **kw), provider


def describe() -> str:
    """ملخّص نصّي للحالة - للتشخيص."""
    lines = ["المزوّدون:"]
    ready = {p.name for p in available()}
    for p in PROVIDERS:
        mark = "✓" if p.name in ready else "·"
        why = "" if p.name in ready else f"  (يحتاج {p.key_env})"
        lines.append(f"  {mark} {p.name:12s} {p.note}{why}")
    lines.append("\nالتوجيه:")
    for kind, names in ROUTES.items():
        chosen = [n for n in names if n in ready]
        lines.append(f"  {kind:9s} → {' ← '.join(chosen) if chosen else 'لا مزوّد جاهز'}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
