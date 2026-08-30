"""
A2A - بروتوكول Agent2Agent (a2aproject/a2a-python).

يجعل هذا الوكيل قابلاً للاكتشاف والاستدعاء من وكلاء أُخرى مبنية على أطر
مختلفة (LangGraph، ADK، Semantic Kernel…). الوكيل الخارجي يقرأ بطاقة
الوكيل من `/.well-known/agent-card.json` ثم يرسل مهمة عبر JSON-RPC.

ملاحظة صريحة: هذا يفتح باباً للتكامل، لكنه لا ينفع بذاته ما لم يوجد وكيل
ثانٍ يستدعيه. أُضيف كنقطة ربط جاهزة، لا كميزة تعمل وحدها اليوم.

التوليد بطيء (دقائق)، لذا ننفّذه في خيط ونبثّ التقدّم كتحديثات حالة
بدل حجب الحلقة غير المتزامنة.
"""
from __future__ import annotations

import asyncio
import os
import threading

import main as core

A2A_ENABLED = os.getenv("A2A", "1").strip().lower() not in ("0", "false", "no")


def build_card(base_url: str):
    """بطاقة الوكيل - ما يقرأه وكيل خارجي ليعرف ماذا نُحسن."""
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill

    skill = AgentSkill(
        id="strategic-ideation",
        name="توليد أفكار استراتيجية موثّقة",
        description=(
            "يبحث على الإنترنت بمصادر حقيقية، ثم يمرّ الموضوع على سبعة وكلاء "
            "(أفكار مضادة، تناقضات، سيناريوهات، نقد قاسٍ، دمج استراتيجي) "
            "ويُخرج تقريراً ينتهي بحلول ينفّذها فرد واحد برأس مال محدود."
        ),
        tags=["research", "strategy", "arabic", "ideation", "water"],
        examples=["تحلية المياه بالطاقة الشمسية في عُمان",
                  "إزالة النترات من مياه الشرب"],
    )

    # في 1.1.2 لم يعد للبطاقة حقل url مفرد - العنوان صار داخل
    # supported_interfaces مع بيان بروتوكوله.
    from a2a.types import AgentInterface

    return AgentCard(
        name="وكيل الأفكار الاستراتيجية",
        description="سبعة وكلاء يحوّلون موضوعاً إلى تقرير موثّق بحلول فردية قابلة للتنفيذ",
        version="1.0.0",
        supported_interfaces=[AgentInterface(url=f"{base_url}/a2a",
                                             protocol_binding="JSONRPC")],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "text/markdown"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        skills=[skill],
    )


def _msg(text: str):
    """
    يبني رسالة وكيل. الإصدار 1.1.2 يستخدم أنواعاً مبنية على protobuf
    (Part(text=...) لا TextPart)، وبلا مساعد new_agent_text_message.
    """
    import uuid
    from a2a.types import Message, Part, Role
    return Message(message_id=uuid.uuid4().hex, role=Role.ROLE_AGENT,
                   parts=[Part(text=text)])


class IdeaAgentExecutor:
    """يجسر بين نداء A2A وخط أنابيب crewai."""

    async def execute(self, context, event_queue) -> None:
        topic = (getattr(context, "get_user_input", lambda: "")() or "").strip()
        if not topic:
            await event_queue.enqueue_event(_msg("لم يصل موضوع للعمل عليه."))
            return

        loop = asyncio.get_running_loop()
        updates: asyncio.Queue = asyncio.Queue()

        def on_stage(i, note=""):
            label = core.STAGES[i][0] if i < len(core.STAGES) else f"مرحلة {i}"
            loop.call_soon_threadsafe(
                updates.put_nowait, f"[{i}/{len(core.STAGES)}] {label}"
                                    + (f" — {note}" if note else ""))

        result: dict = {}

        def work():
            try:
                report, _t, timed_out = core.run_creative_agent(topic, on_stage=on_stage)
                path, stats, _w = core.finish_run(report, topic)
                result.update(report=report, path=path, stats=stats, timed_out=timed_out)
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
            finally:
                loop.call_soon_threadsafe(updates.put_nowait, None)

        threading.Thread(target=work, daemon=True, name="a2a-run").start()

        while True:
            msg = await updates.get()
            if msg is None:
                break
            await event_queue.enqueue_event(_msg(msg))

        if err := result.get("error"):
            await event_queue.enqueue_event(_msg(f"فشل: {err}"))
            return

        head = "⚠ تقرير جزئي (انتهت المهلة)\n\n" if result.get("timed_out") else ""
        await event_queue.enqueue_event(_msg(head + result.get("report", "")))

    async def cancel(self, context, event_queue) -> None:
        await event_queue.enqueue_event(
            _msg("الإلغاء غير مدعوم - التشغيلة تكمل حتى سقفها الزمني."))


def routes(base_url: str) -> list:
    """
    يعيد مسارات Starlette لتُضاف إلى الخادم القائم.

    يعيد [] بصمت إن غابت الحزمة أو تغيّرت واجهتها - الوكيل الأساسي يجب
    أن يعمل سواء توفّر A2A أم لا.
    """
    if not A2A_ENABLED:
        return []
    try:
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
        from a2a.server.tasks import InMemoryTaskStore
    except ImportError:
        return []

    try:
        card = build_card(base_url)
        handler = DefaultRequestHandler(
            agent_executor=IdeaAgentExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        return list(create_agent_card_routes(agent_card=card)) + \
            list(create_jsonrpc_routes(request_handler=handler, rpc_url="/a2a"))
    except Exception as e:
        print(f"A2A معطّل ({type(e).__name__}: {e})")
        return []
