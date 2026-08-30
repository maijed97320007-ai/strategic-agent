"""
لوحة المعلومات - تجمع ما تراكم في قاعدة المعرفة في صورة واحدة.

    رادار الفرص · رادار المنافسين · التنبؤات · التعارضات · التقارير

كل هذه البيانات كانت موجودة ومخزّنة لكنها غير مرئية إلا من سطر الأوامر.
نظام لا يُرى لا يُستخدم.

القراءة فقط: اللوحة لا تشغّل شيئاً ولا تستدعي نموذجاً، فهي فورية ومجانية
وتعمل بلا إنترنت.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

# المسار من memory لا سلسلة نسبية: التشغيل من مجلد آخر كان يفتح قاعدة
# فارغة بصمت فتبدو اللوحة خاوية.
try:
    from memory import DB_DEFAULT as DB
except ImportError:
    DB = "knowledge.db"


def _con(path: str = DB) -> sqlite3.Connection | None:
    if not Path(path).is_file():
        return None
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def snapshot(path: str = DB, out_dir: str = "output") -> dict:
    """كل ما تعرفه القاعدة، في قاموس واحد."""
    con = _con(path)
    if con is None:
        return {"empty": True}

    t = _tables(con)
    d: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def q(sql, args=()):
        try:
            return [dict(r) for r in con.execute(sql, args).fetchall()]
        except sqlite3.Error:
            return []

    # ── رادار الفرص ──
    if "opportunities" in t:
        # عبر recent() لا باستعلام موازٍ: هي التي تحلّ معرّفات الأدلة إلى
        # روابط وتُسقط التكرار، ونسخة ثانية من المنطق هنا ستتخلّف عنها.
        try:
            import opportunity_run
            d["opportunities"] = opportunity_run.recent(limit=25, db=path)
        except Exception:
            d["opportunities"] = q(
                "SELECT o.id,o.title,o.score,o.band,o.why,o.action,o.red_team,"
                "o.evidence,o.created_at, e.url,e.company,e.location,e.event_type"
                " FROM opportunities o LEFT JOIN events e ON e.id=o.event_id"
                " ORDER BY o.score DESC LIMIT 25")
        # الأشرطة تُحسب من القائمة بعد إزالة التكرار لا من الصفوف الخام:
        # جولتان على نفس الحدث تكتبان صفّين، فكانت الترويسة تقول 96 فرصة
        # بينما القائمة تحتها ثمانية عشر.
        bands: dict[str, int] = {}
        for r in d["opportunities"]:
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        d["opp_bands"] = bands

    # ── رادار المنافسين ──
    if "competitors" in t:
        d["competitors"] = q(
            "SELECT c.id,c.name,c.sector,c.threat,"
            " (SELECT COUNT(*) FROM competitor_moves m WHERE m.competitor_id=c.id) moves,"
            " (SELECT COUNT(*) FROM competitor_facts f"
            "  WHERE f.competitor_id=c.id AND f.valid_to IS NOT NULL) changes"
            " FROM competitors c ORDER BY c.threat DESC, moves DESC LIMIT 15")
        d["recent_changes"] = q(
            "SELECT c.name, f.attribute, f.value, f.valid_to"
            " FROM competitor_facts f JOIN competitors c ON c.id=f.competitor_id"
            " WHERE f.valid_to IS NOT NULL ORDER BY f.valid_to DESC LIMIT 12")

    # ── التنبؤات ──
    if "predictions" in t:
        d["predictions_open"] = q(
            "SELECT id,event,probability,deadline FROM predictions"
            " WHERE result IS NULL ORDER BY deadline LIMIT 15")
        d["predictions_due"] = q(
            "SELECT id,event,probability,deadline FROM predictions"
            " WHERE result IS NULL AND deadline<=? ORDER BY deadline",
            (date.today().isoformat(),))
        try:
            import predictions
            d["accuracy"] = predictions.accuracy(path)
        except Exception:
            d["accuracy"] = {}

    # ── المعرفة ──
    if "facts" in t:
        row = q("SELECT COUNT(*) n, SUM(source_url IS NOT NULL) sourced FROM facts")
        d["knowledge"] = {
            "facts": row[0]["n"] if row else 0,
            "sourced": row[0]["sourced"] if row else 0,
            "entities": (q("SELECT COUNT(*) n FROM entities"
                           " WHERE canonical_id IS NULL") or [{"n": 0}])[0]["n"],
        }
        try:
            import memory
            g = memory.ContextGraph(path)
            d["conflicts"] = g.conflicts()[:10]
            g.close()
        except Exception:
            d["conflicts"] = []

    con.close()

    # ── التقارير ──
    od = Path(out_dir)
    if od.is_dir():
        files = sorted((f for f in od.glob("*.md") if f.name != "final_report.md"),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:12]
        d["reports"] = [{"name": f.stem, "kb": round(f.stat().st_size / 1024),
                         "at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                         "pdf": f.with_suffix(".pdf").is_file(),
                         "path": str(f.resolve())} for f in files]
    return d


def render(d: dict) -> str:
    """عرض نصّي للطرفية."""
    if d.get("empty"):
        return "لا توجد قاعدة معرفة بعد - شغّل تشغيلة أولاً."

    out = ["", "=" * 60, "  لوحة المعلومات", "=" * 60]

    if opp := d.get("opportunities"):
        bands = d.get("opp_bands", {})
        out += ["", f"  رادار الفرص  ({', '.join(f'{k} {v}' for k, v in bands.items())})",
                "  " + "-" * 56]
        for o in opp[:8]:
            out.append(f"   [{o['score']:3d}] {o['band']:<11} {o['title'][:44]}")
            for s in (o.get("sources") or [])[:2]:
                w = f"{s['weight']:.2f}" if s.get("weight") is not None else " —  "
                out.append(f"          {w}  {s.get('site') or s['url'][:46]}")

    if comp := d.get("competitors"):
        out += ["", "  رادار المنافسين", "  " + "-" * 56]
        for c in comp[:6]:
            out.append(f"   تهديد {c['threat']:3d} · {c['name'][:32]:34s} "
                       f"{c['moves']} حركة · {c['changes']} تغيّر")
    if ch := d.get("recent_changes"):
        out.append("   آخر التغيّرات:")
        for r in ch[:4]:
            out.append(f"     {r['name'][:22]} · {r['attribute']}: {r['value'][:24]}")

    acc = d.get("accuracy") or {}
    if acc.get("resolved"):
        out += ["", "  التنبؤات", "  " + "-" * 56,
                f"   محسومة {acc['resolved']} · تحققت {acc['hit_rate']}% · "
                f"{acc['calibration']}"]
    if due := d.get("predictions_due"):
        out.append(f"   حان موعد {len(due)} تنبؤ - تحتاج حكمك")

    if k := d.get("knowledge"):
        pct = round(100 * (k["sourced"] or 0) / k["facts"]) if k["facts"] else 0
        out += ["", "  المعرفة", "  " + "-" * 56,
                f"   {k['facts']} حقيقة · {k['sourced']} موثّقة ({pct}%) · "
                f"{k['entities']} كيان"]
    if cf := d.get("conflicts"):
        out.append(f"   {len(cf)} تعارض مرصود")

    if rep := d.get("reports"):
        out += ["", "  آخر التقارير", "  " + "-" * 56]
        for r in rep[:6]:
            out.append(f"   {r['at']}  {r['kb']:>4}KB  {'PDF' if r['pdf'] else '   '}"
                       f"  {r['name'][:38]}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    # ترميز الطرفية على ويندوز cp1252، وكل سطر هنا عربي: بلا هذا ينهار
    # الأمر بـ UnicodeEncodeError قبل أن يطبع حرفاً واحداً.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    d = snapshot()
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(d))
