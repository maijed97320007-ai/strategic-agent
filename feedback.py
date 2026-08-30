"""
حلقة التغذية الراجعة - تصحيحك يصير معرفة دائمة.

    تصحيح ميداني منك
        ↓
    يُخزَّن موسوماً بمصدره (أنت)
        ↓
    يُرقّى إلى ملف المهارة
        ↓
    كل تشغيلة قادمة تحمله

المشكلة التي تحلّها: قلتَ «الإنتاجية غير مجدية ميدانياً» فتغيّر التقرير
جذرياً - لكن **أنا** من عدّل ملف المهارة يدوياً. بلا هذه الحلقة يضيع كل
تصحيح بمجرد انتهاء المحادثة، ويكرّر النظام الخطأ نفسه إلى الأبد.

تصحيح الخبير أثمن من نتيجة بحث: البحث يعطي ما يتصدّر جوجل، والخبير يعطي
ما رآه في الميدان. لذلك يُرقَّى التصحيح إلى **رأس ملف المهارة** ليُقرأ
قبل أي شيء آخر.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from memory import DB_DEFAULT as DB
except ImportError:
    DB = "knowledge.db"

KINDS = {
    "wrong": "خطأ فني",
    "impractical": "غير عملي ميدانياً",
    "outdated": "معلومة قديمة",
    "missing": "نقص مهم",
    "good": "صحيح ومفيد",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY,
    topic      TEXT,
    subject    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    correction TEXT NOT NULL,
    skill      TEXT,
    promoted   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fb ON feedback(promoted, skill);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


def add(subject: str, correction: str, kind: str = "impractical",
        topic: str = "", skill: str = "", path: str = DB) -> int:
    """
    يسجّل تصحيحاً.

    subject    : ما تصحّحه (فكرة، مؤشر، رقم، ادعاء)
    correction : لماذا هو خطأ وما الصواب - هذا ما سيُقرأ لاحقاً
    skill      : المهارة التي يخصّها. فارغ = يُخمَّن من الموضوع
    """
    if kind not in KINDS:
        kind = "impractical"
    if not skill:
        skill = guess_skill(f"{subject} {topic}")

    con = _db(path)
    fid = con.execute(
        "INSERT INTO feedback(topic,subject,kind,correction,skill,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (topic, subject.strip(), kind, correction.strip(), skill, _now())).lastrowid
    con.commit()
    con.close()
    return fid


def guess_skill(text: str) -> str:
    """يطابق النص مع المهارات الموجودة ويعيد اسم مجلد أقواها."""
    try:
        import skills
        hits = skills.select(text, threshold=0.15, limit=1)
        if hits:
            return hits[0].path.parent.name
    except Exception:
        pass
    return ""


def pending(skill: str | None = None, path: str = DB) -> list[dict]:
    con = _db(path)
    sql = "SELECT * FROM feedback WHERE promoted=0"
    args: list = []
    if skill:
        sql += " AND skill=?"
        args.append(skill)
    rows = con.execute(sql + " ORDER BY created_at", args).fetchall()
    con.close()
    return [dict(r) for r in rows]


def all_items(path: str = DB) -> list[dict]:
    con = _db(path)
    rows = con.execute("SELECT * FROM feedback ORDER BY created_at DESC").fetchall()
    con.close()
    return [dict(r) for r in rows]


# ======================
# الترقية إلى ملف المهارة
# ======================
MARK_START = "<!-- feedback:start -->"
MARK_END = "<!-- feedback:end -->"

HEADER = """## تصحيحات ميدانية من الخبير

هذا القسم مبنيّ من تصحيحات صاحب الخبرة على تقارير سابقة. **يعلو على أي
معرفة عامة أدناه** - إن تعارض شيء معه فالصواب ما هنا.
"""


def _skill_path(skill: str) -> Path:
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent
    return base / "skills" / skill / "SKILL.md"


def _render_block(items: list[dict]) -> str:
    lines = [MARK_START, "", HEADER]
    for it in items:
        lines.append(f"- **{it['subject']}** — {KINDS.get(it['kind'], it['kind'])}  \n"
                     f"  {it['correction']}")
    lines += ["", MARK_END]
    return "\n".join(lines)


def promote(skill: str | None = None, path: str = DB) -> dict:
    """
    يرقّي التصحيحات المعلّقة إلى ملفات المهارات.

    يُدرج القسم **بعد رأس YAML مباشرة** لا في النهاية: المهارة قد تُقتطع
    عند حدّ السياق، والتصحيحات يجب ألا تكون أول ما يسقط.
    """
    items = pending(skill, path)
    if not items:
        return {"promoted": 0, "skills": []}

    by_skill: dict[str, list[dict]] = {}
    for it in items:
        if it["skill"]:
            by_skill.setdefault(it["skill"], []).append(it)

    touched, ids = [], []
    for sk, group in by_skill.items():
        p = _skill_path(sk)
        if not p.is_file():
            continue

        text = p.read_text(encoding="utf-8")

        # التصحيحات السابقة تُدمج مع الجديدة لا تُستبدل
        con = _db(path)
        prior = [dict(r) for r in con.execute(
            "SELECT * FROM feedback WHERE skill=? AND promoted=1"
            " ORDER BY created_at", (sk,)).fetchall()]
        con.close()
        block = _render_block(prior + group)

        if MARK_START in text and MARK_END in text:
            text = re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END),
                          block, text, flags=re.S)
        else:
            m = re.match(r"\A(---\s*\n.*?\n---\s*\n)(.*)\Z", text, re.S)
            if m:
                text = m.group(1) + "\n" + block + "\n\n" + m.group(2).lstrip()
            else:
                text = block + "\n\n" + text

        p.write_text(text, encoding="utf-8")
        touched.append(sk)
        ids += [it["id"] for it in group]

    if ids:
        con = _db(path)
        con.executemany("UPDATE feedback SET promoted=1 WHERE id=?",
                        [(i,) for i in ids])
        con.commit()
        con.close()

    orphans = [it for it in items if not it["skill"]]
    return {"promoted": len(ids), "skills": touched,
            "orphans": [{"id": o["id"], "subject": o["subject"]} for o in orphans]}


def stats(path: str = DB) -> dict:
    con = _db(path)
    rows = con.execute(
        "SELECT kind, COUNT(*) n, SUM(promoted) p FROM feedback GROUP BY kind"
    ).fetchall()
    con.close()
    return {r["kind"]: {"total": r["n"], "promoted": r["p"] or 0} for r in rows}


if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "add" and len(args) >= 3:
        kind = args[3] if len(args) > 3 and args[3] in KINDS else "impractical"
        fid = add(args[1], args[2], kind)
        it = [x for x in all_items() if x["id"] == fid][0]
        print(f"سُجّل #{fid} · المهارة: {it['skill'] or '(لم تُطابق - رقّه يدوياً)'}")

    elif args and args[0] == "promote":
        r = promote(args[1] if len(args) > 1 else None)
        print(f"رُقّي {r['promoted']} تصحيح إلى: {'، '.join(r['skills']) or 'لا شيء'}")
        for o in r.get("orphans", []):
            print(f"  ⚠ بلا مهارة: #{o['id']} {o['subject'][:50]}")

    elif args and args[0] == "list":
        for it in all_items():
            mark = "✓" if it["promoted"] else "·"
            print(f"  {mark} [{KINDS.get(it['kind'], '')[:14]:16s}] {it['subject'][:38]:40s}"
                  f" → {it['skill'] or '—'}")
        print(f"\n{json.dumps(stats(), ensure_ascii=False)}")

    else:
        print("الاستخدام:")
        print('  python feedback.py add "<ما تصحّحه>" "<لماذا وما الصواب>" [النوع]')
        print("  python feedback.py promote     # ترقية التصحيحات للمهارات")
        print("  python feedback.py list")
        print(f"\n  الأنواع: {'، '.join(KINDS)}")
