"""
وكيل تصفح - يفتح متصفحاً حقيقياً ويقرأ صفحات لا يكفي فيها مقتطف البحث.

متى يُستخدم؟ Serper يعيد عنواناً ومقتطفاً من كل نتيجة، وهذا يكفي لأغلب
الادعاءات. لكنه لا يكفي حين يكون الرقم داخل جدول في الصفحة، أو خلف نموذج،
أو في تقرير PDF مرتبط. هنا يفتح وكيل التصفح الصفحة ويستخرج المطلوب.

تحذير أداء صريح: زيارة صفحة واحدة تكلّف 20-60 ثانية مقابل ~1 ثانية لبحث
Serper. لذلك الأداة **معطّلة افتراضياً** (BROWSER_AGENT=0) ولا تُشغَّل إلا
حين تطلبها صراحة - إضافتها لكل تشغيلة تنسف مكسب السرعة الذي حققناه.
"""
from __future__ import annotations

import asyncio
import os
import threading

BROWSER_ENABLED = os.getenv("BROWSER_AGENT", "0").strip().lower() in ("1", "true", "yes")
BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "180"))


def _llm():
    """
    browser-use يريد عميل نموذج خاصاً به، لا كائن crewai.

    المزوّد من الموجّه لا من MODEL مباشرةً: بعد أن أُفرغ MODEL ليقرّر
    التوجيه، كان هذا يسقط على OPENAI_API_KEY غير الموجود فيفشل الوكيل
    ستّ مرات متتالية ويعيد «لم يُستخرج شيء» - عطل صامت سببه إعدادٌ في
    مكان آخر.

    كلّهم متوافقون مع واجهة OpenAI، فالفرق عنوانٌ ومفتاح.
    """
    from browser_use import ChatOpenAI

    forced = (os.getenv("MODEL") or "").strip()
    if forced:
        name = forced.split("/")[0]
    else:
        name = ""

    # ترتيب خاص بهذا الوكيل: browser_use يرسل frequency_penalty في كل
    # نداء، وواجهة Gemini المتوافقة مع OpenAI ترفض الحقل بـ400
    # «Unknown name frequency_penalty» فيفشل الوكيل ستّ مرات ويعيد «لم
    # يُستخرج شيء». المزوّدون أدناه يقبلون مجموعة معاملات OpenAI كاملة.
    FULL_OPENAI = ("openrouter", "groq", "cerebras", "mistral", "zai")

    try:
        import providers
        ready = [p for p in providers.available()
                 if p.name in FULL_OPENAI] or \
                [p for p in providers.available() if p.name != "ollama"]
        if forced:
            pick = providers.by_name(name) or (ready[0] if ready else None)
            model_id = forced.split("/", 1)[1] if "/" in forced else forced
        elif ready:
            pick = ready[0]
            model_id = (pick.model_for(providers.FAST) or "").split("/")[-1]
        else:
            pick = None
            model_id = ""
        if pick and model_id and os.getenv(pick.key_env):
            return ChatOpenAI(model=model_id,
                              api_key=os.getenv(pick.key_env),
                              base_url=pick.base_url)
    except Exception:
        pass

    # احتياط أخير: OpenRouter إن كان مفتاحه موجوداً
    if key := os.getenv("OPENROUTER_API_KEY"):
        return ChatOpenAI(model="minimax/minimax-m3:free", api_key=key,
                          base_url="https://openrouter.ai/api/v1")
    raise RuntimeError("لا مزوّد جاهز لوكيل التصفح - أضف مفتاحاً في .env")


def visit(task: str, timeout: int | None = None) -> str:
    """
    ينفّذ مهمة تصفح ويعيد ما استُخرج نصاً.

    يُشغَّل في خيط بحلقة أحداث خاصة: browser-use غير متزامن، وخط أنابيب
    crewai متزامن يعمل داخل خيوط - فاستدعاء asyncio.run مباشرة قد يصطدم
    بحلقة قائمة.
    """
    if not BROWSER_ENABLED:
        return "وكيل التصفح معطّل. فعّله بـ BROWSER_AGENT=1 في .env"

    box: dict = {}

    def runner():
        async def go():
            from browser_use import Agent, Browser
            browser = Browser(headless=True)
            try:
                agent = Agent(task=task, llm=_llm(), browser=browser)
                hist = await agent.run(max_steps=12)
                return hist.final_result() or "لم يُستخرج شيء."
            finally:
                try:
                    await browser.kill()
                except Exception:
                    pass

        loop = asyncio.new_event_loop()
        try:
            box["out"] = loop.run_until_complete(
                asyncio.wait_for(go(), timeout or BROWSER_TIMEOUT))
        except asyncio.TimeoutError:
            box["out"] = f"انتهت مهلة التصفح ({timeout or BROWSER_TIMEOUT} ثانية)."
        except Exception as e:
            box["out"] = f"فشل التصفح: {type(e).__name__}: {e}"
        finally:
            loop.close()

    t = threading.Thread(target=runner, daemon=True, name="browser-agent")
    t.start()
    t.join((timeout or BROWSER_TIMEOUT) + 20)
    return box.get("out", "لم يستجب وكيل التصفح.")


def tool():
    """
    يغلّف `visit` كأداة crewai يستدعيها الباحث عند الحاجة.
    يعيد None حين تكون معطّلة - فلا تظهر للوكيل أصلاً.
    """
    if not BROWSER_ENABLED:
        return None
    try:
        from crewai.tools import tool as crew_tool
    except ImportError:
        return None

    @crew_tool("زيارة صفحة ويب واستخراج معلومة")
    def browse(task: str) -> str:
        """
        يفتح متصفحاً حقيقياً وينفّذ مهمة قراءة على صفحة محددة.
        استخدمها فقط حين لا يكفي مقتطف البحث - مثل رقم داخل جدول
        أو تقرير. اذكر الرابط والمطلوب بدقة.
        مثال: "افتح https://example.com/report واستخرج استهلاك الطاقة النوعي"
        """
        return visit(task)

    return browse


if __name__ == "__main__":
    import sys
    if not BROWSER_ENABLED:
        print("معطّل. شغّله هكذا:  BROWSER_AGENT=1 python browser_agent.py <مهمة>")
        sys.exit(0)
    task = " ".join(sys.argv[1:]) or \
        "افتح https://example.com واذكر عنوان الصفحة الرئيسي"
    print(visit(task))
