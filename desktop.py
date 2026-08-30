"""
نقطة الدخول: نافذة سطح مكتب أصلية.

يشغّل الخادم المحلي في خيط خلفي ثم يعرض الواجهة داخل نافذة WebView2
(محرّك Edge المدمج في ويندوز 11) - لا تبويب متصفح ولا شريط عنوان.

لماذا WebView2 لا واجهة Tk/Qt أصلية؟ العربية تحتاج تشكيل حروف واتجاهاً
ثنائياً، وWebView2 يتقنهما بلا أي عمل إضافي. وهو جزء من ويندوز فلا يزيد
حجم التوزيع.

الاحتياطي: إن غاب WebView2 نفتح Edge بوضع --app (نافذة بلا شريط عنوان)،
ثم متصفحاً عادياً كملاذ أخير.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import app as server

TITLE = "وكيل الأفكار الاستراتيجية"
WIDTH, HEIGHT = 1040, 780


def _free_port(preferred: int) -> int:
    """يتفادى الاصطدام بنسخة أخرى تعمل بالفعل."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _serve(port: int) -> None:
    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")


def _open_native(url: str) -> bool:
    """نافذة WebView2 حقيقية. يعيد False إن لم تتوفر."""
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.create_window(TITLE, url, width=WIDTH, height=HEIGHT,
                              min_size=(760, 560), text_select=True)
        t0 = time.perf_counter()
        webview.start()          # يحجب حتى يغلق المستخدم النافذة
    except Exception as e:
        print(f"تعذّرت النافذة الأصلية ({type(e).__name__}: {e})")
        return False

    # عودة فورية تعني أن النافذة لم تُعرض أصلاً لا أن المستخدم أغلقها -
    # يحدث حين يُشغَّل البرنامج بلا سطح مكتب مرئي (نافذة مخفية، جلسة
    # خدمة). كان يُعدّ نجاحاً فيخرج البرنامج ويقتل تشغيلة جارية معه.
    if time.perf_counter() - t0 < 2.0:
        print("لم تُعرض النافذة الأصلية - نكمل بالمتصفح.")
        return False
    return True


def _wait_for_run(limit: float = 1800) -> None:
    """
    لا نُنهي البرنامج وتشغيلة في منتصفها.

    إغلاق النافذة يقتل العملية وفيها الخادم والخيط العامل، فيضيع تقرير
    قطع نصف طريقه ولا يُحفظ منه شيء. ننتظر انتهاءها - والتشغيلة محكومة
    أصلاً بسقف RUN_TIMEOUT فالانتظار محدود.
    """
    try:
        import app
    except Exception:
        return
    if not app._RUN_LOCK.locked():
        return

    print("تشغيلة جارية - ننتظر انتهاءها قبل الإغلاق (Ctrl+C للإنهاء فوراً).")
    end = time.time() + limit
    try:
        while app._RUN_LOCK.locked() and time.time() < end:
            time.sleep(2)
    except KeyboardInterrupt:
        print("أُنهي بطلبك - التشغيلة الجارية ستضيع.")


def _open_app_window(url: str) -> bool:
    """
    احتياطي: Edge بوضع --app - نافذة مستقلة بلا شريط عنوان ولا تبويبات.
    ملف تعريف منفصل حتى لا يتصادم مع نسخة Edge المفتوحة عند المستخدم.
    """
    for exe in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(exe):
            profile = os.path.join(tempfile.gettempdir(), "ideaagent_win")
            subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                              f"--window-size={WIDTH},{HEIGHT}", "--no-first-run"])
            return True
    return False


def main() -> int:
    port = _free_port(server.PORT)
    threading.Thread(target=_serve, args=(port,), daemon=True, name="http").start()

    if not _wait_ready(port):
        print("تعذّر تشغيل الخادم المحلي.")
        input("اضغط Enter للإغلاق...")
        return 1

    url = f"http://127.0.0.1:{port}"

    if _open_native(url):
        _wait_for_run()                 # أُغلقت النافذة - ننهي بعد أي تشغيلة
        return 0

    print(f"الواجهة على {url}")
    if not _open_app_window(url):
        webbrowser.open(url)
    print("للإيقاف: أغلق هذه النافذة")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
