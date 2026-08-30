"""
التحديث التلقائي - الرادار يعمل بلا أن تطلبه.

المشكلة: كشف الفرص لا يعمل إلا حين يتذكّر المستخدم تشغيله. ورادار يُشغَّل
مرّة كل أسبوعين ليس رادراً - المناقصة التي فاتت لا تُدرَك بعد إغلاقها.
الفارق بين «أداة تجيب حين أسأل» و«نظام يخبرني قبل أن أسأل» هو الجدولة.

التصميم: خيط خلفي داخل الخادم لا مهمّة في مجدول ويندوز. السبب أنّ المهمّة
الخارجية تحتاج تثبيتاً وصلاحيات وتكسر عند نقل المجلد، بينما الخيط يعيش مع
البرنامج ويموت معه - وهذا هو السلوك المتوقّع لبرنامج سطح مكتب.

قيود مقصودة:
  · قفل التشغيل نفسه الذي يحمي التقارير: جولة كشف أثناء تقرير جارٍ تتسابق
    على النموذج. إن كان مشغولاً نؤجّل لا نُلغي.
  · الحالة تُحفظ على القرص: إغلاق البرنامج ليلاً ثم فتحه صباحاً يجب ألّا
    يعيد جولة تمّت قبل ساعة، ولا أن يؤجّل جولة فاتت منذ يومين.
  · الفشل لا يوقف الجدولة: انقطاع إنترنت يؤجّل للدورة التالية ويُسجَّل.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def _root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent


STATE = _root() / "schedule.json"

# 0 يعطّل الجدولة تماماً
HOURS = float(os.getenv("AUTO_UPDATE_HOURS", "6"))

# لا نبدأ جولة فور الإقلاع: المستخدم فتح البرنامج ليكتب موضوعاً، لا
# لينتظر دقيقتين حتى يستجيب. نمهله ثم نعمل.
FIRST_DELAY = float(os.getenv("AUTO_UPDATE_DELAY", "90"))

_thread: threading.Thread | None = None
_stop = threading.Event()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(d: dict) -> None:
    try:
        STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except OSError:
        pass


def _age_hours(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return 1e9
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def due(state: dict | None = None) -> bool:
    state = load_state() if state is None else state
    return HOURS > 0 and _age_hours(state.get("last_run")) >= HOURS


def run_once(force: bool = False) -> dict:
    """
    جولة كشف واحدة. يعيد ملخّصاً ويحدّث الحالة.

    لا يرمي: الجدولة يجب أن تنجو من كل خطأ، فالخطأ يُسجَّل في الحالة
    ويظهر في الواجهة بدل أن يقتل الخيط صامتاً.
    """
    state = load_state()
    if not force and not due(state):
        return {"skipped": "لم يحن الموعد", "state": state}

    started = time.perf_counter()
    try:
        import opportunity_run as opp
        scored = opp.detect()
        opp.persist(scored)
        new_high = sum(1 for s in scored if s.score >= 70)
        state.update({
            "last_run": _now(),
            "last_ok": True,
            "last_error": "",
            "last_found": len(scored),
            "last_high": new_high,
            "last_seconds": round(time.perf_counter() - started),
            "runs": state.get("runs", 0) + 1,
        })
    except Exception as e:
        state.update({
            "last_run": _now(),          # نؤجّل دورة كاملة لا نعيد المحاولة فوراً
            "last_ok": False,
            "last_error": f"{type(e).__name__}: {e}",
            "last_seconds": round(time.perf_counter() - started),
            "runs": state.get("runs", 0) + 1,
        })
    save_state(state)
    return state


def _loop(acquire=None, release=None, defer_if=None) -> None:
    if _stop.wait(FIRST_DELAY):
        return
    while not _stop.is_set():
        # الأولوية للمستخدم: الجولة تأخذ قفل التشغيل نحو دقيقة، فإن
        # خطفته قبله رُفضت تشغيلته برسالة «هناك تشغيلة جارية» وبدا
        # البرنامج معطّلاً. التأجيل هنا يمنع ذلك قبل أن يقع.
        if due() and not (defer_if and defer_if()):
            got = True
            if acquire is not None:
                got = acquire()
            if got:
                try:
                    run_once()
                finally:
                    if got and release is not None:
                        release()
            # مشغول: لا نحدّث الحالة فيبقى الموعد مستحقّاً ونعيد بعد دقائق
        # نستيقظ كل خمس دقائق لا كل ساعة: يجعل التأجيل بسبب الانشغال
        # يُستدرَك سريعاً، وتكلفة الاستيقاظ صفر عملياً.
        if _stop.wait(300):
            return


def start(acquire=None, release=None, defer_if=None) -> bool:
    """يشغّل الخيط. يعيد False إن كانت الجدولة معطّلة أو تعمل أصلاً."""
    global _thread
    if HOURS <= 0 or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(acquire, release, defer_if),
                               daemon=True, name="scheduler")
    _thread.start()
    return True


def stop() -> None:
    _stop.set()


def status() -> dict:
    s = load_state()
    age = _age_hours(s.get("last_run"))
    return {
        "enabled": HOURS > 0,
        "every_hours": HOURS,
        "running": bool(_thread and _thread.is_alive()),
        "last_run": s.get("last_run"),
        "last_ok": s.get("last_ok"),
        "last_error": s.get("last_error") or "",
        "last_found": s.get("last_found"),
        "last_high": s.get("last_high"),
        "last_seconds": s.get("last_seconds"),
        "runs": s.get("runs", 0),
        "hours_since": None if age > 1e8 else round(age, 1),
        "hours_until": None if HOURS <= 0 else max(0.0, round(HOURS - age, 1))
        if age <= 1e8 else 0.0,
    }


def render(s: dict | None = None) -> str:
    s = s or status()
    if not s["enabled"]:
        return "التحديث التلقائي معطّل (AUTO_UPDATE_HOURS=0)."

    out = [f"التحديث التلقائي: كل {s['every_hours']:g} ساعة"
           f"{' · يعمل' if s['running'] else ' · متوقّف'}"]
    if s["last_run"]:
        when = s["last_run"].replace("T", " ")[:16]
        mark = "✓" if s["last_ok"] else "✗"
        out.append(f"  {mark} آخر جولة: {when} (قبل {s['hours_since']} ساعة)")
        if s["last_ok"]:
            out.append(f"     رصد {s['last_found']} فرصة، منها {s['last_high']} "
                       f"بدرجة 70+ · {s.get('last_seconds', '؟')} ثانية")
        else:
            out.append(f"     فشلت: {s['last_error'][:120]}")
        out.append(f"  الجولة التالية بعد {s['hours_until']} ساعة")
    else:
        out.append("  لم تُشغَّل جولة بعد.")
    out.append(f"  إجمالي الجولات: {s['runs']}")
    return "\n".join(out)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    if "--run" in sys.argv:
        print("جولة كشف الآن…")
        st = run_once(force=True)
        print(render())
    else:
        print(render())
        print("\nللتشغيل الفوري: python scheduler.py --run")
