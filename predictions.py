"""
محرك التنبؤ وتتبّع الدقة.

    تنبؤ: «المنافس أ قد يدخل سوق معالجة المياه في عُمان»
    احتمال: 67%   ·   المهلة: 90 يوماً
        ↓  بعد 90 يوماً
    الواقع: نعم / لا
        ↓
    دقة التنبؤ  →  معايرة الاحتمالات القادمة

القيمة الحقيقية ليست في التنبؤ بل في **قياسه**. نظام يتنبأ بلا تتبّع
يكرّر أخطاءه إلى الأبد؛ ونظام يقيس يكتشف أنه متفائل بمقدار كذا فيصحّح.

المعايرة تُحسب في الكود: نقارن متوسط الاحتمال المُعلن بنسبة التحقق
الفعلية. الفرق بينهما هو انحياز النظام، ويُطبَّق خصماً على التنبؤات
التالية.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

DB = "knowledge.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id          INTEGER PRIMARY KEY,
    event       TEXT NOT NULL,
    subject     TEXT,
    probability INTEGER NOT NULL,
    deadline    TEXT NOT NULL,
    basis       TEXT,
    evidence    TEXT,
    result      TEXT,
    resolved_at TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pred_dl ON predictions(deadline, result);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


def add(event: str, probability: int, days: int = 90, subject: str = "",
        basis: str = "", evidence: list[str] | None = None,
        path: str = DB) -> int:
    """يسجّل تنبؤاً بمهلة. الاحتمال يُعايَر تلقائياً قبل الحفظ."""
    p = calibrate(max(1, min(99, int(probability))), path)
    dl = (date.today() + timedelta(days=max(1, days))).isoformat()
    con = db(path)
    pid = con.execute(
        "INSERT INTO predictions(event,subject,probability,deadline,basis,"
        "evidence,created_at) VALUES(?,?,?,?,?,?,?)",
        (event, subject, p, dl, basis,
         json.dumps(evidence or [], ensure_ascii=False), _now())).lastrowid
    con.commit()
    con.close()
    return pid


def resolve(pid: int, happened: bool, note: str = "", path: str = DB) -> None:
    """يسجّل ما حدث فعلاً - هذه الخطوة هي التي تجعل النظام يتعلّم."""
    con = db(path)
    con.execute("UPDATE predictions SET result=?, resolved_at=?, note=?"
                " WHERE id=?",
                ("YES" if happened else "NO", _now(), note, pid))
    con.commit()
    con.close()


def due(path: str = DB) -> list[dict]:
    """تنبؤات حان موعدها ولم تُحسم - تحتاج حكمك."""
    con = db(path)
    rows = con.execute(
        "SELECT * FROM predictions WHERE result IS NULL AND deadline<=?"
        " ORDER BY deadline", (date.today().isoformat(),)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def open_ones(path: str = DB) -> list[dict]:
    con = db(path)
    rows = con.execute(
        "SELECT * FROM predictions WHERE result IS NULL ORDER BY deadline"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def accuracy(path: str = DB) -> dict:
    """
    دقة النظام ومعايرته.

    brier: متوسط مربع الخطأ (0 مثالي، 0.25 = تخمين عشوائي).
    bias: الفرق بين متوسط الثقة المعلنة والتحقق الفعلي. موجب = متفائل.
    """
    con = db(path)
    rows = con.execute(
        "SELECT probability, result FROM predictions WHERE result IS NOT NULL"
    ).fetchall()
    con.close()

    n = len(rows)
    if not n:
        return {"resolved": 0, "hit_rate": None, "brier": None,
                "bias": 0, "calibration": "لا بيانات بعد"}

    hits = sum(1 for r in rows if r["result"] == "YES")
    brier = sum(((r["probability"] / 100) - (1 if r["result"] == "YES" else 0)) ** 2
                for r in rows) / n
    avg_conf = sum(r["probability"] for r in rows) / n
    hit_rate = 100 * hits / n
    bias = round(avg_conf - hit_rate)

    if abs(bias) <= 5:
        label = "معايرة جيدة"
    elif bias > 0:
        label = f"متفائل بـ{bias} نقطة"
    else:
        label = f"متشائم بـ{abs(bias)} نقطة"

    return {"resolved": n, "hits": hits, "hit_rate": round(hit_rate),
            "avg_confidence": round(avg_conf), "brier": round(brier, 3),
            "bias": bias, "calibration": label}


def calibrate(raw_probability: int, path: str = DB) -> int:
    """
    يصحّح احتمالاً جديداً بانحياز النظام المرصود.

    نحتاج 8 تنبؤات محسومة على الأقل قبل التصحيح - أقل من ذلك ضجيج،
    وتصحيح على ضجيج أسوأ من عدم التصحيح.
    """
    acc = accuracy(path)
    if acc["resolved"] < 8 or not acc.get("bias"):
        return raw_probability
    # نصحّح نصف الانحياز فقط: التصحيح الكامل يتأرجح
    return max(1, min(99, round(raw_probability - acc["bias"] * 0.5)))


PREDICT_BRIEF = """أمامك سياق سوق. استخرج تنبؤات **قابلة للحسم**.

--- السياق ---
{context}
--- نهاية ---

التنبؤ القابل للحسم هو الذي يمكن الحكم عليه بنعم/لا في تاريخ محدد.

مقبول : "شركة أ تعلن دخول سوق معالجة المياه في عُمان خلال 90 يوماً"
مرفوض : "السوق سينمو" — لا يمكن حسمه

أعد JSON صالحاً فقط:
{{"items": [
  {{"idea": "التنبؤ بصيغة قابلة للحسم بنعم/لا",
    "detail": "الأساس الذي يجعله محتملاً",
    "score": 0-100,
    "risks": ["ما الذي قد يمنع حدوثه"],
    "evidence": [], "counterarguments": []}}
]}}

`score` = احتمال الحدوث من 100. لا تُدرج ما لا يمكن حسمه."""


def generate(context: str, days: int = 90, subject: str = "",
             on_stage=None, path: str = DB) -> list[dict]:
    """يولّد تنبؤات من سياق ويسجّلها بمهلة."""
    import main
    import pipeline
    import sources as S

    if on_stage:
        on_stage("توليد تنبؤات قابلة للحسم...")

    agents = main.build_agents()
    mk = agents.get("_rebuild")
    raw = pipeline._run_one("PRED", agents["A4"],
                            PREDICT_BRIEF.format(context=context[:6000]),
                            mk("A4") if mk else None)

    out = []
    for it in pipeline.parse_items(raw, "PRED", S.Registry()):
        pid = add(it.idea, it.score, days=days, subject=subject,
                  basis=it.detail, path=path)
        out.append({"id": pid, "event": it.idea,
                    "raw_probability": it.score,
                    "stored_probability": calibrate(it.score, path),
                    "basis": it.detail,
                    "deadline": (date.today() + timedelta(days=days)).isoformat()})
    return out


def render_scoreboard(path: str = DB) -> str:
    acc = accuracy(path)
    op, dl = open_ones(path), due(path)
    out = ["", "=" * 56, "  لوحة التنبؤات", "=" * 56]
    if acc["resolved"]:
        out += [f"  محسومة      : {acc['resolved']}",
                f"  تحققت       : {acc['hits']} ({acc['hit_rate']}%)",
                f"  متوسط الثقة : {acc['avg_confidence']}%",
                f"  Brier       : {acc['brier']}  (0 مثالي · 0.25 عشوائي)",
                f"  المعايرة    : {acc['calibration']}"]
    else:
        out.append("  لا تنبؤات محسومة بعد - الدقة تُقاس بعد أول 8")
    out += ["", f"  مفتوحة: {len(op)} | حان موعدها: {len(dl)}"]
    for d in dl[:8]:
        out.append(f"    [{d['probability']:3d}%] {d['event'][:52]}  (انتهت {d['deadline']})")
    if dl:
        out.append("\n  احسمها: python predictions.py resolve <id> yes|no")
    return "\n".join(out)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "resolve" and len(args) >= 3:
        resolve(int(args[1]), args[2].lower() in ("yes", "y", "نعم", "1"),
                " ".join(args[3:]))
        print("سُجّل.")
        print(render_scoreboard())
    elif args and args[0] == "add" and len(args) >= 3:
        pid = add(" ".join(args[2:]), int(args[1]))
        print(f"تنبؤ #{pid} · احتمال مُعايَر {open_ones()[-1]['probability']}%")
    else:
        print(render_scoreboard())
