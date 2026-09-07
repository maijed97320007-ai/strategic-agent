"""
إرسال الدفعات بإيقاع محسوب، وبسقف يومي لا يُتجاوز.

لماذا الإيقاع ليس ترفاً: Zoho Mail بريد أعمال لا منصّة تسويق. سياستها
المعلنة تحيل الإرسال الجماعي إلى Zoho Campaigns، وحدّها اليومي ألف
رسالة، وخنقها بالساعة **ديناميكي** تحدّده خوارزمية لا رقم منشور. أي
أن الحدّ الحقيقي غير معروف مسبقاً - يُكتشف بالاصطدام به.

والاصطدام لا يكلّف الدفعة وحدها. نطاق جديد يُطلق مئة رسالة باردة في
عشر دقائق يصير في قوائم الحظر، فتذهب معه فواتيرك وعروض أسعارك وردودك
على عملاء حاليين. الأصل الذي تحاول استعماله هو الأصل الذي تحرقه.

فالتصميم هنا:

    سقف يومي  ─→  يُقرأ من قاعدة البيانات لا من عدّاد في الذاكرة،
                  فإعادة التشغيل لا تصفّره
    إيقاع     ─→  فاصل بين رسالة وأخرى، لا دفعة متلاحقة
    توقّف مبكر ─→  خطآن متتاليان من Zoho يوقفان الدفعة فوراً؛ الاستمرار
                  بعد الخنق هو ما يحوّل الإبطاء المؤقّت إلى حظر دائم

`MAIL_LIVE=0` هو الافتراضي: الدفعة تُودَع مسوداتٍ في صندوقك. اجعلها 1
حين تقرّر أن الرسائل تُرسل من نفسها.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

DAILY_CAP = int(os.getenv("MAIL_DAILY_CAP", "200"))
PER_MIN = int(os.getenv("MAIL_PER_MIN", "12"))
# خطآن متتاليان يوقفان: الأول قد يكون عنواناً فاسداً، والثاني خنقاً
STOP_AFTER_ERRORS = 2


def live() -> bool:
    """هل الإرسال فعليّ؟ يُقرأ وقت النداء ليسري تغيير .env بلا إعادة تشغيل."""
    raw = (os.getenv("MAIL_LIVE") or "").strip()
    return (raw or "0").lower() not in ("0", "false", "no")


def sent_today(path: str | None = None) -> int:
    """
    المُرسَل اليوم من قاعدة البيانات لا من عدّاد في الذاكرة.

    عدّاد الذاكرة يُصفَّر بإعادة تشغيل البرنامج - وثلاث تشغيلات في يوم
    تعني ثلاثة أضعاف السقف بلا أن يظهر ذلك في أي مكان.
    """
    import crm

    con = crm.db(path or crm.DB)
    n = con.execute(
        "SELECT COUNT(*) c FROM crm_messages WHERE direction='out'"
        " AND status='sent' AND substr(sent_at,1,10)=?",
        (date.today().isoformat(),)).fetchone()["c"]
    con.close()
    return int(n or 0)


def budget(path: str | None = None) -> dict:
    used = sent_today(path)
    return {"used": used, "cap": DAILY_CAP, "left": max(0, DAILY_CAP - used)}


def send_batch(limit: int | None = None, per_min: int | None = None,
               force_live: bool | None = None,
               path: str | None = None) -> dict:
    """
    يفرّغ قائمة المسودات إلى Zoho بإيقاع، ويتوقّف عند السقف أو الخطأ.

    `mark_sent` يُستدعى في الوضع الحيّ فقط: تعليم رسالة «أُرسلت» وهي
    مسودة يُعمي حارس التكرار، فلا تُراسَل الشركة أبداً بعدها.
    """
    import outreach
    import zoho

    p = path or _db()
    is_live = live() if force_live is None else force_live
    rate = max(1, int(per_min or PER_MIN))
    gap = 60.0 / rate

    b = budget(p)
    queue = [m for m in outreach.drafts(path=p) if (m.get("to_addr") or "").strip()]
    no_addr = len(outreach.drafts(path=p)) - len(queue)

    room = b["left"] if is_live else len(queue)
    take = min(len(queue), room, limit or len(queue))
    if is_live and room <= 0:
        return {"live": True, "sent": 0, "budget": b,
                "stopped": f"بلغت السقف اليومي ({DAILY_CAP})"}

    acc = zoho.account()
    done, failed, streak, stopped = [], [], 0, ""
    t0 = time.time()

    for i, m in enumerate(queue[:take]):
        if i:
            time.sleep(gap)
        try:
            if is_live:
                zoho.send(m["to_addr"], m["subject"], m["body"], acc=acc)
                outreach.mark_sent(m["id"], path=p)
            else:
                zoho.save_draft(m["to_addr"], m["subject"], m["body"], acc=acc)
                _approve(m["id"], p)
            done.append(f"#{m['id']} → {m['to_addr']}")
            streak = 0
        except Exception as e:
            failed.append(f"#{m['id']} {m['to_addr']}: {e}")
            streak += 1
            if streak >= STOP_AFTER_ERRORS:
                stopped = ("خطآن متتاليان من Zoho - أُوقفت الدفعة."
                           " الاستمرار بعد الخنق يحوّله إلى حظر.")
                break

    if not stopped and take < len(queue):
        stopped = f"بقي {len(queue) - take} في الطابور (السقف أو الحدّ المطلوب)"

    return {
        "live": is_live,
        "sent": len(done),
        "failed": failed,
        "no_address": no_addr,
        "seconds": round(time.time() - t0, 1),
        "rate_per_min": rate,
        "budget": budget(p),
        "stopped": stopped,
        "account": acc["address"],
        "results": done,
    }


def _approve(message_id: int, path: str) -> None:
    """الوضع غير الحيّ: الرسالة صارت مسودة في Zoho، لا مُرسَلة."""
    import crm

    con = crm.db(path)
    con.execute("UPDATE crm_messages SET status='approved' WHERE id=?",
                (message_id,))
    con.commit()
    con.close()


def _db() -> str:
    import crm
    return crm.DB


def render(r: dict) -> str:
    """تقرير الدفعة نصّاً."""
    head = "أُرسلت فعلياً" if r.get("live") else "أُودعت مسودات في Zoho"
    b = r.get("budget") or {}
    out = [
        f"{head}: {r.get('sent', 0)} رسالة في {r.get('seconds', 0)} ثانية"
        f"  ({r.get('rate_per_min')}/دقيقة)",
        f"الحساب: {r.get('account', '')}",
        f"السقف اليومي: {b.get('used', 0)}/{b.get('cap', 0)}"
        f"   المتبقّي {b.get('left', 0)}",
    ]
    if r.get("no_address"):
        out.append(f"بلا بريد فتُخطّت: {r['no_address']}")
    if r.get("stopped"):
        out.append(f"توقّف: {r['stopped']}")
    if r.get("failed"):
        out.append("")
        out.append("أخفقت:")
        out += [f"  {x}" for x in r["failed"][:10]]
    if r.get("results"):
        out.append("")
        out += [f"  {x}" for x in r["results"][:40]]
    return "\n".join(out)


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    import main  # يحمّل .env  # noqa: F401

    args = sys.argv[1:]
    n = None
    rate = None
    forced = None
    for a in args:
        if a in ("--live", "live"):
            forced = True
        elif a in ("--draft", "draft"):
            forced = False
        elif a.startswith("--n="):
            n = int(a.split("=", 1)[1])
        elif a.startswith("--rate="):
            rate = int(a.split("=", 1)[1])
        elif a.isdigit():
            n = int(a)

    if args and args[0] == "budget":
        b = budget()
        print(f"اليوم: {b['used']}/{b['cap']}   المتبقّي {b['left']}")
        print(f"الوضع: {'حيّ' if live() else 'مسودات'}   (MAIL_LIVE)")
    else:
        print(render(send_batch(limit=n, per_min=rate, force_live=forced)))
