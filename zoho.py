"""
تكامل Zoho Mail: مسودات، وقراءة الردود.

لماذا مسودة لا إرسال؟ الفرق ضغطةٌ واحدة عليك، ومسافةُ أمانٍ كاملة على
سمعتك. النظام نفسه لفّق أربعة إسنادات في تقرير وأزالها الحارس قبل أيام؛
رقمٌ مخترع في رسالة إلى عميل لا يُسحب بعد الإرسال. المسودة تصل صندوقك
جاهزة، وأنت تقرأ وترسل.

الدالة `send()` موجودة لمن أراد لاحقاً - لكن `push_drafts()` هي المسار
الافتراضي، ولن تُستدعى `send()` تلقائياً من أي جدولة.

مركز البيانات مهمّ: حساب مُنشأ في السعودية يعيش على `.sa` ومفاتيحه لا
تعمل على `.com`. يُضبط بـ ZOHO_DC، والقيم: com · eu · in · au · jp · sa · ca

الإعداد لمرة واحدة (لا أستطيع فعله نيابةً عنك - يتطلّب دخولك):

  1. api-console.zoho.com ← Add Client ← Self Client
  2. انسخ Client ID و Client Secret إلى .env
  3. تبويب «Generate Code»، النطاق:
       ZohoMail.accounts.READ,ZohoMail.messages.ALL,ZohoMail.folders.READ
     المدة 10 دقائق، ثم انسخ الرمز
  4. python zoho.py auth <الرمز>
     يبدّله برمز تحديث دائم ويكتبه في .env

الرمز المؤقت يُستهلك مرة واحدة وينتهي بعد 10 دقائق - أعد التوليد إن تأخّرت.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DC = (os.getenv("ZOHO_DC") or "com").strip().lstrip(".")
ACCOUNTS = f"https://accounts.zoho.{DC}"
API = f"https://mail.zoho.{DC}/api"

SCOPES = ("ZohoMail.accounts.READ,ZohoMail.messages.ALL,"
          "ZohoMail.folders.READ")

_token: dict = {"value": "", "expires": 0.0}


def _env_path() -> Path:
    try:
        from main import app_dir
        return app_dir() / ".env"
    except Exception:
        return Path(".env")


def _req(url: str, method: str = "GET", body: dict | None = None,
         token: str | None = None, form: dict | None = None,
         timeout: int = 30) -> dict:
    data = headers = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    headers = headers or {}
    headers["Accept"] = "application/json"
    if token:
        headers["Authorization"] = f"Zoho-oauthtoken {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Zoho {e.code}: {detail}") from None


# ======================
# المصادقة
# ======================
def exchange_code(code: str) -> dict:
    """يبدّل الرمز المؤقت برمز تحديث دائم ويكتبه في .env."""
    cid = os.getenv("ZOHO_CLIENT_ID")
    sec = os.getenv("ZOHO_CLIENT_SECRET")
    if not cid or not sec:
        raise RuntimeError("ZOHO_CLIENT_ID و ZOHO_CLIENT_SECRET مطلوبان في .env")

    out = _req(f"{ACCOUNTS}/oauth/v2/token", "POST", form={
        "grant_type": "authorization_code", "client_id": cid,
        "client_secret": sec, "code": code.strip()})

    rt = out.get("refresh_token")
    if not rt:
        raise RuntimeError(f"لا رمز تحديث في الرد: {out}")

    p = _env_path()
    txt = p.read_text(encoding="utf-8") if p.exists() else ""
    if "ZOHO_REFRESH_TOKEN=" in txt:
        import re
        txt = re.sub(r"ZOHO_REFRESH_TOKEN=.*", f"ZOHO_REFRESH_TOKEN={rt}", txt)
    else:
        txt += f"\nZOHO_REFRESH_TOKEN={rt}\n"
    p.write_text(txt, encoding="utf-8")
    return {"saved_to": str(p), "scope": out.get("scope", "")}


def token() -> str:
    """
    رمز وصول صالح. يُجدَّد من رمز التحديث ويُخزَّن في الذاكرة.

    نطرح دقيقة من مدة الصلاحية: رمز ينتهي أثناء نداءٍ جارٍ يُفشل عملية
    كاملة، والدقيقة أرخص من إعادة الدفعة.
    """
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]

    rt = os.getenv("ZOHO_REFRESH_TOKEN")
    cid = os.getenv("ZOHO_CLIENT_ID")
    sec = os.getenv("ZOHO_CLIENT_SECRET")
    if not (rt and cid and sec):
        raise RuntimeError(
            "Zoho غير مُعدّ. الخطوات في رأس zoho.py — أو: python zoho.py setup")

    out = _req(f"{ACCOUNTS}/oauth/v2/token", "POST", form={
        "grant_type": "refresh_token", "client_id": cid,
        "client_secret": sec, "refresh_token": rt})
    if not out.get("access_token"):
        raise RuntimeError(f"تعذّر تجديد الرمز: {out}")

    _token["value"] = out["access_token"]
    _token["expires"] = time.time() + int(out.get("expires_in", 3600)) - 60
    return _token["value"]


def account() -> dict:
    """
    الحساب الأول - معرّفه وعنوانه.

    `accountId` يدخل في كل مسار، و`fromAddress` يجب أن يطابق الحساب
    المُصادَق عليه وإلا رفض Zoho الرسالة.
    """
    out = _req(f"{API}/accounts", token=token())
    data = out.get("data") or []
    if not data:
        raise RuntimeError("لا حساب بريد في هذا الاشتراك")
    a = data[0]
    addr = ""
    for e in (a.get("emailAddress") or []):
        if e.get("isPrimary") or not addr:
            addr = e.get("mailId", "")
    return {"accountId": a.get("accountId"), "address": addr,
            "name": a.get("displayName") or a.get("accountName", "")}


# ======================
# المسودات
# ======================
def save_draft(to: str, subject: str, body: str, acc: dict | None = None,
               cc: str = "", in_reply_to: str = "") -> dict:
    """
    يحفظ رسالة في مسودات Zoho. `mode=draft` هو ما يمنع الإرسال.

    النصّ يُرسل بصيغة plaintext عمداً: الرسالة عربية والتنسيق HTML يضيف
    مخاطر اتجاه ومحارف غير مرئية بلا مقابل في رسالة من مئة كلمة.
    """
    acc = acc or account()
    payload = {
        "mode": "draft",
        "fromAddress": acc["address"],
        "toAddress": to,
        "subject": subject,
        "content": body,
        "mailFormat": "plaintext",
        "encoding": "UTF-8",
    }
    if cc:
        payload["ccAddress"] = cc
    if in_reply_to:
        payload["inReplyTo"] = in_reply_to
    return _req(f"{API}/accounts/{acc['accountId']}/messages", "POST",
                body=payload, token=token())


def send(to: str, subject: str, body: str, acc: dict | None = None,
         cc: str = "") -> dict:
    """
    إرسال فوري.

    موجودة عمداً بلا مستدعٍ تلقائي: لا جدولة ولا دفعة تستدعيها. من أراد
    الإرسال من الطرفية فليكتب الأمر بنفسه، فيبقى القرار قراراً لا أثراً
    جانبياً لتشغيلة في الخلفية.
    """
    acc = acc or account()
    payload = {
        "fromAddress": acc["address"], "toAddress": to, "subject": subject,
        "content": body, "mailFormat": "plaintext", "encoding": "UTF-8",
    }
    if cc:
        payload["ccAddress"] = cc
    return _req(f"{API}/accounts/{acc['accountId']}/messages", "POST",
                body=payload, token=token())


# ======================
# القراءة
# ======================
def folders(acc: dict | None = None) -> list[dict]:
    acc = acc or account()
    out = _req(f"{API}/accounts/{acc['accountId']}/folders", token=token())
    return out.get("data") or []


def inbox(limit: int = 50, acc: dict | None = None) -> list[dict]:
    """آخر رسائل الوارد."""
    acc = acc or account()
    fid = next((f.get("folderId") for f in folders(acc)
                if (f.get("folderName") or "").lower() == "inbox"), None)
    q = urllib.parse.urlencode({"limit": limit, "folderId": fid} if fid
                               else {"limit": limit})
    out = _req(f"{API}/accounts/{acc['accountId']}/messages/view?{q}",
               token=token())
    return out.get("data") or []


def push_drafts(limit: int = 20, path: str | None = None) -> dict:
    """
    يرفع المسودات المحفوظة محلياً إلى صندوق مسوداتك في Zoho.

    يتخطّى ما لا عنوان له: رسالة بلا مرسَل إليه لا تُحفظ مسودةً صالحة،
    ويُذكر ذلك في التقرير بدل ابتلاعه.
    """
    import crm
    import outreach

    p = path or crm.DB
    acc = account()
    done, skipped = [], []

    for m in outreach.drafts(path=p)[:limit]:
        if not (m.get("to_addr") or "").strip():
            skipped.append(f"#{m['id']} {m.get('company') or ''}: بلا بريد")
            continue
        try:
            save_draft(m["to_addr"], m["subject"], m["body"], acc=acc)
            con = crm.db(p)
            con.execute("UPDATE crm_messages SET status='approved' WHERE id=?",
                        (m["id"],))
            con.commit()
            con.close()
            done.append(f"#{m['id']} → {m['to_addr']}")
        except Exception as e:
            skipped.append(f"#{m['id']}: {type(e).__name__}: {e}")

    return {"account": acc["address"], "pushed": done, "skipped": skipped}


def sync_replies(limit: int = 50, path: str | None = None) -> dict:
    """
    يطابق وارد بريدك بجهات الـCRM ويسجّل الردود.

    المطابقة بعنوان المرسِل لا بالموضوع: الردّ يغيّر الموضوع أحياناً
    («Re:» أو ترجمة تلقائية) ولا يغيّر عنوانه. ومن ردّ تنتقل صفقته إلى
    «مستندات» لأن الردّ نفسه تقدّم، وتُلغى متابعته المجدولة.
    """
    import crm

    p = path or crm.DB
    con = crm.db(p)
    known = {}
    for r in con.execute(
            "SELECT k.email, k.company_id FROM crm_contacts k WHERE k.email<>''"):
        known[(r["email"] or "").lower().strip()] = r["company_id"]
    con.close()

    if not known:
        return {"error": "لا عناوين مسجّلة في الـCRM بعد"}

    matched, logged = [], 0
    for msg in inbox(limit=limit):
        frm = (msg.get("fromAddress") or "").lower().strip()
        cid = known.get(frm)
        if not cid:
            continue

        con = crm.db(p)
        deal = con.execute(
            "SELECT id, stage FROM crm_deals WHERE company_id=?"
            " ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        if not deal:
            con.close()
            continue

        exists = con.execute(
            "SELECT 1 FROM crm_messages WHERE deal_id=? AND direction='in'"
            " AND subject=?", (deal["id"], msg.get("subject") or "")).fetchone()
        if exists:
            con.close()
            continue

        con.execute(
            "INSERT INTO crm_messages(deal_id,direction,kind,subject,body,"
            "status,to_addr,created_at) VALUES(?,'in','reply',?,?,'sent',?,?)",
            (deal["id"], msg.get("subject") or "",
             (msg.get("summary") or "")[:2000], frm, crm._now()))
        con.commit()
        con.close()
        logged += 1

        if deal["stage"] in ("مرصودة", "مؤهَّلة", "تواصَلنا"):
            crm.set_stage(deal["id"], "مستندات",
                          note=f"ردّ من {frm}", path=p)
            con = crm.db(p)
            con.execute("UPDATE crm_deals SET next_action=NULL, next_at=NULL"
                        " WHERE id=?", (deal["id"],))
            con.commit()
            con.close()
        matched.append(f"{frm} · {(msg.get('subject') or '')[:44]}")

    return {"scanned": limit, "logged": logged, "matched": matched}


def setup_hint() -> str:
    cid = "✓" if os.getenv("ZOHO_CLIENT_ID") else "✗"
    sec = "✓" if os.getenv("ZOHO_CLIENT_SECRET") else "✗"
    ref = "✓" if os.getenv("ZOHO_REFRESH_TOKEN") else "✗"
    return "\n".join([
        f"مركز البيانات : zoho.{DC}   (بدّله بـ ZOHO_DC إن كان حسابك غير ذلك)",
        f"CLIENT_ID     : {cid}",
        f"CLIENT_SECRET : {sec}",
        f"REFRESH_TOKEN : {ref}",
        "",
        "الإعداد لمرة واحدة:",
        "  1) api-console.zoho.com ← Add Client ← Self Client",
        "  2) ضع Client ID و Secret في .env",
        f"  3) تبويب Generate Code · النطاق:\n     {SCOPES}",
        "     المدة 10 دقائق ← انسخ الرمز",
        "  4) python zoho.py auth <الرمز>",
    ])


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path())
    except ImportError:
        pass

    a = sys.argv[1:]
    try:
        if a and a[0] == "auth" and len(a) > 1:
            print(json.dumps(exchange_code(a[1]), ensure_ascii=False, indent=1))
        elif a and a[0] == "account":
            print(json.dumps(account(), ensure_ascii=False, indent=1))
        elif a and a[0] == "push":
            print(json.dumps(push_drafts(), ensure_ascii=False, indent=1))
        elif a and a[0] == "sync":
            print(json.dumps(sync_replies(), ensure_ascii=False, indent=1))
        elif a and a[0] == "inbox":
            for m in inbox(15):
                print(f"  {(m.get('fromAddress') or '')[:34]:<36} "
                      f"{(m.get('subject') or '')[:44]}")
        else:
            print(setup_hint())
    except Exception as e:
        print(f"خطأ: {e}")
        sys.exit(1)
