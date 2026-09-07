"""
قراءة الردود، وتصنيفها، وكتابة الجواب.

الرادار يرصد، والرسائل تخرج، ثم تصل الردود إلى صندوق لا يقرأه أحد -
فينكسر الخيط عند آخر خطوة، وهي الخطوة الوحيدة التي فيها مشترٍ حقيقي.

التصنيف يفصل ما يستحق دقيقتك عمّا لا يستحقها:

    استفسار   سؤال فنّي أو تجاري ينتظر جواباً   ← يُكتب الجواب
    اهتمام    طلب مكالمة أو عرض                 ← يُرفع للأعلى
    رفض       «غير مهتمّين»                     ← تُغلق الصفقة
    ردّ آلي    إجازة أو تأكيد استلام             ← يُتجاهل
    ارتداد    عنوان لا يوجد                     ← يُعلَّم البريد فاسداً

## حارس الأرقام

الخطر في الجواب الآلي ليس الأسلوب، بل الرقم. «الأغشية تصل خلال ١٤
يوماً» أو «الرفض الملحي ٩٩٫٤٪» - رقمٌ لم يقله أحد، أرسله النظام باسمك،
وصار التزاماً. النظام نفسه لفّق أربعة إسنادات في تقرير قبل أيام.

فكل رقم في الجواب يُقارَن بالمصادر: ملفّك، ورسالة العميل، ورسالتك
الأولى. رقمٌ لا أصل له فيها يمنع الإرسال ويحوّل الجواب إلى مسودة -
لا يُصحَّح ولا يُحذف، بل يُعرض عليك. الحذف يُخفي أن النموذج اخترع.
"""
from __future__ import annotations

import json
import os
import re
import sys

KINDS = ("استفسار", "اهتمام", "رفض", "ردّ آلي", "ارتداد", "غير ذي صلة")

# التصنيفات التي يجوز فيها جوابٌ آلي أصلاً
ANSWERABLE = ("استفسار", "اهتمام")

CLASSIFY = """صنّف رسالة بريد واردة ردّاً على رسالة تواصل تجاري.

--- الرسالة الواردة ---
من     : {sender}
الموضوع: {subject}
النصّ  :
{body}
--- نهاية ---

اكتب JSON صالحاً فقط:
{{"kind": "<واحد من: استفسار | اهتمام | رفض | ردّ آلي | ارتداد | غير ذي صلة>",
  "summary": "سطر واحد يلخّص ما يريده المرسِل",
  "questions": ["كل سؤال صريح في الرسالة، بنصّه"]}}

- «استفسار» إن سأل عن مواصفة أو سعر أو توفّر أو مرجع.
- «اهتمام» إن طلب مكالمة أو اجتماعاً أو عرضاً بلا سؤال محدّد.
- «ردّ آلي» لرسائل الإجازة وتأكيد الاستلام التلقائي.
- «ارتداد» لإشعارات فشل التسليم.
- إن لم يكن في الرسالة سؤال صريح فاجعل questions قائمة فارغة."""

ANSWER = """اكتب ردّاً بالعربية على رسالة عميل محتمل.

--- المرسِل (أنت تكتب نيابةً عنه) ---
{profile}
--- نهاية ---

--- رسالتك الأولى إليه ---
{first}
--- نهاية ---

--- ردّه ---
{inbound}
--- نهاية ---

--- أسئلته ---
{questions}
--- نهاية ---

اكتب JSON صالحاً فقط:
{{"subject": "سطر الموضوع", "body": "نصّ الردّ"}}

قواعد ملزِمة:
- أجب عن كل سؤال صراحةً. سؤال بلا جواب يُفقد الصفقة.
- **لا تذكر أي رقم غير موجود في النصوص أعلاه**: لا سعراً، ولا مدة
  توريد، ولا نسبة أداء، ولا مواصفة رقمية. إن كان الجواب يحتاج رقماً
  لا تملكه، فقل إنك سترسله بعد المراجعة - ولا تخمّن.
- إن سأل عن شيء خارج قدرات المرسِل في ملفه، قل ذلك صراحةً.
- من 60 إلى 130 كلمة، وبلا مقدّمات مجاملة طويلة."""

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789..")
_NUM = re.compile(r"\d[\d.,]*")


def _numbers(text: str) -> list[str]:
    """أرقام النصّ موحَّدةً - عربية وهندية معاً."""
    t = (text or "").translate(_AR_DIGITS)
    out = []
    for m in _NUM.finditer(t):
        v = m.group(0).strip(".,")
        if v:
            out.append(v)
    return out


def unsupported(reply: str, grounds: str) -> list[str]:
    """
    أرقام الجواب التي لا أصل لها في المصادر.

    المقارنة نصّية لا عددية عمداً: «14» و«١٤» يتوحّدان، أما «14» و«15»
    فيبقيان مختلفين - والفرق بينهما في مدة توريد هو الفرق بين وعدٍ
    يُوفى وآخر يُخلَف.
    """
    have = set(_numbers(grounds))
    bad = []
    for n in _numbers(reply):
        if n in have:
            continue
        # سنة أو رقم من خانة واحدة: ضجيج لا التزام
        if len(n) == 1:
            continue
        if n not in bad:
            bad.append(n)
    return bad


def _ask(prompt: str, kind: str, max_tokens: int) -> dict:
    import providers

    llm, _p = providers.make_llm(kind, 0, temperature=0.3,
                                 max_tokens=max_tokens, timeout=180)
    raw = str(llm.call(prompt) or "")
    try:
        return json.loads(raw.strip().strip("`").removeprefix("json").strip())
    except Exception:
        import pipeline
        if blob := pipeline._first_json_object(raw):
            try:
                return json.loads(blob)
            except Exception:
                pass
    raise ValueError(f"مخرَج غير صالح: {raw[:160]}")


def classify(sender: str, subject: str, body: str) -> dict:
    import providers

    got = _ask(CLASSIFY.format(sender=sender, subject=subject,
                               body=(body or "")[:2500]),
               providers.FAST, 500)
    kind = str(got.get("kind", "")).strip()
    return {
        "kind": kind if kind in KINDS else "غير ذي صلة",
        "summary": str(got.get("summary", "")).strip(),
        "questions": [str(q).strip() for q in (got.get("questions") or [])
                      if str(q).strip()],
    }


def compose(first: dict, inbound: dict, questions: list[str]) -> dict:
    import outreach
    import providers

    prompt = ANSWER.format(
        profile=outreach._profile_text(),
        first=f"{first.get('subject','')}\n{(first.get('body') or '')[:1200]}",
        inbound=(inbound.get("body") or "")[:1800],
        questions="\n".join(f"- {q}" for q in questions) or "(بلا سؤال صريح)")
    got = _ask(prompt, providers.ANALYTIC, 1200)
    return {"subject": str(got.get("subject", "")).strip(),
            "body": str(got.get("body", "")).strip()}


def _pending(path: str) -> list[dict]:
    """
    الواردات التي لم يُكتب لها ردّ بعد.

    الشرط على وجود رسالة صادرة **بعدها** لا على علم في الصف: العلم
    يحتاج هجرة، والاستعلام يعطي الجواب نفسه من البيانات الموجودة.

    و`kind='reply'` ليس زينة: الملاحظات تُخزَّن واردةً أيضاً، فكانت
    تُقرأ ردوداً وتُصنَّف من جديد في كل تشغيلة - نداء نموذج لكل ملاحظة
    كتبناها نحن، ثم ملاحظة عن الملاحظة.
    """
    import crm

    con = crm.db(path)
    rows = [dict(r) for r in con.execute(
        "SELECT m.*, c.name company FROM crm_messages m"
        " JOIN crm_deals d ON d.id=m.deal_id"
        " LEFT JOIN crm_companies c ON c.id=d.company_id"
        " WHERE m.direction='in' AND m.kind='reply'"
        "   AND NOT EXISTS (SELECT 1 FROM crm_messages r"
        "                   WHERE r.deal_id=m.deal_id AND r.direction='out'"
        "                     AND r.kind='answer' AND r.id>m.id)"
        " ORDER BY m.id DESC")]
    con.close()
    return rows


def _first_out(deal_id: int, path: str) -> dict:
    import crm

    con = crm.db(path)
    r = con.execute(
        "SELECT subject, body FROM crm_messages WHERE deal_id=?"
        " AND direction='out' ORDER BY id ASC LIMIT 1", (deal_id,)).fetchone()
    con.close()
    return dict(r) if r else {}


def handle(limit: int = 20, live: bool | None = None,
           path: str | None = None, pull: bool = True) -> dict:
    """
    الدورة كاملة: اسحب الوارد، صنّف، اكتب الجواب، أودعه أو أرسله.

    الإرسال الحيّ مشروط بأمرين معاً: علم `REPLY_LIVE`، وخلوّ الجواب من
    رقم لا أصل له. الثاني ليس تحفّظاً زائداً - الردّ على استفسار فنّي
    هو بالضبط المكان الذي يُخترع فيه رقم، لأن السؤال يطلب رقماً.
    """
    import crm
    import mailer
    import zoho

    p = path or crm.DB
    if live is None:
        raw = (os.getenv("REPLY_LIVE") or "").strip()
        live = (raw or "0").lower() not in ("0", "false", "no")

    pulled = {}
    if pull:
        try:
            pulled = zoho.sync_replies(path=p)
        except Exception as e:
            pulled = {"error": f"{type(e).__name__}: {e}"}

    profile = None
    acc = None
    answered, held, skipped = [], [], []

    for msg in _pending(p)[:limit]:
        got = classify(msg.get("to_addr") or "", msg.get("subject") or "",
                       msg.get("body") or "")
        _note(p, msg["deal_id"], f"تصنيف الردّ: {got['kind']} — {got['summary']}")

        if got["kind"] == "ارتداد":
            _kill_email(p, msg["deal_id"])
            skipped.append(f"{msg.get('company') or ''}: ارتداد — عُطّل البريد")
            continue
        if got["kind"] == "رفض":
            crm.set_stage(msg["deal_id"], "خسارة", note=got["summary"], path=p)
            skipped.append(f"{msg.get('company') or ''}: رفض — أُغلقت")
            continue
        if got["kind"] not in ANSWERABLE:
            skipped.append(f"{msg.get('company') or ''}: {got['kind']}")
            continue

        first = _first_out(msg["deal_id"], p)
        if profile is None:
            import outreach
            profile = outreach._profile_text()
        try:
            ans = compose(first, msg, got["questions"])
        except Exception as e:
            skipped.append(f"{msg.get('company') or ''}: تعذّرت الكتابة — {e}")
            continue

        grounds = " ".join([profile, first.get("body") or "",
                            first.get("subject") or "", msg.get("body") or "",
                            " ".join(got["questions"])])
        bad = unsupported(ans["body"], grounds)

        mid = _save(p, msg["deal_id"], ans, msg.get("to_addr") or "")
        label = f"{msg.get('company') or ''} · {ans['subject'][:40]}"

        if bad or not live:
            held.append(f"{label}" + (f"   [أرقام بلا أصل: {'، '.join(bad)}]"
                                      if bad else ""))
            continue

        try:
            acc = acc or zoho.account()
            zoho.send(msg["to_addr"], ans["subject"], ans["body"], acc=acc)
            _mark(p, mid)
            answered.append(label)
        except Exception as e:
            held.append(f"{label}   [تعذّر الإرسال: {e}]")

    return {"pulled": pulled, "live": live, "sent": answered,
            "held": held, "skipped": skipped,
            "budget": mailer.budget(p)}


def _save(path: str, deal_id: int, ans: dict, to_addr: str) -> int:
    import crm

    con = crm.db(path)
    cur = con.execute(
        "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,"
        "status,to_addr,created_at) VALUES(?,'out','answer',?,?,'draft',?,?)",
        (deal_id, ans["subject"], ans["body"], to_addr, crm._now()))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return mid


def _mark(path: str, message_id: int) -> None:
    import crm

    con = crm.db(path)
    con.execute("UPDATE crm_messages SET status='sent', sent_at=? WHERE id=?",
                (crm._now(), message_id))
    con.commit()
    con.close()


def _note(path: str, deal_id: int, text: str) -> None:
    import crm

    con = crm.db(path)
    con.execute(
        "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,"
        "status,created_at) VALUES(?,'in','note','',?,'sent',?)",
        (deal_id, text, crm._now()))
    con.commit()
    con.close()


def _kill_email(path: str, deal_id: int) -> None:
    """
    عنوان مرتدّ يُفرَّغ لا يُحذف الاتصال.

    الاسم والمنصب يبقيان مفيدَين للبحث عن عنوان صحيح؛ حذف الصف يفقدهما
    ويعيد استيراد العنوان الفاسد نفسه في الدفعة القادمة.
    """
    import crm

    con = crm.db(path)
    row = con.execute("SELECT company_id FROM crm_deals WHERE id=?",
                      (deal_id,)).fetchone()
    if row:
        con.execute("UPDATE crm_contacts SET email='' WHERE company_id=?",
                    (row["company_id"],))
    con.commit()
    con.close()


def render(r: dict) -> str:
    out = []
    pulled = r.get("pulled") or {}
    if pulled.get("error"):
        out.append(f"تعذّر سحب الوارد: {pulled['error']}")
    elif pulled:
        out.append(f"سُحب من الوارد: {pulled.get('logged', 0)} ردّ جديد")

    mode = "الإرسال الحيّ مفعّل" if r.get("live") else "الوضع: مسودات"
    out.append(mode)
    out.append("")

    if r.get("sent"):
        out.append(f"أُرسلت أجوبة ({len(r['sent'])}):")
        out += [f"  ✓ {x}" for x in r["sent"]]
        out.append("")
    if r.get("held"):
        out.append(f"محجوزة لمراجعتك ({len(r['held'])}):")
        out += [f"  · {x}" for x in r["held"]]
        out.append("")
    if r.get("skipped"):
        out.append(f"بلا جواب ({len(r['skipped'])}):")
        out += [f"  - {x}" for x in r["skipped"]]
    if not (r.get("sent") or r.get("held") or r.get("skipped")):
        out.append("لا ردود جديدة.")
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
    forced = True if "--live" in args else (False if "--draft" in args else None)
    n = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--n=")), 20)
    print(render(handle(limit=n, live=forced)))
