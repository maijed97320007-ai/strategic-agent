"""
جسر إلى محكّم Mastra (خادم Node جانبي).

Mastra إطار TypeScript بلا SDK لبايثون، فالطريق الوحيد لضمّه هو تشغيله
كخادم مستقل يُنادى عبر HTTP. ولئلا يكون مجرد تكرار لما يفعله CrewAI، دوره
هنا واحد: **تقييم مستقل** للتقرير بنموذج خارج الطاقم الذي أنتجه.

معطّل افتراضياً (JUDGE=0): يتطلب Node ومجلد mastra/node_modules، ويضيف
20-40 ثانية لكل تشغيلة. فعّله حين تريد حكماً على الجودة لا مجرد مخرَج.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

JUDGE_ENABLED = os.getenv("JUDGE", "0").strip().lower() in ("1", "true", "yes")
PORT = int(os.getenv("MASTRA_PORT", "8740"))
BASE = f"http://127.0.0.1:{PORT}"


def _root() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent
    return base / "mastra"


def is_up(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=timeout) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


def ensure_server(wait: float = 25.0) -> bool:
    """يشغّل خادم Mastra إن لم يكن يعمل. يعيد False إن تعذّر."""
    if is_up():
        return True

    root = _root()
    if not (root / "server.mjs").exists() or not (root / "node_modules").is_dir():
        return False

    try:
        subprocess.Popen(
            ["node", "server.mjs"], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, FileNotFoundError):
        return False                 # Node غير مثبّت

    deadline = time.time() + wait
    while time.time() < deadline:
        if is_up():
            return True
        time.sleep(0.5)
    return False


def evaluate(report: str, topic: str, timeout: int = 240) -> dict | None:
    """
    يقيّم التقرير ويعيد الدرجات، أو None إن تعذّر التحكيم.

    لا يرمي أبداً: التقرير منتَج مكتمل، وفشل التقييم لا يجوز أن يُسقطه.
    """
    if not JUDGE_ENABLED or not report.strip():
        return None
    if not ensure_server():
        return None

    body = json.dumps({"report": report, "topic": topic}).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/judge", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except Exception:
        return None

    return d if d.get("parsed") else None


def as_markdown(d: dict | None) -> str:
    """يحوّل التقييم إلى قسم يُذيَّل به التقرير."""
    if not d:
        return ""
    labels = {"sourcing": "الاستناد للمصادر", "novelty": "الجدّة",
              "actionability": "القابلية للتنفيذ", "consistency": "الاتساق الداخلي",
              "realism": "الواقعية الرقمية"}
    rows = "\n".join(f"| {labels.get(k, k)} | {v}/10 |"
                     for k, v in (d.get("scores") or {}).items())
    return (
        "\n\n---\n\n## تقييم مستقل\n\n"
        "*حكم نموذج خارج الطاقم الذي أنتج التقرير — لم يشارك في كتابته.*\n\n"
        "| المحور | الدرجة |\n|---|---|\n" + rows +
        f"\n| **المجموع** | **{d.get('total', '—')}/50** |\n\n"
        f"**الحكم:** {d.get('verdict', '')}\n\n"
        f"**الأضعف:** {d.get('weakest', '')}\n\n"
        f"**للتحسين:** {d.get('fix', '')}\n"
    )


if __name__ == "__main__":
    import glob
    files = sorted(glob.glob("output/2026-*.md"))
    if not files:
        print("لا توجد تقارير في output/")
        sys.exit(1)
    path = sys.argv[1] if len(sys.argv) > 1 else files[-1]
    topic = Path(path).stem.split("_", 1)[-1].replace("_", " ")
    os.environ["JUDGE"] = "1"
    JUDGE_ENABLED = True
    res = evaluate(Path(path).read_text(encoding="utf-8"), topic)
    print(as_markdown(res) if res else "تعذّر التحكيم (Node أو mastra/node_modules غير متوفر)")
