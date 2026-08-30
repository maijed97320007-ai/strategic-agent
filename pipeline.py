"""
خط الأنابيب: بحث ← 7 وكلاء في موجتين ← أدلة ← تسجيل ← تركيب ← تحقق إسناد ← تقرير

    SEARCH  (مصادر مرقّمة S1..Sn)
      ↓
    ┌────┬────┬────┬────┐          الموجة 1 - كلها من المصادر مباشرة
    A1   A2   A3   A4
    └────┴────┴────┴────┘
      ↓
    ┌────┬────┬────┐               الموجة 2 - من مخرجات الموجة 1
    A5   A6   A7
    └────┴────┴────┘
      ↓
    Evidence → Scoring → Synthesis → Citation Validation → Final Report

قيد في مجدول crewai اقتضى ثلاث دفعات منفصلة: المهام غير المتزامنة
المتتالية تُطلق دفعة واحدة (يُنشأ الـfuture فوراً)، فلو وضعنا الموجتين في
طاقم واحد لبدأت الثانية قبل أن تكتمل الأولى. كل موجة طاقم مستقل، وانتهاء
kickoff هو الحاجز الطبيعي بينهما.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import sources as S

# ======================
# مخطط المخرجات
# ======================
SCHEMA = """أعد **JSON صالحاً فقط** بلا أي نص قبله أو بعده، بهذا الشكل:

{"items": [
  {"idea": "عنوان الفكرة أو الملاحظة في سطر واحد",
   "detail": "شرح من 2-4 جمل",
   "score": 0-100,
   "risks": ["خطر ملموس", "..."],
   "evidence": ["S1", "S4"],
   "counterarguments": ["حجة مضادة وجيهة", "..."]}
]}

قواعد ملزِمة:
- `evidence` معرّفات من قائمة المصادر أعلاه حصراً. لا تخترع معرّفاً غير موجود.
- إن لم تجد سنداً لعنصر، اترك `evidence` فارغة بدل تلفيقها - الفراغ مقبول والتلفيق مرفوض.
- `score` تقديرك لقوة العنصر: 0-40 ضعيف، 41-70 معقول، 71-100 قوي.
- اكتب المحتوى بالعربية، والمفاتيح بالإنجليزية كما هي."""


@dataclass
class Item:
    """عنصر واحد من مخرجات وكيل."""
    idea: str
    detail: str = ""
    score: int = 0
    risks: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    agent: str = ""
    bad_evidence: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.evidence)


def parse_items(raw: str, agent: str, reg: S.Registry) -> list[Item]:
    """
    يستخرج العناصر من مخرَج الوكيل ويفصل الأدلة الصحيحة عن المخترعة.

    النماذج تغلّف JSON بأسوار ```json أو تسبقه بمقدمة، فنلتقط أول كتلة
    متوازنة الأقواس بدل الاعتماد على نظافة المخرَج.
    """
    txt = (raw or "").strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.M).strip()

    data = None
    for cand in (txt, _first_json_object(txt)):
        if not cand:
            continue
        try:
            data = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue

    if not isinstance(data, dict):
        # إنقاذ المبتور: وكيل تجاوز سقف الرموز يترك JSON غير مغلق فيضيع
        # عمله كاملاً. رُصد فعلياً - A7 أنتج 132 ألف حرف ولم يُحلَّل منها شيء.
        # نلتقط الكائنات المكتملة داخله ونهمل الأخير الناقص.
        salvaged = _salvage_objects(txt)
        if not salvaged:
            return []
        data = {"items": salvaged}

    out: list[Item] = []
    for d in data.get("items", []) or []:
        if not isinstance(d, dict) or not str(d.get("idea", "")).strip():
            continue
        ev_raw = [str(x).upper().strip("[] ") for x in (d.get("evidence") or [])]
        good = [e for e in ev_raw if e in reg.ids]
        bad = [e for e in ev_raw if e not in reg.ids]
        try:
            score = max(0, min(100, int(d.get("score") or 0)))
        except (TypeError, ValueError):
            score = 0
        out.append(Item(
            idea=str(d["idea"]).strip(), detail=str(d.get("detail", "")).strip(),
            score=score,
            risks=[str(x) for x in (d.get("risks") or []) if str(x).strip()],
            evidence=good, bad_evidence=bad,
            counterarguments=[str(x) for x in (d.get("counterarguments") or [])
                              if str(x).strip()],
            agent=agent,
        ))
    return out


def _salvage_objects(text: str, cap: int = 60) -> list[dict]:
    """
    يستخرج كائنات JSON مكتملة من نص مبتور.

    يمسح النص محرفاً محرفاً متتبّعاً عمق الأقواس خارج السلاسل، فيلتقط كل
    كائن أُغلق ويهمل ما بقي مفتوحاً عند نقطة القطع.
    """
    # مكدّس بدايات الأقواس: العناصر تقع داخل "items" أي على عمق ≥ 1،
    # فالاكتفاء بالعمق صفر يفوّتها كلها.
    out: list[dict] = []
    stack: list[int] = []
    in_str = esc = False

    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            try:
                obj = json.loads(text[start:i + 1])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and str(obj.get("idea", "")).strip():
                out.append(obj)
                if len(out) >= cap:
                    break
    return out


def _first_json_object(text: str) -> str | None:
    """أول كائن JSON متوازن الأقواس في النص."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# ======================
# التسجيل
# ======================
def rescore(items: list[Item], reg: S.Registry) -> None:
    """
    يعدّل درجات الوكلاء بعوامل موضوعية لا يملكها النموذج عن نفسه.

    النموذج يمنح نفسه درجات متفائلة ولا يعرف كم مصدراً يشترك مع غيره.
    نعاقب غير المسنَد ونكافئ ما تعدّدت أدلته أو تلاقى عليه أكثر من وكيل.
    """
    seen: dict[str, int] = {}
    for it in items:
        key = it.idea[:40]
        seen[key] = seen.get(key, 0) + 1

    # جودة المصدر لا عدده: فكرة مسنَدة لورقة محكّمة أقوى من فكرة بأربع
    # مدونات مورّدين. المحكّم المستقل رصد هذا الخلل صراحة (4/10 استناد).
    try:
        import trust
    except ImportError:
        trust = None

    for it in items:
        s = float(it.score)
        if not it.evidence:
            s *= 0.55                        # بلا سند - العقوبة الأقسى
        else:
            s *= min(1.25, 1 + 0.08 * len(it.evidence))
            if trust:
                urls = [src.url for e in it.evidence
                        if (src := reg.get(e)) is not None]
                q = trust.evidence_weight(urls)      # 0..1
                # 0.75 → محايد، أعلى يكافئ وأدنى يعاقب حتى الثلث
                s *= max(0.65, min(1.20, 0.65 + 0.73 * q))
        if it.bad_evidence:
            s *= 0.5                         # لفّق إسناداً - أشد من غياب السند
        if it.counterarguments:
            s *= 1.05                        # واجه نفسه بحجة مضادة
        if seen.get(it.idea[:40], 1) > 1:
            s *= 1.1                         # التقى عليه أكثر من وكيل
        it.raw = s

    # القصّ عند 100 كان يسوّي أعلى الأفكار كلها عند نفس الرقم: أربع عشرة
    # فكرة بدرجة 100 لا ترتّب شيئاً، وخريطة الأفكار كانت تكدّسها في نقطة
    # واحدة. نُعيد التحجيم بدل القصّ فيبقى الترتيب والتباعد قائمين.
    hi = max((it.raw for it in items), default=0.0)
    k = 100.0 / hi if hi > 100 else 1.0
    for it in items:
        it.score = max(0, min(100, round(it.raw * k)))
        del it.raw


# ======================
# التحقق من الإسناد
# ======================
@dataclass
class Audit:
    total: int = 0
    grounded: int = 0
    fabricated: int = 0
    fabricated_ids: set[str] = field(default_factory=set)
    used_sources: int = 0
    available_sources: int = 0

    @property
    def coverage(self) -> float:
        return self.grounded / self.total if self.total else 0.0

    def as_markdown(self) -> str:
        cov = f"{self.coverage * 100:.0f}%"
        rows = [
            f"| عناصر مُنتَجة | {self.total} |",
            f"| منها مُسنَدة لمصدر | {self.grounded} ({cov}) |",
            f"| مصادر استُخدمت فعلاً | {self.used_sources} من {self.available_sources} |",
        ]
        if self.fabricated:
            ids = "، ".join(sorted(self.fabricated_ids))
            rows.append(f"| **إسنادات ملفّقة رُفضت** | **{self.fabricated}** ({ids}) |")
        return ("\n\n---\n\n## تحقّق الإسناد\n\n"
                "*فحص آلي: كل معرّف مصدر ذكره وكيل قُورن بسجل البحث الفعلي.*\n\n"
                "| المؤشر | القيمة |\n|---|---|\n" + "\n".join(rows) + "\n")


def audit(items: list[Item], reg: S.Registry) -> Audit:
    a = Audit(total=len(items), available_sources=len(reg.items))
    used: set[str] = set()
    for it in items:
        if it.evidence:
            a.grounded += 1
            used |= set(it.evidence)
        if it.bad_evidence:
            a.fabricated += len(it.bad_evidence)
            a.fabricated_ids |= set(it.bad_evidence)
    a.used_sources = len(used)
    return a


def validate_report(text: str, reg: S.Registry) -> tuple[str, set[str]]:
    """
    يزيل الاستشهادات الملفّقة من التقرير النهائي.

    الوكيل المركّب قد يخترع [S12] وسجلّنا فيه 7 مصادر. حذفها أصدق من
    تركها - إسناد كاذب أسوأ من غياب إسناد.
    """
    _, bad = reg.validate(text)
    for b in bad:
        text = text.replace(f"[{b}]", "")
    return re.sub(r"[ \t]{2,}", " ", text), bad


# ======================
# الوكلاء السبعة + المركّب
# ======================
# (اسم، دور، هدف، خلفية، حرارة، موجة)
ROSTER = [
    ("A1", "مستخرج الحقائق الموثّقة",
     "استخراج الحقائق والأرقام القابلة للتحقق من المصادر، كل واحدة بمعرّفها",
     "باحث صارم لا يكتب رقماً بلا معرّف مصدر. إن لم يجد الرقم في المصادر "
     "المرفقة قال 'غير متوفر' بدل أن يخمّن.", 0.2, 1),

    ("A2", "مولد الأفكار المضادة",
     "توليد أفكار معاكسة أو مشوّهة بذكاء لما تقوله المصادر",
     "مفكر غير تقليدي يؤمن أن أفضل الأفكار تولد من نقيضها. يكره التفكير الخطي.",
     0.9, 1),

    ("A3", "كاشف التناقضات",
     "اكتشاف تناقضات بين المصادر أو داخل السوق وتحويل كل تناقض إلى فرصة",
     "محلل يرى الفرص في التناقضات التي يتجاهلها الجميع. يستمتع بمصدرين "
     "يقولان عكس بعضهما.", 0.85, 1),

    ("A4", "مهندس السيناريوهات المتطرفة",
     "بناء سيناريوهات 'ماذا لو' جريئة وواقعية مبنية على فجوات المصادر",
     "مستقبلي عملي يفكر في الاحتمالات المتطرفة ثم يرجعها للواقع.", 0.9, 1),

    ("A5", "الناقد القاسي",
     "نقد كل ما أنتجته الموجة الأولى وإضافة المخاطر والحجج المضادة",
     "ناقد لا يرحم. يؤمن أن الفكرة الضعيفة تموت تحت النقد القوي والقوية "
     "تصير أقوى. لا يجامل.", 0.25, 2),

    ("A6", "محلل الجدوى الاقتصادية",
     "الحكم على واقعية الأرقام والتكاليف وحساب ما يلزم فعلاً",
     "محلل مالي يكره الأرقام المتفائلة بلا حساب. يسأل دائماً: من أين جاء "
     "هذا الرقم، وما الذي يجعله معقولاً في هذا السوق تحديداً؟", 0.3, 2),

    ("A7", "خبير الحلول الفردية",
     "استخراج ما ينفّذه فرد واحد برأس مال محدود بلا فريق ولا دعم حكومي",
     "رائد أعمال فردي عملي. يكره ما يحتاج مليارات أو موافقات وزارية. "
     "يسأل: ما الذي أبدأه الأسبوع القادم من بيتي بمالي الخاص؟", 0.5, 2),
]


def build_roster(model: str, agent_factory):
    """agent_factory(role, goal, backstory, temperature) -> Agent"""
    return {code: agent_factory(role, goal, back, temp)
            for code, role, goal, back, temp, _ in ROSTER}


def wave_of(code: str) -> int:
    return next(w for c, *_ , w in ((r[0], r[1], r[2], r[3], r[4], r[5]) for r in ROSTER)
                if c == code)


def brief(code: str, topic: str, reg: S.Registry, upstream: str = "",
          skills: str = "") -> str:
    """وصف المهمة لوكيل بعينه."""
    role = next(r[1] for r in ROSTER if r[0] == code)
    head = f"الموضوع: «{topic}»\nدورك: {role}\n"

    if wave_of(code) == 1:
        body = ("اعمل على المصادر التالية حصراً. استشهد بمعرّف كل مصدر "
                "تستند إليه.\n\n--- المصادر ---\n"
                f"{reg.as_block()}\n--- نهاية المصادر ---\n")
        counts = {"A1": "8-12 حقيقة", "A2": "8 أفكار مضادة",
                  "A3": "5-8 تناقضات", "A4": "5 سيناريوهات"}
        body += f"\nأنتج {counts.get(code, '5-8 عناصر')}.\n"
    else:
        body = ("مخرجات الموجة الأولى بين يديك. اعمل عليها، وحافظ على "
                "معرّفات المصادر التي تنقلها.\n\n"
                f"--- مخرجات سابقة ---\n{upstream}\n--- نهاية ---\n\n"
                f"قائمة المصادر للرجوع:\n{reg.as_block()}\n")
        extra = {
            "A5": "انتقد أقوى العناصر من 5 زوايا: الجدوى، التميز، المخاطر، "
                  "التوقيت، القابلية للتنفيذ. لكل عنصر تنتقده املأ risks "
                  "وcounterarguments بجدية.",
            "A6": "احكم على واقعية كل رقم ورد. ضع score منخفضاً لما لا سند "
                  "له، واذكر في risks أي تقدير مالي غير معقول ولماذا.",
            "A7": "استبعد كل ما يحتاج تمويلاً مؤسسياً أو موافقات أو فريقاً "
                  "أكبر من ثلاثة. لكل حل اذكر في detail: رأس المال بالدولار، "
                  "أول خطوة في الأسبوع الأول، ومصدر الدخل.",
        }
        body += f"\n{extra.get(code, '')}\n"

    return f"{head}\n{body}\n{skills}\n\n{SCHEMA}"


def upstream_text(items: list[Item], limit: int = 40) -> str:
    """يلخّص مخرجات الموجة الأولى نصاً للموجة الثانية."""
    top = sorted(items, key=lambda x: -x.score)[:limit]
    lines = []
    for it in top:
        ev = " ".join(f"[{e}]" for e in it.evidence) or "(بلا سند)"
        lines.append(f"- ({it.agent}) {it.idea} — {it.detail} {ev}")
    return "\n".join(lines)


RED_TEAM_BRIEF = """الموضوع: «{topic}»

دورك: **الفريق الأحمر**. لا تحسّن ولا تجمّل - مهمتك تدمير أقوى العناصر.

--- أقوى ما أنتجه الوكلاء ---
{items}
--- نهاية ---

قائمة المصادر:
{sources}

لكل عنصر قوي اسأل: **لماذا قد يفشل هذا؟**

ابحث تحديداً عن:
- افتراض ضمني لم يُصرَّح به وقد يكون خاطئاً
- رقم يبدو معقولاً لكنه لا يصمد أمام الحساب
- سبب فشل مشاريع مشابهة سابقاً
- ما الذي يجعل هذا مستحيلاً في السياق المحلي تحديداً

أنتج 6-10 عناصر. في `idea` اكتب سبب الفشل لا الفكرة، وفي `risks`
الآلية الدقيقة للانهيار، وفي `score` احتمال الفشل من 100 (لا جودة الفكرة).

{schema}"""


SYNTH_BRIEF = """الموضوع: «{topic}»

أمامك كل ما أنتجه سبعة وكلاء، مرتّباً بالدرجة بعد إعادة تسجيل موضوعية
(المسنَد يُكافأ، والملفَّق يُعاقب).

--- العناصر ---
{items}
--- نهاية ---

قائمة المصادر:
{sources}

اكتب تقريراً نهائياً بصيغة Markdown يحتوي:

1. `## ملخص تنفيذي` — 4-6 أسطر.
2. `## الأفكار النهائية` — 3-5 أفكار، كل واحدة `### ` بعنوانها، وتحتها:
   الوصف، الزاوية الاستراتيجية، أبرز خطر، وخطة تنفيذ من 3 خطوات.
3. `## ما يمكنك تنفيذه كفرد` — 4-6 حلول من مخرجات A7، لكل واحد:
   رأس المال بالدولار، أول خطوة عملية، ومصدر الدخل.
4. `## المخاطر والحجج المضادة` — جدول بأهم ما رصده النقد.

قواعد ملزِمة:
- استشهد بمعرّفات المصادر `[S1]` بجانب كل رقم أو ادعاء تنقله.
- لا تخترع معرّفاً غير موجود في القائمة أعلاه.
- ما لا سند له اكتب بجانبه (تقدير) صراحة.
- التاريخ اليوم {today} - أي جدول زمني يبدأ من {year} فما بعد."""


# ======================
# المنفّذ
# ======================

def _unfence(md: str) -> str:
    """
    المُركِّب يلفّ التقرير كلّه أحياناً في ```markdown ... ``` فيصير النصّ
    كتلة شيفرة في PDF: خطّ أحادي المسافة، بلا عناوين ولا جداول. نزيل
    السياج الخارجي فقط - أسيجة الشيفرة داخل النصّ تبقى كما هي.
    """
    lines = md.strip().splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return md
    tag = lines[0].strip()
    if not tag.startswith("```") or not tag[3:].isalpha() and tag != "```":
        return md
    return "\n".join(lines[1:-1]).strip()


def _charts(items, reg, stage_times) -> str:
    """
    مخططات تُدرَج في التقرير. تفشل بصمت: رسم ناقص لا يجوز أن يُسقط تقريراً.

    خريطة الأفكار تحتاج وزن الإسناد لكل عنصر، ونحسبه هنا من طبقات الثقة
    بدل تخزينه في Item - فالوزن مشتقّ لا أصيل.
    """
    try:
        import trust
        import visuals as V
    except ImportError:
        return ""

    try:
        top = sorted(items, key=lambda x: -x.score)
        pts = []
        for it in top:
            urls = [src.url for e in it.evidence if (src := reg.get(e)) is not None]
            pts.append({"idea": it.idea, "score": it.score,
                        "evidence_weight": trust.evidence_weight(urls)})

        out = V.as_markdown(V.idea_matrix(pts),
                            "كل نقطة فكرة - الربع الأعلى الأيمن يستحق التنفيذ")
        out += V.as_markdown(V.source_quality(trust.audit_registry(reg)))
        if stage_times:
            out += V.as_markdown(V.pipeline_flow(stage_times))
        return out
    except Exception:
        return ""


def _trust_md(reg) -> str:
    """جدول جودة المصادر - يُذيَّل بالتقرير ليرى القارئ على ماذا بُني."""
    try:
        import trust
        return trust.as_markdown(trust.audit_registry(reg))
    except Exception:
        return ""


def _ckpt_load(topic, model, stage_name):
    """يقرأ نقطة حفظ. الفشل صامت - غيابها يعني إعادة التنفيذ لا الانهيار."""
    try:
        import checkpoints
        return checkpoints.load(topic, model, stage_name)
    except Exception:
        return None


def _ckpt_save(topic, model, stage_name, data) -> None:
    try:
        import checkpoints
        checkpoints.save(topic, model, stage_name, data)
    except Exception:
        pass


def _run_one(code, agent, description, rebuild=None, tries: int = 3) -> str:
    """
    يشغّل وكيلاً واحداً في طاقم مستقل مع تجاوز تلقائي للمزوّد عند الفشل.

    rebuild(index) يعيد وكيلاً بمزوّد بديل. عند نفاد المزوّدات نعيد نصاً
    فارغاً بدل الرمي: ستة وكلاء ناجحين خير من إسقاط الموجة كلها.
    """
    from crewai import Crew, Process, Task

    last = None
    for attempt in range(tries):
        if attempt and rebuild:
            try:
                agent = rebuild(attempt)
            except Exception:
                break                      # نفدت المزوّدات
        task = Task(description=description,
                    expected_output="JSON صالح فيه مفتاح items",
                    agent=agent)
        try:
            Crew(agents=[agent], tasks=[task], process=Process.sequential,
                 verbose=False).kickoff()
            raw = getattr(getattr(task, "output", None), "raw", "") or ""
            if raw.strip():
                return raw
            last = "مخرَج فارغ"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if not rebuild:
            break
    return ""


def _run_wave(codes, agents, briefs, on_note=None) -> dict[str, str]:
    """
    يشغّل موجة متوازية ويعيد {code: raw_output}.

    التوازي بمَجمَع خيوط لا بـasync_execution: crewai يرفض أكثر من مهمة
    غير متزامنة واحدة في نهاية الطاقم
    (ValidationError: The crew must end with at most one asynchronous task)،
    وهو قيد يمنع موجة من أربعة. طاقم مستقل لكل وكيل يتفاداه ويعطي تحكّماً
    كاملاً في حدود التوازي - وهو مهم على نموذج مجاني محدود المعدل.

    فشل وكيل واحد يعيد نصاً فارغاً ولا يُسقط الموجة: ستة وكلاء ناجحين
    أفضل من لا شيء.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(codes)) as pool:
        mk = agents.get("_rebuild")
        futures = {pool.submit(_run_one, c, agents[c], briefs[c],
                               mk(c) if mk else None): c for c in codes}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                out[c] = fut.result()
            except Exception:
                out[c] = ""
            if on_note:
                on_note(c, len(out[c]))
    return out


def run(topic: str, reg: S.Registry, agents: dict, skills: str = "",
        on_stage=None, today: str = "", year: int = 0, model: str = "") -> dict:
    """
    ينفّذ خط الأنابيب كاملاً ويعيد قاموس النتيجة.

    on_stage(index, note) يُستدعى عند كل مرحلة لتغذية الواجهة.
    """
    def stage(i, note=""):
        if on_stage:
            on_stage(i, note)

    t0 = time.perf_counter()
    items: list[Item] = []
    stage_times: list[tuple[str, float]] = []
    _last = [t0]

    def mark(name: str) -> None:
        now = time.perf_counter()
        stage_times.append((name, now - _last[0]))
        _last[0] = now

    # ── الموجة 1 ──
    stage(2, "أربعة وكلاء متوازين")
    w1_codes = [r[0] for r in ROSTER if r[5] == 1]
    briefs = {c: brief(c, topic, reg, skills=skills) for c in w1_codes}
    raw1 = _ckpt_load(topic, model, "wave1")
    if raw1:
        stage(2, "مستأنَفة من نقطة حفظ")
    else:
        raw1 = _run_wave(w1_codes, agents, briefs)
        _ckpt_save(topic, model, "wave1", raw1)
    for c in w1_codes:
        items += parse_items(raw1[c], c, reg)
    stage(3, f"{len(items)} عنصر من الموجة الأولى")
    mark("الموجة الأولى")

    # ── الموجة 2 ──
    stage(4, "ثلاثة وكلاء متوازين")
    up = upstream_text(items)
    w2_codes = [r[0] for r in ROSTER if r[5] == 2]
    briefs2 = {c: brief(c, topic, reg, upstream=up, skills=skills) for c in w2_codes}
    raw2 = _ckpt_load(topic, model, "wave2")
    if raw2:
        stage(4, "مستأنَفة من نقطة حفظ")
    else:
        raw2 = _run_wave(w2_codes, agents, briefs2)
        _ckpt_save(topic, model, "wave2", raw2)
    w2_items: list[Item] = []
    for c in w2_codes:
        w2_items += parse_items(raw2[c], c, reg)
    items += w2_items
    stage(5, f"{len(w2_items)} عنصر من الموجة الثانية")
    mark("الموجة الثانية")

    # ── الأدلة والتسجيل ──
    rescore(items, reg)
    stage(5, f"{audit(items, reg).grounded}/{len(items)} عنصر مُسنَد")

    # ── الفريق الأحمر ──
    stage(6, "مهاجمة أقوى العناصر")
    strongest = sorted(items, key=lambda x: -x.score)[:15]
    red_brief = RED_TEAM_BRIEF.format(
        topic=topic, sources=reg.as_block(), schema=SCHEMA,
        items="\n".join(f"- [{i.score}] ({i.agent}) {i.idea} — {i.detail}"
                        for i in strongest))
    mk = agents.get("_rebuild")
    red_raw = _ckpt_load(topic, model, "red")
    if not red_raw:
        red_raw = _run_one("RED", agents["RED"], red_brief,
                           mk("RED") if mk else None)
        _ckpt_save(topic, model, "red", red_raw)
    red_items = parse_items(red_raw, "RED", reg)
    items += red_items
    rescore(items, reg)
    report_audit = audit(items, reg)
    stage(7, f"{len(red_items)} نقطة فشل محتملة")
    mark("الفريق الأحمر")

    # ── التركيب ──
    from crewai import Crew, Process, Task

    top = sorted(items, key=lambda x: -x.score)[:35]
    listing = "\n".join(
        f"- [{it.score:3d}] ({it.agent}) {it.idea} — {it.detail} "
        f"{' '.join('[' + e + ']' for e in it.evidence) or '(بلا سند)'}"
        + (f"  ⚠ مخاطر: {'; '.join(it.risks[:2])}" if it.risks else "")
        for it in top)

    synth_task = Task(
        description=SYNTH_BRIEF.format(topic=topic, items=listing,
                                       sources=reg.as_block(),
                                       today=today, year=year),
        expected_output="تقرير Markdown كامل بالأقسام الأربعة",
        agent=agents["SYN"],
    )
    stage(8, "دمج المرتّب")
    Crew(agents=[agents["SYN"]], tasks=[synth_task],
         process=Process.sequential, verbose=False).kickoff()
    body = getattr(getattr(synth_task, "output", None), "raw", "") or ""
    body = _unfence(body)

    # ── تحقق الإسناد ──
    mark("التركيب")
    body, fabricated = validate_report(body, reg)
    if fabricated:
        report_audit.fabricated += len(fabricated)
        report_audit.fabricated_ids |= fabricated
    stage(9, f"{len(fabricated)} إسناد ملفّق أُزيل" if fabricated else "الإسناد سليم")

    return {
        "report": (body + _charts(items, reg, stage_times)
                   + report_audit.as_markdown() + _trust_md(reg)
                   + reg.as_markdown()),
        "items": items,
        "audit": report_audit,
        "seconds": time.perf_counter() - t0,
    }
