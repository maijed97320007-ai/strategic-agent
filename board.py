"""
مجلس الإدارة الشهري - يقرأ ما تراكم ويوصي.

    قاعدة المعرفة (فرص، منافسون، تنبؤات، تعارضات)
        ↓
    مجلس من سبعة أدوار (نفس غرفة الحرب)
        ↓
    مشاريع تُبدأ · مشاريع تُوقف · مخاطر · فرص · ميزانية · أولويات

الفرق عن غرفة الحرب: تلك تحكم على **قرار تطرحه أنت**، وهذا يقرأ ما رصده
النظام خلال الشهر ويقترح **ما يجب أن تقرّره**. المدخل هو التراكم لا السؤال.

لا يستدعي بحثاً ولا يولّد معرفة جديدة: يعمل على المخزون وحده، فهو رخيص
ويعمل بلا إنترنت.
"""
from __future__ import annotations

# مسار قاعدة المعرفة من memory: السلسلة النسبية تُحلّ حسب مجلد التشغيل،
# فتشغيل الـEXE من مجلد آخر يفتح قاعدة فارغة بصمت.
try:
    from memory import DB_DEFAULT as _DB
except ImportError:
    _DB = "knowledge.db"

import json
import sys
from datetime import date

BOARD_BRIEF = """أنت مجلس إدارة يجتمع شهرياً. أمامك ما رصده نظام الاستخبارات
خلال الفترة الماضية عن سوق هذه الشركة.

--- ملف الشركة ---
{profile}
--- نهاية ---

--- الفرص المرصودة (مرتّبة بالدرجة) ---
{opportunities}
--- نهاية ---

--- المنافسون وحركاتهم ---
{competitors}
--- نهاية ---

--- تنبؤات مفتوحة ---
{predictions}
--- نهاية ---

--- تعارضات في المعرفة (معلومات متضاربة تحتاج حسماً) ---
{conflicts}
--- نهاية ---

أصدر قرارات المجلس. كن محدداً - لا توصيات عامة.

أعد JSON صالحاً فقط:
{{"start": [{{"item": "مشروع يُبدأ", "why": "سبب مرتبط بما رُصد أعلاه",
              "first_step": "أول خطوة هذا الأسبوع", "score": 0-100}}],
  "stop": [{{"item": "نشاط يُوقف أو يُؤجّل", "why": "لماذا", "score": 0-100}}],
  "risks": [{{"item": "خطر يستحق انتباه المجلس", "why": "الآلية",
              "mitigation": "ما يقلّله", "score": 0-100}}],
  "budget": [{{"item": "بند إنفاق", "amount_omr": "نطاق تقديري",
               "why": "العائد المتوقع", "score": 0-100}}],
  "priorities": ["أولوية 1", "أولوية 2", "أولوية 3"],
  "verdict": "حكم المجلس في سطرين"}}

قواعد ملزِمة:
- كل توصية تستند لشيء **ورد أعلاه** لا لمعرفة عامة.
- `score` = إلحاح البند من 100.
- في `stop` اذكر ما يجب إيقافه فعلاً - مجلس لا يوقف شيئاً لم يجتمع.
- الميزانية بالريال العُماني وضمن سقف الشركة المذكور في ملفها."""


def gather(db: str = _DB) -> dict:
    """يجمع مخزون الفترة من القاعدة - قراءة فقط."""
    import dashboard
    import opportunity

    snap = dashboard.snapshot(db)
    return {
        "profile": opportunity.load_profile(),
        "opportunities": snap.get("opportunities", [])[:12],
        "competitors": snap.get("competitors", [])[:8],
        "changes": snap.get("recent_changes", [])[:8],
        "predictions": snap.get("predictions_open", [])[:8],
        "conflicts": snap.get("conflicts", [])[:8],
        "knowledge": snap.get("knowledge", {}),
        "accuracy": snap.get("accuracy", {}),
    }


def _fmt(data: dict) -> dict:
    """يحوّل المخزون إلى نصوص موجزة - لا نغرق النموذج بـJSON خام."""
    opp = "\n".join(
        f"- [{o['score']}] {o['band']} · {o['title'][:70]}"
        + (f"  ({o['company']})" if o.get("company") else "")
        for o in data["opportunities"]) or "- لا فرص مرصودة"

    comp_lines = [f"- {c['name']} · تهديد {c['threat']} · {c['moves']} حركة"
                  for c in data["competitors"]]
    comp_lines += [f"  · تغيّر: {c['name']} — {c['attribute']}: {c['value'][:40]}"
                   for c in data["changes"]]
    comp = "\n".join(comp_lines) or "- لا منافسين مسجّلين"

    pred = "\n".join(f"- [{p['probability']}%] {p['event'][:70]} (حتى {p['deadline']})"
                     for p in data["predictions"]) or "- لا تنبؤات مفتوحة"

    conf = "\n".join(f"- {c['entity'][:40]} · {c['field'][:26]}: "
                     f"{'، '.join(str(v)[:16] for v in c['values'][:3])}"
                     for c in data["conflicts"]) or "- لا تعارضات"

    return {"opportunities": opp, "competitors": comp,
            "predictions": pred, "conflicts": conf}


def convene(db: str = _DB, on_stage=None) -> dict:
    import main
    import pipeline

    def stage(m):
        if on_stage:
            on_stage(m)

    stage("قراءة مخزون الفترة...")
    data = gather(db)
    parts = _fmt(data)

    stage("المجلس يجتمع...")
    agents = main.build_agents()
    mk = agents.get("_rebuild")
    raw = pipeline._run_one(
        "BOARD", agents["SYN"],
        BOARD_BRIEF.format(
            profile=json.dumps(data["profile"], ensure_ascii=False)[:1800],
            **parts),
        mk("SYN") if mk else None)

    txt = (raw or "").strip()
    out = None
    for cand in (txt, pipeline._first_json_object(txt)):
        if not cand:
            continue
        try:
            out = json.loads(cand.strip("`").replace("json\n", "", 1))
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(out, dict):
        return {"error": "لم يُنتج المجلس قراراً صالحاً", "raw": txt[:400],
                "inventory": data}

    out["inventory"] = data
    out["date"] = date.today().isoformat()
    return out


def render(res: dict) -> str:
    if "error" in res:
        return f"  ⚠ {res['error']}"

    inv = res.get("inventory", {})
    k = inv.get("knowledge", {})
    out = ["", "=" * 58,
           f"  اجتماع مجلس الإدارة — {res.get('date','')}",
           "=" * 58,
           f"  المخزون: {len(inv.get('opportunities',[]))} فرصة · "
           f"{len(inv.get('competitors',[]))} منافس · "
           f"{len(inv.get('predictions',[]))} تنبؤ · "
           f"{k.get('facts',0)} حقيقة"]

    acc = inv.get("accuracy") or {}
    if acc.get("resolved"):
        out.append(f"  دقة التنبؤ: {acc['hit_rate']}% · {acc['calibration']}")

    sections = [("start", "مشاريع تُبدأ", True),
                ("stop", "أنشطة تُوقف أو تُؤجّل", False),
                ("risks", "مخاطر", False),
                ("budget", "ميزانية", False)]
    for key, label, show_step in sections:
        rows = sorted(res.get(key) or [], key=lambda x: -(x.get("score") or 0))
        if not rows:
            continue
        out += ["", f"  {label}", "  " + "-" * 54]
        for r in rows[:5]:
            amt = f"  ({r['amount_omr']})" if r.get("amount_omr") else ""
            out.append(f"   [{r.get('score',0):3d}] {r.get('item','')[:48]}{amt}")
            if r.get("why"):
                out.append(f"         {r['why'][:76]}")
            if show_step and r.get("first_step"):
                out.append(f"         ← {r['first_step'][:74]}")
            if r.get("mitigation"):
                out.append(f"         ↓ {r['mitigation'][:74]}")

    if pr := res.get("priorities"):
        out += ["", "  الأولويات", "  " + "-" * 54]
        out += [f"   {i}. {p[:70]}" for i, p in enumerate(pr[:5], 1)]

    if v := res.get("verdict"):
        out += ["", "-" * 58, f"  حكم المجلس: {v[:200]}"]
    return "\n".join(out)


def save(res: dict, out_dir: str | None = None) -> str:
    if out_dir is None:
        try:
            from main import out_dir_default
            out_dir = out_dir_default()
        except ImportError:
            out_dir = "output"
    """يحفظ محضر الاجتماع."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"board_{res.get('date', date.today())}.md")
    body = ["# محضر مجلس الإدارة", f"\n*{res.get('date','')}*\n"]
    for key, label in (("start", "مشاريع تُبدأ"), ("stop", "أنشطة تُوقف"),
                       ("risks", "مخاطر"), ("budget", "ميزانية")):
        rows = res.get(key) or []
        if not rows:
            continue
        body.append(f"\n## {label}\n")
        for r in sorted(rows, key=lambda x: -(x.get("score") or 0)):
            body.append(f"- **{r.get('item','')}** ({r.get('score',0)})  \n"
                        f"  {r.get('why','')}"
                        + (f"  \n  ← {r['first_step']}" if r.get("first_step") else "")
                        + (f"  \n  ↓ {r['mitigation']}" if r.get("mitigation") else ""))
    if pr := res.get("priorities"):
        body.append("\n## الأولويات\n")
        body += [f"{i}. {p}" for i, p in enumerate(pr, 1)]
    if v := res.get("verdict"):
        body.append(f"\n## حكم المجلس\n\n{v}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    return path


if __name__ == "__main__":
    res = convene(on_stage=lambda m: print("·", m, flush=True))
    print(render(res))
    if "error" not in res:
        print(f"\n  المحضر: {save(res)}")
