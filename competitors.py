"""
ملفات المنافسين وسجلّهم التاريخي.

("تاريخ" هنا بمعنى السجل المتراكم عبر الزمن، لا تاريخ اليوم.)

القاعدة الحاكمة: **لا تُستبدل قيمة قديمة أبداً**. تغيّر السعر من 100 إلى 85
ليس تصحيحاً بل حدثاً - وهو بالضبط ما يكشف الأنماط:

    OLD 100 ريال → NEW 85 ريال  ·  تغيّر 2026-08-30  ·  المصدر S12

بلا سجل متراكم لا يمكن كشف نمط تسعير ولا نمط توسّع ولا سلوك مناقصات، ولا
يمكن بناء توأم رقمي يتنبأ بردّ الفعل.

الحقول تُخزَّن كأزواج (سمة، قيمة) لا كأعمدة ثابتة: المنافسون يختلفون،
وإضافة سمة جديدة يجب ألا تتطلب تعديل مخطط.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

DB = "knowledge.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitors (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    norm_name  TEXT NOT NULL,
    sector     TEXT,
    country    TEXT,
    threat     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- كل صف حقيقة عن منافس في لحظة. valid_to = NULL يعني أنها الحالية.
CREATE TABLE IF NOT EXISTS competitor_facts (
    id            INTEGER PRIMARY KEY,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id),
    attribute     TEXT NOT NULL,
    value         TEXT NOT NULL,
    source_id     TEXT,
    source_url    TEXT,
    confidence    REAL NOT NULL DEFAULT 0.6,
    valid_from    TEXT NOT NULL,
    valid_to      TEXT,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cf ON competitor_facts(competitor_id, attribute);

-- سلوك مرصود: مناقصة فازها، سعر خفّضه، سوق دخله
CREATE TABLE IF NOT EXISTS competitor_moves (
    id            INTEGER PRIMARY KEY,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id),
    move_type     TEXT NOT NULL,
    detail        TEXT,
    magnitude     REAL,
    happened_at   TEXT,
    source_id     TEXT,
    recorded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cm ON competitor_moves(competitor_id, move_type);
"""

MOVE_TYPES = ["خفض سعر", "رفع سعر", "دخول سوق", "خروج من سوق", "فوز بمناقصة",
              "خسارة مناقصة", "إطلاق منتج", "شراكة", "توسّع", "توظيف مكثّف"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


@dataclass
class Change:
    attribute: str
    old: str
    new: str
    changed_at: str
    source: str = ""


def upsert(name: str, sector: str = "", country: str = "",
           path: str = DB) -> int:
    """ينشئ منافساً أو يعيد معرّفه. يستخدم تطبيع memory لدمج التهجئات."""
    from memory import normalize

    con = db(path)
    norm = normalize(name)
    row = con.execute("SELECT id FROM competitors WHERE norm_name=?",
                      (norm,)).fetchone()
    if row:
        cid = row["id"]
    else:
        cid = con.execute(
            "INSERT INTO competitors(name,norm_name,sector,country,created_at)"
            " VALUES(?,?,?,?,?)",
            (name.strip(), norm, sector, country, _now())).lastrowid
        con.commit()
    con.close()
    return cid


def set_fact(cid: int, attribute: str, value: str, source_id: str = "",
             source_url: str = "", confidence: float = 0.6,
             path: str = DB) -> Change | None:
    """
    يسجّل قيمة سمة. يعيد Change إن كانت تغيّرت عن السابقة، وإلا None.

    لا يحذف القديم: يغلق نافذته بـvalid_to ويفتح صفاً جديداً.
    """
    con = db(path)
    cur = con.execute(
        "SELECT id, value FROM competitor_facts"
        " WHERE competitor_id=? AND attribute=? AND valid_to IS NULL",
        (cid, attribute)).fetchone()

    change = None
    if cur:
        if str(cur["value"]).strip() == str(value).strip():
            con.close()
            return None                      # لا جديد
        con.execute("UPDATE competitor_facts SET valid_to=? WHERE id=?",
                    (_now(), cur["id"]))
        change = Change(attribute=attribute, old=cur["value"], new=value,
                        changed_at=_now(), source=source_id)

    con.execute(
        "INSERT INTO competitor_facts(competitor_id,attribute,value,source_id,"
        "source_url,confidence,valid_from,recorded_at) VALUES(?,?,?,?,?,?,?,?)",
        (cid, attribute, value, source_id, source_url, confidence, _now(), _now()))
    con.commit()
    con.close()
    return change


def add_move(cid: int, move_type: str, detail: str = "", magnitude: float | None = None,
             happened_at: str = "", source_id: str = "", path: str = DB) -> int:
    con = db(path)
    mid = con.execute(
        "INSERT INTO competitor_moves(competitor_id,move_type,detail,magnitude,"
        "happened_at,source_id,recorded_at) VALUES(?,?,?,?,?,?,?)",
        (cid, move_type, detail, magnitude, happened_at or _now(),
         source_id, _now())).lastrowid
    con.commit()
    con.close()
    return mid


def profile(name_or_id: str | int, path: str = DB) -> dict:
    """الملف الكامل: السمات الحالية + السجل السابق + الحركات."""
    from memory import normalize

    con = db(path)
    if isinstance(name_or_id, int):
        row = con.execute("SELECT * FROM competitors WHERE id=?",
                          (name_or_id,)).fetchone()
    else:
        row = con.execute("SELECT * FROM competitors WHERE norm_name=?",
                          (normalize(name_or_id),)).fetchone()
    if not row:
        con.close()
        return {}

    cid = row["id"]
    current = con.execute(
        "SELECT attribute,value,source_url,confidence,valid_from"
        " FROM competitor_facts WHERE competitor_id=? AND valid_to IS NULL",
        (cid,)).fetchall()
    history = con.execute(
        "SELECT attribute,value,valid_from,valid_to,source_id"
        " FROM competitor_facts WHERE competitor_id=? AND valid_to IS NOT NULL"
        " ORDER BY valid_to DESC", (cid,)).fetchall()
    moves = con.execute(
        "SELECT move_type,detail,magnitude,happened_at,source_id"
        " FROM competitor_moves WHERE competitor_id=?"
        " ORDER BY happened_at DESC LIMIT 40", (cid,)).fetchall()
    con.close()

    return {
        "id": cid, "name": row["name"], "sector": row["sector"],
        "country": row["country"], "threat": row["threat"],
        "current": {r["attribute"]: r["value"] for r in current},
        "sources": {r["attribute"]: r["source_url"] for r in current},
        "history": [dict(r) for r in history],
        "moves": [dict(r) for r in moves],
    }


def patterns(cid: int, path: str = DB) -> dict:
    """
    أنماط سلوكية من السجل المتراكم - وقود التوأم الرقمي.

    نحسبها في الكود لا بالنموذج: العدّ والمتوسط والتكرار حساب لا استدلال.
    """
    con = db(path)
    moves = con.execute(
        "SELECT move_type, COUNT(*) n, AVG(COALESCE(magnitude,0)) avg_mag"
        " FROM competitor_moves WHERE competitor_id=? GROUP BY move_type",
        (cid,)).fetchall()
    changes = con.execute(
        "SELECT attribute, COUNT(*) n FROM competitor_facts"
        " WHERE competitor_id=? AND valid_to IS NOT NULL GROUP BY attribute",
        (cid,)).fetchall()
    con.close()

    by_move = {r["move_type"]: {"count": r["n"], "avg": round(r["avg_mag"], 1)}
               for r in moves}
    total = sum(v["count"] for v in by_move.values()) or 1
    return {
        "moves": by_move,
        "move_share": {k: round(100 * v["count"] / total)
                       for k, v in by_move.items()},
        "volatile_attributes": {r["attribute"]: r["n"] for r in changes},
        "total_moves": total if by_move else 0,
    }


def all_competitors(path: str = DB) -> list[dict]:
    con = db(path)
    rows = con.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM competitor_moves m"
        "             WHERE m.competitor_id=c.id) AS moves"
        " FROM competitors c ORDER BY c.threat DESC, moves DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def set_threat(cid: int, threat: int, path: str = DB) -> None:
    con = db(path)
    con.execute("UPDATE competitors SET threat=? WHERE id=?",
                (max(0, min(100, threat)), cid))
    con.commit()
    con.close()


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        rows = all_competitors()
        for r in rows:
            print(f"  [{r['threat']:3d}] {r['name'][:40]:42s} {r['moves']} حركة")
        print(f"\nالمجموع: {len(rows)} منافس")
    else:
        p = profile(" ".join(args))
        if not p:
            print("غير موجود")
        else:
            print(json.dumps(p, ensure_ascii=False, indent=2)[:2000])
            print("\nالأنماط:", json.dumps(patterns(p["id"]), ensure_ascii=False))
