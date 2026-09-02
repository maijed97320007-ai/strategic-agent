"""
كتابة رسائل التواصل من بيانات الفرصة.

الغرض تشغيلي لا تجميلي: الرادار يرصد خمساً وعشرين فرصة ولا تُراسَل أيّ
منها، فالرصد بلا تواصل عملٌ لا يُنتج بيعاً.

ما يجعل الرسالة تُقرأ لا تُحذف - وهو ما تُبنى عليه القواعد أدناه:

  · **رقم المناقصة وموعدها في أول سطرين**. موظف المشتريات يتلقّى عشرات
    الرسائل العامة يومياً، والرسالة التي تذكر مناقصته بالاسم تُقرأ.
  · **قدرة محدّدة لا قائمة خدمات**. «تشخيص الأغشية بالضغط التفاضلي
    والرفض الملحي» تقول خبرة؛ «حلول متكاملة للمياه» تقول لا شيء.
  · **طلب واحد صغير**. مكالمة عشر دقائق أو نسخة من كراسة المناقصة -
    لا عرض سعر ولا اجتماع ساعة.
  · **بلا رقم غير مسنَد**. النظام نفسه لفّق أربعة إسنادات في تقرير
    وأزالها الحارس؛ رقمٌ مخترع في رسالة إلى لجنة مناقصات لا يُسحب.

وحارس التكرار مبنيّ في المولّد لا مضاف عليه: `draft_for` ترفض الجهة
المُراسَلة سابقاً ما لم يُطلب خلاف ذلك صراحةً. الرادار يرصد للجهة
الواحدة عدة مناقصات، وثلاث رسائل متطابقة في أسبوع تُفقد الثقة لا تبنيها.

الرسائل تُحفظ **مسودات**. الإرسال قرار المستخدم بضغطة، لا قرار النظام.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone

import crm

BRIEF = """اكتب رسالة بريد إلكتروني بالعربية إلى جهة طرحت مناقصة.

--- المرسِل (أنت تكتب نيابةً عنه) ---
{profile}
--- نهاية ---

--- الفرصة ---
الجهة        : {company}
عنوان المناقصة: {title}
الموقع       : {location}
آخر موعد     : {deadline}
سبب الملاءمة : {why}
الإجراء المقترح: {action}
المصدر       : {url}
--- نهاية ---

اكتب JSON صالحاً فقط:
{{"subject": "سطر الموضوع", "body": "نصّ الرسالة"}}

قواعد ملزِمة:
- الموضوع يذكر المناقصة بالاسم أو رقمها. لا موضوع عام مثل «عرض خدماتنا».
- افتح بالمناقصة نفسها وموعدها، لا بتعريف عن المرسِل.
- اذكر **قدرة واحدة أو اثنتين محدّدتين** من ملف المرسِل تخدم هذه المناقصة
  تحديداً. لا تسرد قائمة خدمات.
- اختم بطلب واحد صغير: مكالمة قصيرة أو نسخة من كراسة المناقصة.
- **لا تذكر رقماً أو تاريخاً أو مرجعاً غير موجود أعلاه.** لا سعراً، ولا
  مدة تنفيذ، ولا نسبة توفير، ولا مشروعاً سابقاً لم يُذكر في الملف.
- من 90 إلى 150 كلمة. رسالة أطول لا تُقرأ.
- بلا مبالغة ولا عبارات تسويقية جوفاء («رائدون»، «الأفضل»، «حلول متكاملة»).
- **التوقيع من حقل `sender` حرفياً**: الاسم ثم المنصب ثم اسم الشركة
  ثم البريد. لا تخترع هاتفاً ولا منصباً.
- إن كان «آخر موعد» فارغاً فلا تخترعه ولا تقل «قريباً»."""

FOLLOWUP = """اكتب رسالة متابعة قصيرة بالعربية.

الرسالة الأولى أُرسلت في {sent} ولم يصل ردّ.

--- الرسالة الأولى ---
الموضوع: {subject}
{body}
--- نهاية ---

آخر موعد للمناقصة: {deadline}

اكتب JSON صالحاً فقط: {{"subject": "...", "body": "..."}}

قواعد ملزِمة:
- من 40 إلى 70 كلمة. المتابعة أقصر من الأولى دائماً.
- لا تُعِد شرح ما في الأولى. أضف سطراً واحداً جديداً ذا قيمة.
- اذكر قرب الموعد إن كان معروفاً، بلا ضغط ولا إلحاح.
- الموضوع يبدأ بـ«متابعة:» ثم موضوع الرسالة الأولى.
- بلا اعتذار عن الإزعاج - يُضعف الرسالة ولا يضيف شيئاً."""


COMPANY_BRIEF = """اكتب رسالة بريد إلكتروني بالعربية إلى شركة في قائمة عملاء المرسِل.

--- المرسِل (أنت تكتب نيابةً عنه) ---
{profile}
--- نهاية ---

--- الشركة المستقبِلة ---
الاسم    : {company}
القطاع   : {sector}
الدولة   : {country}
المسؤول  : {contact}
ملاحظات  : {notes}
--- نهاية ---

الزاوية المطلوبة: {angle}

اكتب JSON صالحاً فقط:
{{"subject": "سطر الموضوع", "body": "نصّ الرسالة"}}

قواعد ملزِمة:
- **لا تعرف عن هذه الشركة إلا ما ورد أعلاه.** لا تفترض أن لديها محطة
  بعينها، ولا حجماً، ولا مشكلة قائمة، ولا تعاملاً سابقاً مع المرسِل.
  اكتب بصيغة «إن كان لديكم…» لا بصيغة «محطتكم تعاني…».
- افتح بسبب مقنع للكتابة إليهم تحديداً - من قطاعهم أو دولتهم.
- اذكر **قدرة واحدة أو اثنتين محدّدتين** من ملف المرسِل تناسب قطاعهم.
  «تشخيص الأغشية بالضغط التفاضلي والرفض الملحي» لا «حلول متكاملة».
- اختم بطلب واحد صغير: مكالمة عشر دقائق أو زيارة تشخيصية.
- **لا رقم ولا سعر ولا نسبة ولا مدة غير موجودة في ملف المرسِل.**
- من 80 إلى 130 كلمة.
- بلا عبارات جوفاء («رائدون»، «الأفضل»، «شراكة استراتيجية»).
- **التوقيع من حقل `sender` حرفياً**: الاسم ثم المنصب ثم اسم الشركة
  ثم البريد. لا تخترع هاتفاً ولا منصباً ولا لقباً غير المذكور.
- خاطب المسؤول باسمه إن ذُكر، وإلا فبصيغة محايدة مهذّبة.
- اكتب بصفة الشركة («نحن في …») لا بصفة فرد مستقل."""

# زوايا التواصل - كلٌّ منها خدمة يقدّمها المرسِل فعلاً حسب ملفه
ANGLES = {
    "تشخيص": "عرض زيارة تشخيصية لمحطة قائمة: قراءة الضغط التفاضلي "
             "والرفض الملحي وتحديد نوع الانسداد.",
    "خط-أساس": "تأسيس خط أساس تشغيلي موثّق لمحطة قائمة، تُقاس عليه "
               "أي شكوى لاحقة بدل الأرقام النظرية.",
    "غسيل": "بروتوكول غسيل كيميائي مرتّب (قلوي ثم حمضي) عند تدهور "
            "الأداء 10-15% من خط الأساس.",
    "تصميم": "خدمة تصميم أو مراجعة تصميم محطة تناضح عكسي جديدة.",
    "توريد": "توريد أغشية وقطع غيار مع دعم فني في الاختيار.",
    "مراجعة-عرض": "مراجعة فنية محايدة لعرض مورّد قبل التوقيع: تدقيق "
                  "أرقام الاستهلاك والاسترجاع والضغط.",
    "تدريب": "تدريب المشغّلين على قراءة المؤشرات الميدانية وبروتوكول الغسيل.",
}


def draft_for_company(company_id: int, angle: str = "تشخيص",
                      force: bool = False, path: str = crm.DB) -> dict:
    """
    رسالة إلى شركة من قائمتك - لا علاقة لها بمناقصة.

    تفتح صفقة للشركة إن لم تكن مفتوحة، فتبقى الرسالة مربوطة بمسار متتبَّع
    بدل أن تكون رسالة يتيمة لا يُعرف ما جرى بعدها.
    """
    prev = crm.contacted(company_id, path)
    if prev and not force:
        when = (prev.get("sent_at") or prev.get("created_at") or "")[:10]
        con = crm.db(path)
        nm = con.execute("SELECT name FROM crm_companies WHERE id=?",
                         (company_id,)).fetchone()
        con.close()
        return {"skipped": True, "reason":
                f"«{nm['name'] if nm else company_id}» رُوسلت في {when} "
                f"(«{(prev.get('subject') or '')[:46]}»). force=True لتجاوزها."}

    con = crm.db(path)
    c = con.execute("SELECT * FROM crm_companies WHERE id=?",
                    (company_id,)).fetchone()
    if not c:
        con.close()
        raise KeyError(f"لا شركة بالمعرّف {company_id}")
    c = dict(c)
    k = con.execute(
        "SELECT name, role, email FROM crm_contacts WHERE company_id=?"
        " ORDER BY (email<>'') DESC, id LIMIT 1", (company_id,)).fetchone()
    con.close()
    k = dict(k) if k else {}

    deal_id = crm.open_deal_for_company(company_id, path=path)

    who = " · ".join(x for x in (k.get("name"), k.get("role")) if x) or "غير معروف"
    msg = _ask(COMPANY_BRIEF.format(
        profile=_profile_text(),
        company=c["name"], sector=c.get("sector") or "غير مذكور",
        country=c.get("country") or "غير مذكور",
        contact=who, notes=(c.get("notes") or "لا شيء")[:300],
        angle=ANGLES.get(angle, angle)))

    to_addr = k.get("email") or ""
    con = crm.db(path)
    cur = con.execute(
        "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,status,"
        "to_addr,created_at) VALUES(?,'out','first',?,?,'draft',?,?)",
        (deal_id, msg["subject"], msg["body"], to_addr, crm._now()))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return {"id": mid, "deal_id": deal_id, "to": to_addr,
            "company": c["name"], **msg}


def draft_batch(angle: str = "تشخيص", limit: int = 10,
                path: str = crm.DB) -> list[dict]:
    """
    مسودات لكل شركة لم تُراسَل بعد ولها بريد.

    الترتيب: من له بريد مسجّل أولاً - رسالة بلا مرسَل إليه لا تُرسَل،
    وكتابتها إهدار نداءٍ للنموذج.
    """
    out = []
    for c in crm.companies(with_email_only=True, path=path):
        if c["contacted"]:
            continue
        try:
            out.append(draft_for_company(c["id"], angle=angle, path=path))
        except Exception as e:
            out.append({"skipped": True,
                        "reason": f"{c['name']}: {type(e).__name__}: {e}"})
        if len(out) >= limit:
            break
    return out


def _profile_text() -> str:
    try:
        from opportunity import load_profile
        p = load_profile()
    except Exception:
        p = {}
    # `sender` ضروري: بدونه يوقّع النموذج باسم الشركة أو بصفة عامة،
    # وقد فعل - «استشاري مستقل في معالجة وتحلية المياه» بلا اسم.
    keep = ("company", "entity_type", "country", "sector", "website",
            "capabilities", "products", "strengths",
            "outside_country_modes", "sender")
    return json.dumps({k: p[k] for k in keep if k in p},
                      ensure_ascii=False, indent=1)[:1800]


def _ask(brief: str) -> dict:
    """نداء واحد للنموذج ويعيد {subject, body}."""
    import providers
    llm, _p = providers.make_llm(providers.ANALYTIC, 0, temperature=0.4,
                                 max_tokens=1200, timeout=180)
    raw = str(llm.call(brief) or "")

    import pipeline
    obj = None
    try:
        obj = json.loads(raw.strip().strip("`").removeprefix("json").strip())
    except Exception:
        if blob := pipeline._first_json_object(raw):
            try:
                obj = json.loads(blob)
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        raise ValueError(f"مخرَج غير صالح: {raw[:160]}")
    return {"subject": str(obj.get("subject", "")).strip(),
            "body": str(obj.get("body", "")).strip()}


def draft_for(deal_id: int, force: bool = False, path: str = crm.DB) -> dict:
    """
    يكتب رسالة أولى لصفقة ويحفظها مسودة.

    يرفض إن كانت الجهة مُراسَلة سابقاً - والحارس على مستوى الجهة لا
    الصفقة، لأن الرادار يرصد للجهة الواحدة عدة مناقصات.
    """
    con = crm.db(path)
    d = con.execute(
        "SELECT d.*, c.name company_name, c.id cid FROM crm_deals d"
        " LEFT JOIN crm_companies c ON c.id=d.company_id WHERE d.id=?",
        (deal_id,)).fetchone()
    if not d:
        con.close()
        raise KeyError(f"لا صفقة بالمعرّف {deal_id}")
    d = dict(d)

    opp = con.execute(
        "SELECT o.why, o.action, e.location, e.url FROM opportunities o"
        " LEFT JOIN events e ON e.id=o.event_id WHERE o.id=?",
        (d["opportunity_id"],)).fetchone()
    opp = dict(opp) if opp else {}

    to_addr = ""
    if d.get("cid"):
        c = con.execute(
            "SELECT email FROM crm_contacts WHERE company_id=? AND email<>''"
            " ORDER BY id LIMIT 1", (d["cid"],)).fetchone()
        to_addr = c["email"] if c else ""
    con.close()

    if not force and d.get("cid"):
        if prev := crm.contacted(d["cid"], path):
            when = (prev.get("sent_at") or prev.get("created_at") or "")[:10]
            return {"skipped": True, "reason":
                    f"الجهة «{d['company_name']}» رُوسلت في {when} "
                    f"(«{(prev.get('subject') or '')[:50]}»). "
                    f"استعمل force=True لتجاوز الحارس."}

    msg = _ask(BRIEF.format(
        profile=_profile_text(),
        company=d.get("company_name") or "الجهة",
        title=d["title"],
        location=opp.get("location") or "—",
        deadline=d.get("deadline") or "غير مذكور",
        why=(opp.get("why") or "")[:400],
        action=(opp.get("action") or "")[:200],
        url=opp.get("url") or "—"))

    con = crm.db(path)
    cur = con.execute(
        "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,status,"
        "to_addr,created_at) VALUES(?,'out','first',?,?,'draft',?,?)",
        (deal_id, msg["subject"], msg["body"], to_addr, crm._now()))
    con.commit()
    mid = cur.lastrowid
    con.close()

    return {"id": mid, "deal_id": deal_id, "to": to_addr,
            "company": d.get("company_name"), **msg}


def draft_followup(deal_id: int, path: str = crm.DB) -> dict:
    """متابعة للرسالة الأولى التي لم يصل عليها ردّ."""
    con = crm.db(path)
    first = con.execute(
        "SELECT * FROM crm_messages WHERE deal_id=? AND direction='out'"
        " AND kind='first' ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
    d = con.execute("SELECT * FROM crm_deals WHERE id=?", (deal_id,)).fetchone()
    con.close()
    if not first:
        raise KeyError("لا رسالة أولى لهذه الصفقة")

    msg = _ask(FOLLOWUP.format(
        sent=(first["sent_at"] or first["created_at"])[:10],
        subject=first["subject"], body=(first["body"] or "")[:1200],
        deadline=(d["deadline"] if d else "") or "غير مذكور"))

    con = crm.db(path)
    cur = con.execute(
        "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,status,"
        "to_addr,created_at) VALUES(?,'out','followup',?,?,'draft',?,?)",
        (deal_id, msg["subject"], msg["body"], first["to_addr"], crm._now()))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return {"id": mid, "deal_id": deal_id, **msg}


def drafts(status: str = "draft", path: str = crm.DB) -> list[dict]:
    con = crm.db(path)
    rows = [dict(r) for r in con.execute(
        "SELECT m.*, d.title deal_title, c.name company FROM crm_messages m"
        " JOIN crm_deals d ON d.id=m.deal_id"
        " LEFT JOIN crm_companies c ON c.id=d.company_id"
        " WHERE m.status=? AND m.direction='out' ORDER BY m.id DESC", (status,))]
    con.close()
    return rows


def mark_sent(message_id: int, path: str = crm.DB) -> None:
    """
    يسجّل أن الرسالة أُرسلت وينقل الصفقة إلى «تواصَلنا».

    التسجيل هو ما يجعل حارس التكرار يعمل: رسالة أُرسلت من بريدك ولم
    تُسجَّل هنا تعني أن النظام سيكتب للجهة نفسها مرة أخرى.
    """
    con = crm.db(path)
    row = con.execute("SELECT deal_id FROM crm_messages WHERE id=?",
                      (message_id,)).fetchone()
    con.execute("UPDATE crm_messages SET status='sent', sent_at=? WHERE id=?",
                (crm._now(), message_id))
    con.commit()
    con.close()
    if row:
        crm.set_stage(row["deal_id"], "تواصَلنا", path=path)
        crm.schedule_next(row["deal_id"], "متابعة إن لم يصل ردّ", 5, path=path)


def render(msg: dict) -> str:
    if msg.get("skipped"):
        return f"⊘ {msg['reason']}"
    return "\n".join([
        f"إلى     : {msg.get('to') or '(لا بريد مسجّل لهذه الجهة)'}",
        f"الجهة   : {msg.get('company') or '—'}",
        f"الموضوع : {msg.get('subject','')}",
        "",
        msg.get("body", ""),
    ])


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    a = sys.argv[1:]
    if a and a[0] == "list":
        rows = crm.companies(path=crm.DB)
        yes = [r for r in rows if r["contacted"]]
        print(f"{len(rows)} شركة · رُوسل منها {len(yes)}")
        print()
        for r in rows[:40]:
            mark = "✓" if r["contacted"] else " "
            mail = r.get("email") or "(بلا بريد)"
            print(f"  {mark} #{r['id']:<4} {r['name'][:34]:<36} {mail}")
    elif a and a[0] == "angles":
        for k, v in ANGLES.items():
            print(f"  {k:<14} {v}")
    elif a and a[0] == "company" and len(a) > 1:
        ang = a[2] if len(a) > 2 and not a[2].startswith("--") else "تشخيص"
        print(render(draft_for_company(int(a[1]), angle=ang,
                                       force="--force" in a)))
    elif a and a[0] == "batch":
        ang = a[1] if len(a) > 1 and not a[1].startswith("--") else "تشخيص"
        n = int(next((x.split("=")[1] for x in a if x.startswith("--n=")), 5))
        for m in draft_batch(angle=ang, limit=n):
            print(render(m)); print("-" * 58)
    elif a and a[0] == "draft" and len(a) > 1:
        print(render(draft_for(int(a[1]), force="--force" in a)))
    elif a and a[0] == "followup" and len(a) > 1:
        print(render(draft_followup(int(a[1]))))
    elif a and a[0] == "sent" and len(a) > 1:
        mark_sent(int(a[1]))
        print("سُجّلت كمُرسَلة، والصفقة انتقلت إلى «تواصَلنا».")
    else:
        rows = drafts()
        print(f"{len(rows)} مسودة بانتظارك\n")
        for m in rows:
            print(f"  #{m['id']}  {m['company'] or '—'}")
            print(f"       {m['subject']}")
            print(f"       إلى: {m['to_addr'] or '(بلا بريد)'}\n")
        print("الأوامر:")
        print("  list                       شركاتك وحالة التواصل")
        print("  angles                     زوايا التواصل المتاحة")
        print("  company <رقم> [زاوية]      مسودة لشركة من قائمتك")
        print("  batch [زاوية] [--n=5]      مسودات لمن لم يُراسَل")
        print("  followup <صفقة>            متابعة")
        print("  sent <رسالة>               تسجيل الإرسال")
