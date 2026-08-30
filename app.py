"""
واجهة الويب المحلية للوكيل.

لماذا الويب لا الطرفية؟ الطرفية شبكة خانات أحادية العرض بلا bidi - العربية
فيها إما منفصلة الحروف أو مبعثرة الترتيب. المتصفح يتقن التشكيل والاتجاه
ويسمح بتصميم فعلي. وuvicorn/starlette موجودان أصلاً مع crewai فصفر تبعيات.

الإقلاع فوري: crewai لا يُستورد إلا عند بدء تشغيلة حقيقية (انظر main.py).
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

import main as core

ROOT = Path(__file__).parent
# داخل EXE تُفكّ ملفات البيانات في sys._MEIPASS لا بجانب الملف التنفيذي،
# بينما المخرجات (output/) يجب أن تُكتب بجانب الـEXE حيث يراها المستخدم.
WEB = Path(getattr(sys, "_MEIPASS", ROOT)) / "web"
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8730"))


# ---------- الصفحة ----------
async def index(_request):
    # no-store عمداً: تبويب يحمل نسخة قديمة من الصفحة يظل يتصرّف بالمنطق
    # القديم بعد تحديث التطبيق. حدث فعلاً - نسخة EventSource قديمة ظلّت
    # تعيد تشغيل موضوع محفوظ عند كل إعادة تشغيل للخادم.
    return FileResponse(WEB / "index.html", media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store, must-revalidate"})


def _local_models() -> list[dict]:
    """نماذج Ollama المنزّلة، مع تقدير سرعتها من حجمها."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            models = json.loads(r.read()).get("models", [])
    except Exception:
        return []
    out = []
    for m in models:
        gb = m.get("size", 0) / 1e9
        out.append({"id": f"ollama/{m['name']}", "label": m["name"],
                    "gb": round(gb, 1), "local": True})
    return sorted(out, key=lambda x: x["gb"])


def _routing() -> dict:
    """
    المزوّدون الجاهزون ومَن يتصدّر كل مسار.

    الواجهة كانت تعرض معرّفات النماذج الخام (minimax-m3:free، qwen2.5:3b)
    كشرائح للاختيار. صارت بلا معنى بعد أن أصبح التوجيه تلقائياً حسب نوع
    المهمة: المستخدم لا يختار نموذجاً واحداً، والنظام يستعمل ثلاثة في
    التشغيلة الواحدة ويتجاوز الفاشل. اسم المزوّد يقول الحقيقة، والمعرّف
    الخام يقول نصفها.
    """
    try:
        import providers
        ready = [p.name for p in providers.available()]
        first = {}
        for kind in (providers.FAST, providers.ANALYTIC, providers.BROAD):
            chain = providers.chain(kind)
            first[kind] = chain[0][0].name if chain else None

        # التوزيع الفعلي للوكلاء: من يعمل على ماذا في هذه التشغيلة
        spread = {}
        for code, kind in core.KIND_OF.items():
            try:
                _llm, pr = providers.make_llm(kind, 0,
                                              spread=core.AGENT_SPREAD.get(code, 0))
                spread[code] = pr.name
            except Exception:
                pass
        return {"ready": ready, "routes": first, "agents": spread}
    except Exception:
        return {"ready": [], "routes": {}}


async def info(_request):
    _touch()          # فتح الصفحة = المستخدم حاضر
    return JSONResponse({
        "model": core.MODEL,
        "is_local": core.is_local(),
        "stages": core.STAGES,
        "key_error": core.check_key(),
        "timeout_min": core.RUN_TIMEOUT // 60,
        "routing": _routing(),
    })


# ---------- التشغيل مع بث حيّ ----------
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# تشغيلة واحدة في كل لحظة - أداة محلية لمستخدم واحد، وتشغيلتان متوازيتان
# تضاعفان الضغط على حصة النموذج المجاني.
#
# القفل قابل للاسترداد عمداً: النسخة الأولى استخدمت threading.Lock مجرّداً،
# فكانت تشغيلة يهجرها المتصفح (إغلاق تبويب، انقطاع SSE) تحتجزه حتى نهاية
# المهلة - 25 دقيقة يرى فيها المستخدم 409 بلا تفسير ولا مخرج. الآن نتتبّع
# الحالة ونتيح الإلغاء.
_RUN_LOCK = threading.Lock()
_ACTIVE: dict = {}       # {"topic", "started", "stage"} حين تكون تشغيلة جارية

# آخر لمسة من المستخدم. الجدولة تؤجّل جولتها ما دام قريباً: جولة الكشف
# تحتجز قفل التشغيل نحو دقيقة، فخطفها قبل المستخدم يردّ تشغيلته برسالة
# «هناك تشغيلة جارية» ويبدو البرنامج بطيئاً أو معطّلاً بلا سبب ظاهر.
_LAST_TOUCH: float = 0.0
IDLE_BEFORE_AUTO = float(os.getenv("IDLE_BEFORE_AUTO_MIN", "10")) * 60


def _touch() -> None:
    global _LAST_TOUCH
    _LAST_TOUCH = time.time()


def _user_active() -> bool:
    return (time.time() - _LAST_TOUCH) < IDLE_BEFORE_AUTO


def _run_state() -> dict:
    if not _RUN_LOCK.locked() or not _ACTIVE:
        return {"active": False}
    return {
        "active": True,
        "topic": _ACTIVE.get("topic", ""),
        "stage": _ACTIVE.get("stage", 0),
        "elapsed": round(time.perf_counter() - _ACTIVE.get("started", 0)),
    }


async def status(_request):
    return JSONResponse(_run_state())


async def cancel(_request):
    """
    يتخلّى عن التشغيلة الجارية ويحرّر القفل.

    الخيط نفسه لا يُقتل - لا يمكن قتل خيط بايثون عالق على مقبس شبكة - لكنه
    daemon فيموت مع العملية، ومخرجاته تُهمَل. بناء وكلاء جدد لكل تشغيلة
    (بلا تخزين عالمي) هو ما يجعل هذا آمناً: التشغيلة الجديدة لا تشارك
    الخيط المهجور أي كائن.
    """
    if not _RUN_LOCK.locked():
        return JSONResponse({"cancelled": False, "reason": "لا توجد تشغيلة جارية"})
    _ACTIVE["abandoned"] = True
    try:
        _RUN_LOCK.release()
    except RuntimeError:
        pass                  # حُرّر بين الفحص والتحرير
    return JSONResponse({"cancelled": True})


async def run(request):
    _touch()
    topic = (request.query_params.get("topic") or "").strip()
    if not topic:
        return JSONResponse({"error": "لا يوجد موضوع"}, status_code=400)

    # تبديل النموذج لهذه التشغيلة. آمن لأن القفل يمنع التوازي والوكلاء
    # يُبنون من جديد كل مرة - لا كائن قديم يحمل النموذج السابق.
    if picked := (request.query_params.get("model") or "").strip():
        core.MODEL = picked

    if err := core.check_key():
        return JSONResponse({"error": err}, status_code=400)
    if _RUN_LOCK.locked():
        return JSONResponse(
            {"error": "هناك تشغيلة جارية بالفعل. انتظر انتهاءها أو أعد تحميل الصفحة."},
            status_code=409)

    async def stream():
        # الخيط ينفّذ خط الأنابيب ويدفع الأحداث في طابور،
        # والحلقة غير المتزامنة تصرفها للمتصفح بلا حجب.
        q: queue.Queue = queue.Queue()
        started = time.perf_counter()

        def worker():
            if not _RUN_LOCK.acquire(blocking=False):
                q.put(("failed", {"error": "هناك تشغيلة جارية بالفعل."}))
                q.put((None, None))
                return

            _ACTIVE.clear()
            _ACTIVE.update({"topic": topic, "started": started, "stage": 0})

            def on_stage(i, note=""):
                _ACTIVE["stage"] = i
                q.put(("stage", {"index": i, "note": note}))

            try:
                report, _tasks, timed_out = core.run_creative_agent(topic, on_stage=on_stage)
                path, stats, warns = core.finish_run(report, topic)
                q.put(("done", {
                    "path": str(Path(path).resolve()),
                    "filename": Path(path).name,
                    "chars": len(report),
                    "seconds": round(time.perf_counter() - started),
                    "stats": stats,
                    "warnings": warns,
                    "timed_out": timed_out,
                }))
            except Exception as e:
                q.put(("failed", {"error": f"{type(e).__name__}: {e}"}))
            finally:
                # قد يكون /api/cancel حرّر القفل بالفعل - لا نحرّره مرتين
                # وإلا سرقناه من تشغيلة جديدة بدأت بعد الإلغاء.
                if not _ACTIVE.get("abandoned"):
                    _ACTIVE.clear()
                    try:
                        _RUN_LOCK.release()
                    except RuntimeError:
                        pass
                q.put((None, None))

        threading.Thread(target=worker, daemon=True, name="run").start()

        while True:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.25)
                continue
            if kind is None:
                break
            yield _sse(kind, payload)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def dash(_request):
    """لقطة اللوحة - قراءة فقط، بلا نموذج ولا شبكة."""
    try:
        import dashboard
        return JSONResponse(dashboard.snapshot(out_dir=core.out_dir_default()))
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


async def schedule_status(_request):
    """حالة التحديث التلقائي - قراءة فقط."""
    try:
        import scheduler
        return JSONResponse(scheduler.status())
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


async def service_list(_request):
    """فهرس الخدمات المبنيّة - قراءة فقط."""
    try:
        import services
        return JSONResponse({"services": services.catalog()})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


async def service_run(request):
    """
    ينفّذ خدمة واحدة ويعيد نصّها.

    البطيئة تأخذ قفل التشغيل نفسه الذي يحمي التقارير: غرفة الحرب وكشف
    الفرص يستدعيان النموذج بالتوازي، وتشغيلهما مع تقرير جارٍ يتسابق على
    نفس الموارد - وهو ما أنتج سابقاً RuntimeError: Executor is already
    running. الرفض الصريح أوضح من تعثّر غامض بعد دقيقتين.
    """
    _touch()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    sid = (payload.get("id") or "").strip()

    import services
    svc = services.BY_ID.get(sid)
    if svc is None:
        return JSONResponse({"error": f"خدمة غير معروفة: {sid}"}, status_code=404)

    slow = svc.kind == "slow"
    if slow and not _RUN_LOCK.acquire(blocking=False):
        return JSONResponse({"error": "هناك تشغيلة جارية — انتظر انتهاءها."},
                            status_code=409)

    t0 = time.perf_counter()
    try:
        text = await asyncio.to_thread(services.run, sid,
                                       {"input": payload.get("input") or ""})
        return JSONResponse({"id": sid, "text": text,
                             "seconds": round(time.perf_counter() - t0, 1)})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    finally:
        if slow:
            try:
                _RUN_LOCK.release()
            except RuntimeError:
                pass


def _safe(raw: str) -> Path | None:
    """يقيّد أي مسار وارد بمجلد output - وإلا صار المسار قراءةً حرّة للقرص."""
    try:
        q = Path(raw).resolve()
        q.relative_to(Path(core.out_dir_default()).resolve())
    except (ValueError, OSError):
        return None
    return q if q.is_file() else None


async def report_md(request):
    """نصّ التقرير كما هو - الواجهة تعرضه في الصفحة بدل فتح ملف."""
    q = _safe(request.query_params.get("p") or "")
    if q is None:
        return JSONResponse({"error": "مسار غير مسموح أو غير موجود"}, status_code=403)
    return JSONResponse({"markdown": q.read_text(encoding="utf-8"),
                         "name": q.stem})


async def make_pdf(request):
    """
    يولّد PDF عند الطلب لا في كل تشغيلة.

    توليده يشغّل Edge بلا واجهة ويضيف ثوانيَ لملفٍ قد لا يُفتح أصلاً،
    والتقرير صار يُقرأ في الصفحة. فبقي زرّاً لمن يريد نسخة للطباعة.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    q = _safe(payload.get("path") or "")
    if q is None:
        return JSONResponse({"error": "مسار غير مسموح"}, status_code=403)
    try:
        import pdf
        out = await asyncio.to_thread(pdf.markdown_to_pdf, q)
        return JSONResponse({"path": str(out), "name": Path(out).name})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


ASK_BRIEF = """أنت تجيب عن أسئلة حول تقرير أنتجتَه للتوّ.

قواعد ملزِمة:
- أجب من التقرير نفسه. إن لم يحوِ الجواب فقل ذلك صراحةً ولا تخترع.
- لا تعِد سرد التقرير - أجب عن السؤال المطروح وحده.
- بالعربية، مباشرةً، بلا مقدمات ولا اعتذارات.
- الأرقام التي تذكرها يجب أن تكون في التقرير حرفياً.

--- التقرير ---
{report}
--- نهاية التقرير ---

السؤال: {question}"""


async def ask(request):
    """سؤال متابعة عن تقرير - يقرأ التقرير كسياق ولا يبحث من جديد."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    question = (payload.get("question") or "").strip()
    q = _safe(payload.get("path") or "")
    if not question:
        return JSONResponse({"error": "لا يوجد سؤال"}, status_code=400)
    if q is None:
        return JSONResponse({"error": "التقرير غير موجود"}, status_code=404)

    _touch()
    if not _RUN_LOCK.acquire(blocking=False):
        return JSONResponse({"error": "هناك تشغيلة جارية — انتظر انتهاءها."},
                            status_code=409)
    try:
        def run():
            import providers
            # سياق مليون رمز عند Gemini يستوعب التقرير كاملاً، وحدّ 60 ألف
            # حرف يحمي المزوّدين الأضيق سياقاً في سلسلة التجاوز.
            body = q.read_text(encoding="utf-8")[:60000]
            llm, _p = providers.make_llm(providers.BROAD, 0, temperature=0.3,
                                         max_tokens=1200, timeout=180)
            return str(llm.call(ASK_BRIEF.format(report=body, question=question)))

        answer = await asyncio.to_thread(run)
        return JSONResponse({"answer": answer.strip()})
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    finally:
        try:
            _RUN_LOCK.release()
        except RuntimeError:
            pass


async def serve_file(request):
    """
    يفتح ملف تقرير. مقيّد بمجلد output وحده حتى لا يتحوّل لقراءة عشوائية
    للقرص عبر مسارات مثل ../../
    """
    raw = request.query_params.get("p") or ""
    try:
        p = Path(raw).resolve()
        p.relative_to(Path(core.out_dir_default()).resolve())
    except (ValueError, OSError):
        return JSONResponse({"error": "مسار غير مسموح"}, status_code=403)

    if not p.is_file():
        return JSONResponse({"error": "غير موجود"}, status_code=404)

    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if p.suffix == ".md":
        mime = "text/markdown; charset=utf-8"
    return FileResponse(p, media_type=mime)


_ROUTES = [
    Route("/", index),
    Route("/api/info", info),
    Route("/api/status", status),
    Route("/api/cancel", cancel, methods=["POST"]),
    Route("/api/run", run),
    Route("/api/dashboard", dash),
    Route("/api/schedule", schedule_status),
    Route("/api/services", service_list),
    Route("/api/service", service_run, methods=["POST"]),
    Route("/api/report", report_md),
    Route("/api/pdf", make_pdf, methods=["POST"]),
    Route("/api/ask", ask, methods=["POST"]),
    Route("/api/file", serve_file),
]

# A2A: يضيف /.well-known/agent-card.json و /a2a ليكتشفنا وكلاء آخرون.
# يعيد [] بصمت إن غابت الحزمة - لا يجوز أن يمنع غيابُه تشغيلَ الوكيل.
try:
    import a2a_server
    _ROUTES += a2a_server.routes(f"http://{HOST}:{PORT}")
except Exception as e:
    print(f"A2A غير مفعّل: {type(e).__name__}: {e}")

app = Starlette(routes=_ROUTES)


def start(open_browser: bool = True) -> None:
    url = f"http://{HOST}:{PORT}"
    print(f"وكيل الأفكار الاستراتيجية يعمل على  {url}")

    # التحديث التلقائي يشارك قفل التشغيل: جولة كشف أثناء تقرير جارٍ
    # تتسابق معه على النموذج. نمرّر القفل بدل أن نكرّره.
    try:
        import scheduler
        if scheduler.start(acquire=lambda: _RUN_LOCK.acquire(blocking=False),
                           release=_RUN_LOCK.release,
                           defer_if=_user_active):
            print(f"التحديث التلقائي: كل {scheduler.HOURS:g} ساعة")
        elif scheduler.HOURS <= 0:
            print("التحديث التلقائي: معطّل")
    except Exception as e:
        print(f"تعذّرت الجدولة: {type(e).__name__}: {e}")

    print("للإيقاف: Ctrl+C")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    try:
        start()
    except KeyboardInterrupt:
        print("\nتم الإيقاف.")
