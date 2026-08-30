"""
ذاكرة مؤقتة لنتائج البحث.

كل تشغيلة تُطلق 4 استعلامات، وكاشف الفرص 6، وكثير منها يتكرر بين
التشغيلات ("تحلية المياه عُمان مناقصة" يُسأل في كل دورة كشف). البحث
المكرر يستهلك حصة Serper المجانية (2500 طلب) ويضيف ثوانيَ بلا فائدة.

المفتاح هو الاستعلام **بعد التطبيع العربي** - فـ"تحلية المياه" و"تحليه
المياة" استعلام واحد لا اثنان.

الصلاحية قصيرة عمداً (6 ساعات): المناقصات والأخبار تتغيّر، وذاكرة
مؤقتة قديمة أسوأ من لا ذاكرة لأنها تخفي الجديد.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "6"))
ENABLED = os.getenv("CACHE", "1").strip().lower() not in ("0", "false", "no")
DB = "knowledge.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_cache (
    key       TEXT PRIMARY KEY,
    query     TEXT NOT NULL,
    payload   TEXT NOT NULL,
    hits      INTEGER NOT NULL DEFAULT 0,
    stored_at REAL NOT NULL
);
"""


def _db(path: str = DB) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    con.commit()
    return con


def _key(query: str) -> str:
    try:
        from memory import normalize
        q = normalize(query)
    except Exception:
        q = query.strip().lower()
    return hashlib.sha256(q.encode("utf-8")).hexdigest()[:20]


def get(query: str, path: str = DB, ttl_hours: float | None = None):
    """
    يعيد النتيجة المحفوظة إن كانت طازجة، وإلا None.

    `ttl_hours` يتجاوز السقف العام: نتيجة بحث تتقادم في ساعات، لكن ترجمة
    موضوع إلى استعلام إنجليزي لا تتقادم أبداً - وإعادتها كلّفت 13.8 ثانية
    مقيسة على نموذج مجاني، وهي أبطأ خطوة في التشغيلة كلها.
    """
    if not ENABLED:
        return None
    ttl = TTL_HOURS if ttl_hours is None else ttl_hours
    try:
        con = _db(path)
        row = con.execute("SELECT payload, stored_at FROM search_cache WHERE key=?",
                          (_key(query),)).fetchone()
        if not row:
            con.close()
            return None
        if (time.time() - row["stored_at"]) / 3600 > ttl:
            con.execute("DELETE FROM search_cache WHERE key=?", (_key(query),))
            con.commit()
            con.close()
            return None
        con.execute("UPDATE search_cache SET hits=hits+1 WHERE key=?", (_key(query),))
        con.commit()
        payload = json.loads(row["payload"])
        con.close()
        return payload
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return None            # فشل الذاكرة المؤقتة لا يجوز أن يوقف البحث


def put(query: str, payload, path: str = DB) -> None:
    if not ENABLED or payload is None:
        return
    try:
        con = _db(path)
        con.execute(
            "INSERT OR REPLACE INTO search_cache(key,query,payload,hits,stored_at)"
            " VALUES(?,?,?,COALESCE((SELECT hits FROM search_cache WHERE key=?),0),?)",
            (_key(query), query, json.dumps(payload, ensure_ascii=False),
             _key(query), time.time()))
        con.commit()
        con.close()
    except (sqlite3.Error, TypeError, OSError):
        pass


def stats(path: str = DB) -> dict:
    try:
        con = _db(path)
        row = con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(hits),0) hits,"
            " COALESCE(MIN(stored_at),0) oldest FROM search_cache").fetchone()
        con.close()
        age = (time.time() - row["oldest"]) / 3600 if row["oldest"] else 0
        return {"entries": row["n"], "hits_saved": row["hits"],
                "oldest_hours": round(age, 1), "ttl_hours": TTL_HOURS}
    except sqlite3.Error:
        return {"entries": 0, "hits_saved": 0}


def clear(path: str = DB) -> int:
    try:
        con = _db(path)
        n = con.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
        con.execute("DELETE FROM search_cache")
        con.commit()
        con.close()
        return n
    except sqlite3.Error:
        return 0


def prune(path: str = DB) -> int:
    """يحذف المنتهية صلاحيتها."""
    try:
        con = _db(path)
        cut = time.time() - TTL_HOURS * 3600
        n = con.execute("SELECT COUNT(*) FROM search_cache WHERE stored_at<?",
                        (cut,)).fetchone()[0]
        con.execute("DELETE FROM search_cache WHERE stored_at<?", (cut,))
        con.commit()
        con.close()
        return n
    except sqlite3.Error:
        return 0


def cached_search(tool, query: str, path: str = DB):
    """
    يغلّف أداة البحث بالذاكرة المؤقتة.

    نخزّن حتى النتائج الفارغة: استعلام لا يعيد شيئاً سيظل كذلك خلال
    الساعات القليلة القادمة، وإعادة سؤاله هدر للحصة.
    """
    hit = get(query, path)
    if hit is not None:
        return hit
    try:
        res = tool.run(search_query=query)
    except Exception:
        return None            # لا نخزّن الفشل - قد يكون عارضاً
    put(query, res, path)
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "clear":
        print(f"مُسح {clear()} مدخل")
    elif args and args[0] == "prune":
        print(f"حُذف {prune()} مدخل منتهٍ")
    else:
        s = stats()
        print(f"  مدخلات محفوظة : {s['entries']}")
        print(f"  استعلامات وُفّرت: {s['hits_saved']}")
        print(f"  أقدم مدخل     : {s.get('oldest_hours', 0)} ساعة")
        print(f"  الصلاحية      : {s['ttl_hours']} ساعة")
