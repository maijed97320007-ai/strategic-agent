"""
نقاط الحفظ - لا تُعاد مرحلة نجحت.

المشكلة التي تحلّها: فشل التركيب كان يهدر البحث والموجتين معه. تشغيلة
واحدة تكلّف دقيقتين وعشرات النداءات، وإعادتها من الصفر لأن المرحلة
الأخيرة تعثّرت إهدار صريح - ورُصد فعلياً حين رسب نموذج محلي في المرحلة
الأولى فضاعت 17 دقيقة.

كل مرحلة تُحفظ بمفتاح = (الموضوع + النموذج + المرحلة). إعادة التشغيل على
نفس الموضوع تستأنف من آخر نقطة ناجحة.

التخزين ملفات JSON عادية: لا خادم، ولا مخطط، وقابلة للفحص بالعين.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

TTL_HOURS = int(os.getenv("CHECKPOINT_TTL_HOURS", "24"))
ENABLED = os.getenv("CHECKPOINTS", "1").strip().lower() not in ("0", "false", "no")


def _root() -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent
    d = base / ".checkpoints"
    d.mkdir(exist_ok=True)
    return d


def _key(topic: str, model: str, stage: str) -> str:
    h = hashlib.sha256(f"{topic}|{model}|{stage}".encode("utf-8")).hexdigest()[:16]
    return f"{stage}_{h}"


def _path(topic: str, model: str, stage: str) -> Path:
    return _root() / f"{_key(topic, model, stage)}.json"


def load(topic: str, model: str, stage: str):
    """يعيد المحفوظ إن وُجد وكان طازجاً، وإلا None."""
    if not ENABLED:
        return None
    p = _path(topic, model, stage)
    if not p.is_file():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    age_h = (time.time() - blob.get("at", 0)) / 3600
    if age_h > TTL_HOURS:
        p.unlink(missing_ok=True)     # قديم - الأسواق تتغيّر والمصادر تُحدَّث
        return None
    return blob.get("data")


def save(topic: str, model: str, stage: str, data) -> None:
    """يحفظ مخرَج مرحلة. الفشل صامت - نقطة حفظ ضائعة أهون من تشغيلة ساقطة."""
    if not ENABLED:
        return
    try:
        _path(topic, model, stage).write_text(
            json.dumps({"at": time.time(), "topic": topic, "model": model,
                        "stage": stage, "data": data},
                       ensure_ascii=False),
            encoding="utf-8")
    except (OSError, TypeError):
        pass


def clear(topic: str | None = None, model: str = "") -> int:
    """يمسح نقاط الحفظ - كلها أو لموضوع بعينه. يعيد العدد الممسوح."""
    n = 0
    for p in _root().glob("*.json"):
        if topic:
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                if blob.get("topic") != topic:
                    continue
            except (OSError, json.JSONDecodeError):
                continue
        p.unlink(missing_ok=True)
        n += 1
    return n


def status() -> list[dict]:
    out = []
    for p in sorted(_root().glob("*.json")):
        try:
            b = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"stage": b.get("stage"), "topic": b.get("topic", "")[:40],
                    "age_min": round((time.time() - b.get("at", 0)) / 60),
                    "kb": round(p.stat().st_size / 1024, 1)})
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "clear":
        print(f"مُسح {clear(args[1] if len(args) > 1 else None)} نقطة حفظ")
    else:
        rows = status()
        for r in rows:
            print(f"  {r['stage']:10s} {r['age_min']:4d}د  {r['kb']:6.1f}KB  {r['topic']}")
        print(f"\nالمجموع: {len(rows)} نقطة في {_root()}  (صلاحية {TTL_HOURS} ساعة)")
