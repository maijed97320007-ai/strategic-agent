"""
يبني ملف EXE مستقل للوكيل.

تشغيل:  .venv/Scripts/python.exe build_exe.py

ملاحظة مهمة: الـEXE لا يتضمن مفاتيح API - المفاتيح لا تُدفن في ملف
تنفيذي يُوزَّع. يُنسخ .env بجانبه، وmain.app_dir() يقرأه من **مجلد
البرنامج** لا مجلد التشغيل (كان يقرأ من الثاني فلا يجد الملف حين
يُشغَّل الـEXE من مجلد آخر، ويسقط بـ«المفتاح غير موجود»).
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
NAME = "IdeaAgent"

# ترميز الطرفية على ويندوز cp1252 ورسائل هذا الملف عربية: بلا هذا ينهار
# البناء بـ UnicodeEncodeError **بعد** حذف dist/ فيترك المستخدم بلا نسخة.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# crewai يحمّل هذه ديناميكياً، فـPyInstaller لا يكتشفها بالتحليل الساكن
HIDDEN = [
    "crewai", "crewai_tools", "crewai.llms", "crewai.llms.providers",
    "crewai.llms.providers.openai.completion",
    "crewai.llms.providers.openai_compatible.completion",
    "crewai.cli", "chromadb", "chromadb.telemetry", "chromadb.api.segment",
    "onnxruntime", "tiktoken", "tiktoken_ext", "tiktoken_ext.openai_public",
    "instructor", "litellm", "pydantic", "pydantic.deprecated.decorator",
    "opentelemetry.sdk", "json_repair",
    "rich", "markdown_it",
    "webview", "webview.platforms.edgechromium", "clr_loader", "pythonnet",
    "uvicorn", "uvicorn.logging", "uvicorn.protocols",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on", "uvicorn.loops.auto", "starlette",
]

# ملفات بيانات تُنسخ بجانب الـEXE لا داخله: المستخدم يحرّرها.
# skills/ معرفة نطاقه، وprofile.json تعريف شركته - ودفنهما داخل الأرشيف
# يجعل تعديلهما مستحيلاً بلا إعادة بناء.
BESIDE = ["skills", "profile.json", ".env"]


def local_modules() -> list[str]:
    """
    كل وحدة .py بجوارنا.

    لماذا الاكتشاف لا قائمة مكتوبة: وحداتنا تُستورد **داخل الدوال** عمداً
    (استيراد كسول خفّض الإقلاع من 11 ثانية إلى 0.13)، وتحليل PyInstaller
    ساكن فلا يرى استيراداً داخل دالة. القائمة المكتوبة يدوياً تخلّفت فعلاً:
    كانت تذكر خمس وحدات بينما المشروع يضمّ ثلاثين، فكان الـEXE يُبنى
    ناقصاً ويسقط عند أول استدعاء لخدمة.
    """
    skip = {"build_exe"}
    return sorted(p.stem for p in ROOT.glob("*.py")
                  if p.stem not in skip and not p.stem.startswith("_"))

# ملفات بيانات لا تُجمع تلقائياً
COLLECT = ["crewai", "crewai_tools", "chromadb", "tiktoken_ext", "litellm",
           "webview"]   # pywebview يحمّل واجهاته الخلفية ديناميكياً


def kill_running() -> int:
    """
    يُنهي نسخاً عاملة من البرنامج قبل حذف dist.

    ويندوز يقفل الملفات المفتوحة، فنسخة تعمل تحتجز _internal/**/*.pyd
    ويفشل البناء بـPermissionError على ملف عشوائي - رسالة لا تدلّ على
    السبب إطلاقاً. رُصد فعلياً بعد ترك نسخة اختبار تعمل.
    """
    killed = 0
    try:
        out = subprocess.run(["taskkill", "/F", "/IM", f"{NAME}.exe"],
                             capture_output=True, text=True)
        if out.returncode == 0:
            killed = out.stdout.count("SUCCESS") or 1
    except (OSError, subprocess.SubprocessError):
        pass
    if killed:
        print(f"أُنهيت {killed} نسخة عاملة من {NAME}.exe")
        time.sleep(2)
    return killed


def main() -> int:
    kill_running()
    for d in ("build", "dist"):
        try:
            shutil.rmtree(ROOT / d)
        except FileNotFoundError:
            pass
        except PermissionError as e:
            print()
            print(f"تعذّر حذف {d}: ملف محتجَز — {e.filename}")
            print("أغلق أي نسخة عاملة من البرنامج ثم أعد المحاولة.")
            return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",              # onefile يفكّ ~700MB في temp عند كل تشغيل - بطيء جداً
        "--console",   # نبقيها لعرض الأخطاء إن فشلت النافذة
        "--noconfirm",
        "--name", NAME,
        "--paths", str(ROOT),
    ]
    for h in HIDDEN + local_modules():
        cmd += ["--hidden-import", h]
    for c in COLLECT:
        cmd += ["--collect-all", c]
    # نقطة الدخول صارت app.py (واجهة الويب)
    cmd += ["--add-data", f"{ROOT / 'web'}{os.pathsep}web"]
    cmd.append(str(ROOT / "desktop.py"))

    print("PyInstaller يعمل - قد يستغرق عدة دقائق...\n")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("\nفشل البناء.")
        return r.returncode

    out = ROOT / "dist" / NAME

    # .env و skills/ و profile.json تُنسخ بجانب الـEXE لا داخله:
    # المفاتيح لا تُدفن في ملف تنفيذي يُوزَّع، والمهارات والملف التعريفي
    # يجب أن يبقيا قابلين للتحرير بلا إعادة بناء.
    for item in BESIDE:
        src = ROOT / item
        if not src.exists():
            print(f"  · {item} غير موجود - تخطّي")
            continue
        dst = out / item
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy(src, dst)
        print(f"  · نُسخ {item}")

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"\nتم. المجلد: {out}")
    print(f"الحجم: {size / 1024 / 1024:.0f} ميجابايت")
    print(f"التشغيل: {out / (NAME + '.exe')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
