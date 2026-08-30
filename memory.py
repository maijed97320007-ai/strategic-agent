"""
طبقة ذاكرة معرفية مستوحاة من مخطط Semantica (github.com/semantica-agi/semantica)
لكن منفّذة على SQLite فقط - بلا Neo4j ولا Oxigraph ولا خوادم ولا تبعيات خارجية.

ما أُخذ من Semantica:
  1. المصدر على مستوى الحقيقة الواحدة (PROV-O): كل رقم يحمل رابط مصدره وثقته
  2. الزمن الثنائي (bi-temporal): متى كانت الحقيقة صحيحة + متى عرفناها
  3. دمج الكيانات (entity resolution): كشف التكرار بعتبة تشابه ثم الدمج مع حفظ الأصل
  4. حواف مُسمّاة بين الكيانات (typed edges) بدل جداول مسطحة
  5. القرارات ككائنات من الدرجة الأولى مع سلسلة سببية

ما لم يُؤخذ عمداً: Rete/Datalog/SPARQL و Allen interval algebra - ثقيلة بلا عائد هنا.

السرعة تأتي من فهرس FTS5 المدمج في SQLite: بحث نصي كامل بلا embeddings
وبلا استدعاء شبكة، أي نتائج فورية بتكلفة صفر.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

def _db_path() -> str:
    """
    مسار قاعدة المعرفة.

    كان `"knowledge.db"` نسبياً فيُحلّ حسب **مجلد التشغيل** لا مجلد
    البرنامج. داخل الـEXE هذا عطل صامت: تشغيله من مجلد آخر يُنشئ قاعدة
    فارغة جديدة، فتبدو الفرص والمعرفة والتنبؤات كلها ضائعة بلا رسالة خطأ.
    نثبّتها بجانب البرنامج، ويبقى KNOWLEDGE_DB لمن أراد مساراً آخر.
    """
    if env := os.getenv("KNOWLEDGE_DB"):
        return env
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return str(base / "knowledge.db")


DB_DEFAULT = _db_path()

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- الكيانات. canonical_id يشير للكيان الأصلي بعد الدمج (NULL = هو الأصل)
CREATE TABLE IF NOT EXISTS entities (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    norm_name    TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT 'Thing',
    canonical_id INTEGER REFERENCES entities(id),
    recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ent_norm ON entities(norm_name, type);
CREATE INDEX IF NOT EXISTS idx_ent_canon ON entities(canonical_id);

-- الحقائق. كل صف = ادعاء واحد بمصدره وثقته ونافذته الزمنية
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    field       TEXT NOT NULL,
    value       TEXT NOT NULL,
    unit        TEXT,
    source_url  TEXT,
    source_name TEXT,
    confidence  REAL NOT NULL DEFAULT 0.5,
    valid_from  TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL,
    topic       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fact_ent ON facts(entity_id, field);
CREATE INDEX IF NOT EXISTS idx_fact_topic ON facts(topic);

-- حواف مُسمّاة بين الكيانات
CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY,
    src_id      INTEGER NOT NULL REFERENCES entities(id),
    dst_id      INTEGER NOT NULL REFERENCES entities(id),
    rel         TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    source_url  TEXT,
    valid_from  TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edge_src ON edges(src_id, rel);
CREATE INDEX IF NOT EXISTS idx_edge_dst ON edges(dst_id, rel);

-- القرارات ككائنات من الدرجة الأولى
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    category    TEXT NOT NULL,
    scenario    TEXT NOT NULL,
    reasoning   TEXT,
    outcome     TEXT,
    confidence  REAL NOT NULL DEFAULT 0.5,
    metadata    TEXT,
    recorded_at TEXT NOT NULL
);

-- سلسلة سببية بين القرارات: CAUSED | INFLUENCED | PRECEDENT_FOR
CREATE TABLE IF NOT EXISTS causal (
    src_id INTEGER NOT NULL REFERENCES decisions(id),
    dst_id INTEGER NOT NULL REFERENCES decisions(id),
    rel    TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, rel)
);

-- سجل الدمج: يحفظ ما دُمج في ماذا حتى لا تُفقد المعلومة الأصلية
CREATE TABLE IF NOT EXISTS merge_log (
    merged_id   INTEGER NOT NULL,
    into_id     INTEGER NOT NULL,
    similarity  REAL,
    strategy    TEXT,
    recorded_at TEXT NOT NULL
);

-- فهرس البحث النصي الكامل: مصدر السرعة.
-- يخزّن النص *بعد التطبيع* لأن الاستعلام يُطبَّع أيضاً - بدون ذلك
-- لا يطابق 'تحلية' المخزّن استعلامَ 'تحليه' المطبَّع. القيم الأصلية
-- تُقرأ من جدول facts عبر الـ JOIN.
CREATE VIRTUAL TABLE IF NOT EXISTS fact_fts USING fts5(
    entity, field, value, source_name, topic,
    content=''
);
"""

_AR_DIACRITICS = re.compile(r"[ً-ْٰـ]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    """
    توحيد النص للمقارنة: يزيل التشكيل والهمزات المختلفة وأل التعريف
    حتى يُعتبر 'الشركة العُمانية' و'شركه عمانيه' نفس الكيان.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).strip().lower()
    t = _AR_DIACRITICS.sub("", t)
    t = t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"^(ال|el-|al-)", "", t)
    t = re.sub(r"[^\w؀-ۿ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


@dataclass
class Fact:
    """ادعاء واحد بمصدره - الوحدة الذرية للذاكرة."""
    entity: str
    field: str
    value: str
    unit: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    confidence: float = 0.5
    valid_from: str | None = None
    valid_until: str | None = None
    entity_type: str = "Thing"
    topic: str | None = None


class ContextGraph:
    """
    رسم المعرفة. الأسماء متعمَّد تطابقها مع Semantica ليسهل الانتقال لاحقاً
    لو احتجت النسخة الكاملة بـ Neo4j.
    """

    def __init__(self, path: str = DB_DEFAULT, merge_threshold: float = 0.85):
        self.path = path
        self.merge_threshold = merge_threshold
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # ---------- الكيانات ودمجها ----------
    def add_entity(self, name: str, type: str = "Thing", merge: bool = True) -> int:
        norm = normalize(name)
        if not norm:
            raise ValueError("اسم الكيان فارغ")

        row = self.db.execute(
            "SELECT id FROM entities WHERE norm_name=? AND type=? AND canonical_id IS NULL",
            (norm, type),
        ).fetchone()
        if row:
            return row["id"]

        if merge:
            hit = self._find_similar_entity(norm, type)
            if hit:
                return hit

        cur = self.db.execute(
            "INSERT INTO entities(name, norm_name, type, recorded_at) VALUES(?,?,?,?)",
            (name.strip(), norm, type, _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def _find_similar_entity(self, norm: str, type: str) -> int | None:
        """كشف التكرار بعتبة تشابه - مكافئ DuplicateDetector."""
        head = norm.split(" ")[0][:4]
        cands = self.db.execute(
            "SELECT id, norm_name FROM entities "
            "WHERE type=? AND canonical_id IS NULL AND norm_name LIKE ? LIMIT 200",
            (type, f"%{head}%"),
        ).fetchall()
        best, best_score = None, 0.0
        for c in cands:
            s = SequenceMatcher(None, norm, c["norm_name"]).ratio()
            if s > best_score:
                best, best_score = c["id"], s
        return best if best_score >= self.merge_threshold else None

    def resolve(self, entity_id: int) -> int:
        """يتبع سلسلة الدمج للوصول للكيان الأصلي."""
        seen = set()
        while entity_id not in seen:
            seen.add(entity_id)
            row = self.db.execute(
                "SELECT canonical_id FROM entities WHERE id=?", (entity_id,)
            ).fetchone()
            if not row or row["canonical_id"] is None:
                return entity_id
            entity_id = row["canonical_id"]
        return entity_id

    def merge_entities(self, merged_id: int, into_id: int, sim: float = 1.0,
                       strategy: str = "keep_most_complete") -> None:
        """دمج مع حفظ الأصل - مكافئ EntityMerger(preserve_provenance=True)."""
        if merged_id == into_id:
            return
        self.db.execute("UPDATE entities SET canonical_id=? WHERE id=?", (into_id, merged_id))
        self.db.execute("UPDATE facts SET entity_id=? WHERE entity_id=?", (into_id, merged_id))
        self.db.execute("UPDATE edges SET src_id=? WHERE src_id=?", (into_id, merged_id))
        self.db.execute("UPDATE edges SET dst_id=? WHERE dst_id=?", (into_id, merged_id))
        self.db.execute(
            "INSERT INTO merge_log(merged_id, into_id, similarity, strategy, recorded_at) "
            "VALUES(?,?,?,?,?)", (merged_id, into_id, sim, strategy, _now()),
        )
        self.db.commit()

    def find_duplicates(self, threshold: float | None = None) -> list[tuple[int, int, float]]:
        th = threshold if threshold is not None else self.merge_threshold
        rows = self.db.execute(
            "SELECT id, norm_name, type FROM entities WHERE canonical_id IS NULL"
        ).fetchall()
        out = []
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[i]["type"] != rows[j]["type"]:
                    continue
                s = SequenceMatcher(None, rows[i]["norm_name"], rows[j]["norm_name"]).ratio()
                if s >= th:
                    out.append((rows[i]["id"], rows[j]["id"], round(s, 3)))
        return out

    # ---------- الحقائق ----------
    def add_fact(self, f: Fact) -> int:
        eid = self.resolve(self.add_entity(f.entity, f.entity_type))
        cur = self.db.execute(
            "INSERT INTO facts(entity_id, field, value, unit, source_url, source_name,"
            " confidence, valid_from, valid_until, recorded_at, topic)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (eid, f.field, f.value, f.unit, f.source_url, f.source_name,
             f.confidence, f.valid_from, f.valid_until, _now(), f.topic),
        )
        fid = cur.lastrowid
        self.db.execute(
            "INSERT INTO fact_fts(rowid, entity, field, value, source_name, topic)"
            " VALUES(?,?,?,?,?,?)",
            (fid, normalize(f.entity), normalize(f.field), normalize(f.value),
             normalize(f.source_name or ""), normalize(f.topic or "")),
        )
        self.db.commit()
        return fid

    def add_facts(self, facts: Iterable[Fact]) -> int:
        return sum(1 for f in facts if self.add_fact(f))

    # ---------- الحواف ----------
    def add_edge(self, src: str, rel: str, dst: str, weight: float = 1.0,
                 source_url: str | None = None, valid_from: str | None = None,
                 valid_until: str | None = None, src_type: str = "Thing",
                 dst_type: str = "Thing") -> int:
        s = self.resolve(self.add_entity(src, src_type))
        d = self.resolve(self.add_entity(dst, dst_type))
        cur = self.db.execute(
            "INSERT INTO edges(src_id, dst_id, rel, weight, source_url,"
            " valid_from, valid_until, recorded_at) VALUES(?,?,?,?,?,?,?,?)",
            (s, d, rel, weight, source_url, valid_from, valid_until, _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def neighbors(self, name: str, rel: str | None = None, depth: int = 1) -> list[dict]:
        start = self.resolve(self.add_entity(name, merge=True))
        seen, frontier, out = {start}, [start], []
        for _ in range(max(1, depth)):
            if not frontier:
                break
            marks = ",".join("?" * len(frontier))
            q = (f"SELECT e.rel, e.weight, e.src_id, e.dst_id, a.name AS src, b.name AS dst "
                 f"FROM edges e JOIN entities a ON a.id=e.src_id JOIN entities b ON b.id=e.dst_id "
                 f"WHERE (e.src_id IN ({marks}) OR e.dst_id IN ({marks}))")
            args = list(frontier) + list(frontier)
            if rel:
                q += " AND e.rel=?"
                args.append(rel)
            nxt = []
            for r in self.db.execute(q, args).fetchall():
                edge = {"src": r["src"], "rel": r["rel"], "dst": r["dst"], "weight": r["weight"]}
                if edge not in out:      # الحافة تُلتقط مرتين (كمصدر وكوجهة) عند التوسّع
                    out.append(edge)
                for side in (r["src_id"], r["dst_id"]):
                    if side not in seen:
                        seen.add(side)
                        nxt.append(side)
            frontier = nxt
        return out

    # ---------- القرارات ----------
    def record_decision(self, category: str, scenario: str, reasoning: str = "",
                        outcome: str = "", confidence: float = 0.5,
                        metadata: dict | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO decisions(category, scenario, reasoning, outcome, confidence,"
            " metadata, recorded_at) VALUES(?,?,?,?,?,?,?)",
            (category, scenario, reasoning, outcome, confidence,
             json.dumps(metadata or {}, ensure_ascii=False), _now()),
        )
        self.db.commit()
        return cur.lastrowid

    def add_causal_relationship(self, src_id: int, dst_id: int,
                                relationship_type: str = "INFLUENCED") -> None:
        if relationship_type not in ("CAUSED", "INFLUENCED", "PRECEDENT_FOR"):
            raise ValueError("نوع علاقة غير مدعوم")
        self.db.execute("INSERT OR IGNORE INTO causal VALUES(?,?,?)",
                        (src_id, dst_id, relationship_type))
        self.db.commit()

    def trace_decision_chain(self, decision_id: int) -> list[dict]:
        out, stack, seen = [], [decision_id], set()
        while stack:
            did = stack.pop()
            if did in seen:
                continue
            seen.add(did)
            row = self.db.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
            if row:
                out.append(dict(row))
            for r in self.db.execute("SELECT src_id FROM causal WHERE dst_id=?", (did,)):
                stack.append(r["src_id"])
        return out

    # ---------- الاسترجاع السريع ----------
    def search(self, query: str, limit: int = 20, topic: str | None = None) -> list[dict]:
        """بحث نصي كامل عبر FTS5 - فوري وبلا شبكة."""
        terms = [t for t in re.split(r"\s+", normalize(query)) if len(t) > 1]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        sql = ("SELECT f.id, e.name AS entity, f.field, f.value, f.unit, f.source_url,"
               " f.source_name, f.confidence, f.valid_from, f.valid_until, f.topic,"
               " bm25(fact_fts) AS score"
               " FROM fact_fts JOIN facts f ON f.id = fact_fts.rowid"
               " JOIN entities e ON e.id = f.entity_id"
               " WHERE fact_fts MATCH ?")
        args: list[Any] = [match]
        if topic:
            sql += " AND f.topic = ?"
            args.append(topic)
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        try:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]
        except sqlite3.OperationalError:
            return []

    def get_facts(self, entity: str, at: str | None = None) -> list[dict]:
        """كل حقائق كيان، مع إمكانية السفر الزمني - مكافئ state_at()."""
        eid = self.resolve(self.add_entity(entity, merge=True))
        rows = self.db.execute(
            "SELECT f.*, e.name AS entity FROM facts f JOIN entities e ON e.id=f.entity_id"
            " WHERE f.entity_id=? ORDER BY f.confidence DESC", (eid,)
        ).fetchall()
        out = [dict(r) for r in rows]
        if at:
            out = [r for r in out
                   if (not r["valid_from"] or r["valid_from"] <= at)
                   and (not r["valid_until"] or r["valid_until"] >= at)]
        return out

    def state_at(self, at: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT f.*, e.name AS entity FROM facts f JOIN entities e ON e.id=f.entity_id"
            " WHERE (f.valid_from IS NULL OR f.valid_from<=?)"
            "   AND (f.valid_until IS NULL OR f.valid_until>=?)"
            "   AND f.recorded_at<=?", (at, at, at + "T23:59:59+00:00"),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_lineage(self, entity: str) -> dict:
        """سلسلة مصادر كيان - مكافئ ProvenanceManager.get_lineage()."""
        eid = self.resolve(self.add_entity(entity, merge=True))
        facts = self.db.execute(
            "SELECT field, value, source_url, source_name, confidence, recorded_at"
            " FROM facts WHERE entity_id=? ORDER BY recorded_at", (eid,)
        ).fetchall()
        merges = self.db.execute(
            "SELECT * FROM merge_log WHERE into_id=?", (eid,)
        ).fetchall()
        sources = sorted({f["source_url"] for f in facts if f["source_url"]})
        return {
            "entity_id": eid,
            "facts": [dict(f) for f in facts],
            "merged_from": [dict(m) for m in merges],
            "distinct_sources": sources,
            "unsourced_facts": sum(1 for f in facts if not f["source_url"]),
        }

    def sources(self, topic: str | None = None) -> list[dict]:
        """
        كل المصادر المخزّنة، مع عدد الحقائق المستندة لكل مصدر.

        هذه هي بوابة استرجاع المصادر بعد حذفها من نص التقرير المعروض.
        """
        sql = ("SELECT f.source_url AS url, f.source_name AS site, f.topic,"
               " COUNT(*) AS n, AVG(f.confidence) AS conf"
               " FROM facts f WHERE f.source_url IS NOT NULL")
        args: list[Any] = []
        if topic:
            sql += " AND f.topic LIKE ?"
            args.append(f"%{topic}%")
        sql += " GROUP BY f.source_url ORDER BY n DESC"
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def conflicts(self) -> list[dict]:
        """حقائق متضاربة: نفس الكيان ونفس الحقل بقيم مختلفة."""
        rows = self.db.execute(
            "SELECT e.name AS entity, f.field, COUNT(DISTINCT f.value) AS n,"
            " GROUP_CONCAT(DISTINCT f.value) AS values_"
            " FROM facts f JOIN entities e ON e.id=f.entity_id"
            " GROUP BY f.entity_id, f.field HAVING n > 1"
        ).fetchall()
        return [{"entity": r["entity"], "field": r["field"],
                 "values": (r["values_"] or "").split(","),
                 "severity": "high" if r["n"] > 2 else "medium"} for r in rows]

    def stats(self) -> dict:
        one = lambda q: self.db.execute(q).fetchone()[0]
        return {
            "entities": one("SELECT COUNT(*) FROM entities WHERE canonical_id IS NULL"),
            "merged": one("SELECT COUNT(*) FROM entities WHERE canonical_id IS NOT NULL"),
            "facts": one("SELECT COUNT(*) FROM facts"),
            "sourced_facts": one("SELECT COUNT(*) FROM facts WHERE source_url IS NOT NULL"),
            "edges": one("SELECT COUNT(*) FROM edges"),
            "decisions": one("SELECT COUNT(*) FROM decisions"),
            "conflicts": len(self.conflicts()),
        }

    def close(self):
        self.db.close()


# ======================
# استخراج الحقائق من تقارير الوكيل
# ======================
_NUM_RE = re.compile(
    r"(?P<value>\d[\d,\.]*)\s*"
    r"(?P<unit>%|م³/يوم|م³|كم²|كم|MW[phc]?|MWh|GW|kWh/m²/يوم|kWh|ميجاوات|مليون|مليار|ألف|\$|دولار|ريال)?"
)
_URL_RE = re.compile(r"https?://[^\s\)\]\>،]+")


def extract_facts(markdown: str, topic: str, default_entity: str | None = None) -> list[Fact]:
    """
    يحوّل تقرير الوكيل إلى حقائق ذرية بمصادرها.

    القاعدة: الرقم يُنسب لآخر رابط ظهر قبله في نفس القسم. إن لم يوجد رابط،
    تُسجَّل الحقيقة بثقة منخفضة و source_url فارغ - فتظهر لاحقاً في
    unsourced_facts بدل أن تختفي وكأنها موثّقة.
    """
    facts: list[Fact] = []
    section = default_entity or topic
    last_url: str | None = None

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            section = re.sub(r"^#+\s*", "", stripped)
            section = re.sub(r"[💡✅❌⚠️*_`]", "", section).strip() or section
            last_url = None
            continue

        urls = _URL_RE.findall(stripped)
        if urls:
            last_url = urls[0]

        for m in _NUM_RE.finditer(stripped):
            val, unit = m.group("value"), m.group("unit")
            if not unit or len(val.replace(",", "").replace(".", "")) < 2:
                continue
            ctx = stripped[max(0, m.start() - 60):m.start()].strip()
            label = re.sub(r"[|*`\-–—:]+", " ", ctx).strip()[-45:] or "قيمة"
            facts.append(Fact(
                entity=section[:80], field=label, value=val, unit=unit,
                source_url=last_url, source_name=(last_url or "").split("/")[2] if last_url else None,
                confidence=0.75 if last_url else 0.25,
                topic=topic, entity_type="Topic",
            ))
    return facts


def ingest_report(path_or_text: str, topic: str, db: str = DB_DEFAULT) -> dict:
    """يبتلع تقريراً ويعيد إحصاءات ما خُزّن."""
    try:
        with open(path_or_text, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError):
        text = path_or_text

    g = ContextGraph(db)
    facts = extract_facts(text, topic)
    n = g.add_facts(facts)
    did = g.record_decision(
        category="research_run", scenario=topic,
        reasoning=f"تشغيلة وكيل أنتجت {len(text)} حرف",
        outcome=f"خُزّنت {n} حقيقة", confidence=0.8,
        metadata={"chars": len(text), "facts": n},
    )
    st = g.stats()
    g.close()
    return {"stored": n, "decision_id": did, **st}


if __name__ == "__main__":
    import sys
    g = ContextGraph()
    if len(sys.argv) > 2 and sys.argv[1] == "ingest":
        print(json.dumps(ingest_report(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "عام"),
                         ensure_ascii=False, indent=2))
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        for r in g.search(" ".join(sys.argv[2:])):
            src = r["source_url"] or "بلا مصدر"
            print(f"[{r['confidence']:.2f}] {r['entity']} · {r['field']} = {r['value']} {r['unit'] or ''}\n      {src}")
    elif len(sys.argv) > 1 and sys.argv[1] == "sources":
        rows = g.sources(" ".join(sys.argv[2:]) or None)
        if not rows:
            print("لا توجد مصادر مخزّنة لهذا الموضوع.")
        for i, r in enumerate(rows, 1):
            print(f"{i:2}. [{r['n']:3} حقيقة] {r['site'] or ''}\n    {r['url']}")
        print(f"\nالمجموع: {len(rows)} مصدر")
    else:
        print(json.dumps(g.stats(), ensure_ascii=False, indent=2))
