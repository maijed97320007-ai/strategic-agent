"""
رسومات SVG تُدرَج في التقارير.

ملاحظة RTL مهمة: نثبّت direction:ltr على عنصر <svg> صراحةً.

السبب: text-anchor يعمل بالمعنى **المنطقي** لا الفيزيائي. تحت اتجاه من
اليمين يصير "end" هو اليسار، فيمتد النص المرسى عند x=600 يميناً خارج
إطار عرضه 640 ويُقتطع. ولا يكفي حذف direction من الوسم لأن dir="rtl"
على <body> يتوارث إلى SVG عبر CSS. التثبيت الصريح يجعل الإرساء فيزيائياً،
والعربية تبقى موصولة ومقروءة لأن خوارزمية bidi تعمل داخل كل نص على حدة.

لماذا SVG مكتوب بالكود لا مكتبة رسم؟
  · صفر تبعيات - matplotlib يضيف 60MB للـEXE ولا يجيد العربية
  · يُدرَج مباشرة في HTML فيظهر في PDF بلا صورة وسيطة
  · نصّ خالص - قابل للفحص والتعديل، ويكبر بلا تشوّه

المخططات مختارة لما يُفهم بالصورة أسرع من الجدول:
  · مصفوفة الملاءمة/الأثر - موقع كل فكرة في مربعين
  · شريط جودة المصادر - على ماذا بُني التقرير فعلاً
  · مخطط خط الأنابيب - كيف وصلنا للنتيجة
  · محاكاة السيناريوهات - أثر كل متغيّر
"""
from __future__ import annotations

import html
import math

# ألوان متوافقة مع تصميم التقرير
DEEP, MID, SURF = "#0a2e42", "#17697a", "#2fb3ba"
GOLD, RED, GREY = "#c8922a", "#c0392b", "#8b8b8b"
LIGHT, LINE = "#f4fafb", "#dbe6ea"

FONT = "'Segoe UI','Noto Sans Arabic',Tahoma,sans-serif"


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _clip(text: str, width: int) -> str:
    """قصّ عند حدّ كلمة: «انخفاض الإنت» في المفتاح كان يقرأ كخطأ لا كاختصار."""
    t = " ".join(str(text).split())
    if len(t) <= width:
        return t
    cut = t[:width].rsplit(" ", 1)[0]
    return (cut if len(cut) >= width * 0.6 else t[:width]).rstrip("،, ") + "…"


def _wrap(text: str, width: int) -> list[str]:
    """يقسّم نصاً عربياً على أسطر - تقريب بعدد المحارف يكفي هنا."""
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = f"{cur} {w}".strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


# ======================
# 1) مصفوفة الأفكار
# ======================
def idea_matrix(items: list[dict], w: int = 640, h: int = 500) -> str:
    """
    كل فكرة نقطة: المحور الأفقي = الدرجة، الرأسي = قوة الإسناد.

    الربع الأعلى الأيمن (درجة عالية + إسناد قوي) هو ما يستحق التنفيذ،
    والأسفل الأيسر ما يجب إهماله. الموقع يقول ذلك أسرع من أي جدول.
    """
    if not items:
        return ""
    pad, plot_w, plot_h = 60, w - 120, h - 170
    x0, y0 = pad, 50

    def px(score):
        return x0 + plot_w * max(0, min(100, score)) / 100

    def py(ev):
        return y0 + plot_h - plot_h * max(0.0, min(1.0, ev))

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}" style="direction:ltr">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{w-pad}" y="28" text-anchor="end" font-size="15" '
        f'font-weight="600" fill="{DEEP}">خريطة الأفكار: الدرجة مقابل قوة الإسناد</text>',
        # أرباع
        f'<rect x="{x0}" y="{y0}" width="{plot_w/2}" height="{plot_h/2}" '
        f'fill="{LIGHT}" opacity="0.5"/>',
        f'<rect x="{x0+plot_w/2}" y="{y0}" width="{plot_w/2}" '
        f'height="{plot_h/2}" fill="{SURF}" opacity="0.12"/>',
        f'<line x1="{x0}" y1="{y0+plot_h/2}" x2="{x0+plot_w}" '
        f'y2="{y0+plot_h/2}" stroke="{LINE}" stroke-dasharray="4 4"/>',
        f'<line x1="{x0+plot_w/2}" y1="{y0}" x2="{x0+plot_w/2}" '
        f'y2="{y0+plot_h}" stroke="{LINE}" stroke-dasharray="4 4"/>',
        # محاور
        f'<line x1="{x0}" y1="{y0+plot_h}" x2="{x0+plot_w}" y2="{y0+plot_h}" '
        f'stroke="{DEEP}" stroke-width="1.5"/>',
        f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0+plot_h}" '
        f'stroke="{DEEP}" stroke-width="1.5"/>',
        f'<text x="{x0+plot_w/2}" y="{h-52}" text-anchor="middle" '
        f'font-size="12" fill="{MID}">الدرجة بعد التسجيل ←</text>',
        f'<text x="22" y="{y0+plot_h/2}" font-size="12" fill="{MID}" '
        f'transform="rotate(-90 22 {y0+plot_h/2})" text-anchor="middle">'
        f'قوة الإسناد ←</text>',
        f'<text x="{x0+plot_w-8}" y="{y0+18}" text-anchor="end" font-size="11" '
        f'fill="{SURF}" font-weight="600">نفّذ</text>',
        f'<text x="{x0+8}" y="{y0+plot_h-8}" font-size="11" fill="{GREY}">أهمل</text>',
    ]

    # كل العناصر تُرسم، لا أعلى 14 فقط: قصر الرسم على القمّة كان يكدّس
    # النقاط كلها في ربع واحد فيفقد التقسيم إلى أرباع معناه. المرقّمة هي
    # الخمس الأولى فقط - ما عداها نقاط صغيرة تُظهر التوزيع لا الهويّة.
    for it in items[5:]:
        cx, cy = px(it.get("score", 0)), py(it.get("evidence_weight", 0.5))
        s = it.get("score", 0)
        col = SURF if s >= 70 else (GOLD if s >= 45 else GREY)
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="4.5" '
                     f'fill="{col}" opacity="0.45"/>')

    for i, it in enumerate(items[:5], 1):
        cx, cy = px(it.get("score", 0)), py(it.get("evidence_weight", 0.5))
        s = it.get("score", 0)
        col = SURF if s >= 70 else (GOLD if s >= 45 else GREY)
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="9" fill="{col}" '
                     f'opacity="0.9"/>')
        parts.append(f'<text x="{cx:.0f}" y="{cy+4:.0f}" text-anchor="middle" '
                     f'font-size="10" fill="white" font-weight="600">{i}</text>')

    # مفتاح: القصّ عند 52 حرفاً كان يبتر الكلمة الأخيرة نصفين
    ly = y0 + plot_h + 58
    for i, it in enumerate(items[:5], 1):
        parts.append(f'<text x="{w-pad}" y="{ly + (i-1)*14}" text-anchor="end" '
                     f'font-size="10" fill="{GREY}">'
                     f'{i}. {_esc(_clip(str(it.get("idea", "")), 56))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ======================
# 2) جودة المصادر
# ======================
def source_quality(audit: dict, w: int = 640, h: int = 240) -> str:
    """شريط أفقي مكدّس: نسبة كل طبقة مصادر."""
    dist = (audit or {}).get("distribution") or {}
    if not dist:
        return ""
    try:
        from trust import TIERS
    except ImportError:
        return ""

    total = sum(dist.values()) or 1
    order = sorted(dist.items(), key=lambda x: -TIERS.get(x[0], (0,))[0])

    pad, bar_h, bar_y = 40, 40, 80
    bar_w = w - 2 * pad
    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}" style="direction:ltr">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{w-pad}" y="30" text-anchor="end" font-size="15" '
        f'font-weight="600" fill="{DEEP}">على ماذا بُني هذا التقرير</text>',
        f'<text x="{w-pad}" y="52" text-anchor="end" font-size="11" fill="{GREY}">'
        f'متوسط وزن المصادر {audit.get("avg_weight",0)} من 1.00</text>',
    ]

    # نرسم من اليمين لليسار
    x = w - pad
    for tier, n in order:
        weight = TIERS.get(tier, (0.45, tier))[0]
        seg = bar_w * n / total
        col = (SURF if weight >= 0.8 else MID if weight >= 0.6
               else GOLD if weight >= 0.45 else RED)
        parts.append(f'<rect x="{x-seg:.1f}" y="{bar_y}" width="{seg:.1f}" '
                     f'height="{bar_h}" fill="{col}"/>')
        if seg > 34:
            parts.append(f'<text x="{x-seg/2:.0f}" y="{bar_y+25}" '
                         f'text-anchor="middle" font-size="12" fill="white" '
                         f'font-weight="600">{n}</text>')
        x -= seg

    ly = bar_y + bar_h + 26
    for tier, n in order[:5]:
        weight, name = TIERS.get(tier, (0.45, tier))
        col = (SURF if weight >= 0.8 else MID if weight >= 0.6
               else GOLD if weight >= 0.45 else RED)
        parts.append(f'<rect x="{w-pad-12}" y="{ly-9}" width="10" height="10" fill="{col}"/>')
        parts.append(f'<text x="{w-pad-20}" y="{ly}" text-anchor="end" '
                     f'font-size="11" fill="{DEEP}">{_esc(name)} — {n}</text>')
        ly += 17
    parts.append("</svg>")
    return "\n".join(parts)


# ======================
# 3) خط الأنابيب
# ======================
def pipeline_flow(stages: list[tuple[str, float]], w: int = 640) -> str:
    """شريط زمني: عرض كل مرحلة يعكس زمنها الفعلي."""
    if not stages:
        return ""
    total = sum(max(0.1, s[1]) for s in stages) or 1
    h, pad, bar_y, bar_h = 190, 40, 66, 34
    bar_w = w - 2 * pad

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}" style="direction:ltr">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{w-pad}" y="30" text-anchor="end" font-size="15" '
        f'font-weight="600" fill="{DEEP}">أين ذهب الوقت</text>',
        f'<text x="{w-pad}" y="50" text-anchor="end" font-size="11" fill="{GREY}">'
        f'الإجمالي {int(total)//60} د {int(total)%60} ث</text>',
    ]
    x = w - pad
    palette = [SURF, MID, DEEP, GOLD, "#3d8f7a", "#7a5c9e", MID, SURF, GOLD, RED]
    for i, (name, secs) in enumerate(stages):
        seg = bar_w * max(0.1, secs) / total
        col = palette[i % len(palette)]
        parts.append(f'<rect x="{x-seg:.1f}" y="{bar_y}" width="{max(1,seg-1):.1f}" '
                     f'height="{bar_h}" fill="{col}" rx="2"/>')
        if seg > 40:
            parts.append(f'<text x="{x-seg/2:.0f}" y="{bar_y+21}" '
                         f'text-anchor="middle" font-size="11" fill="white">'
                         f'{secs:.0f}ث</text>')
        x -= seg

    ly = bar_y + bar_h + 24
    for i, (name, secs) in enumerate(sorted(stages, key=lambda s: -s[1])[:4]):
        parts.append(f'<text x="{w-pad}" y="{ly}" text-anchor="end" font-size="11" '
                     f'fill="{DEEP}">{_esc(name[:38])} — {secs:.0f} ثانية</text>')
        ly += 17
    parts.append("</svg>")
    return "\n".join(parts)


# ======================
# 4) محاكاة السيناريوهات
# ======================
def scenario_chart(base: int, scenarios: list[dict], w: int = 640) -> str:
    """أعمدة أفقية: كل سيناريو وانحرافه عن الأساس."""
    rows = scenarios[:8]
    if not rows:
        return ""
    h = 90 + len(rows) * 30
    pad, lab_w = 30, 210
    plot_w = w - pad * 2 - lab_w
    x0 = pad

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}" style="direction:ltr">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{w-pad}" y="28" text-anchor="end" font-size="15" '
        f'font-weight="600" fill="{DEEP}">أثر كل سيناريو على احتمال النجاح</text>',
    ]
    bx = x0 + plot_w * base / 100
    parts.append(f'<line x1="{bx:.0f}" y1="46" x2="{bx:.0f}" y2="{h-16}" '
                 f'stroke="{GREY}" stroke-dasharray="3 3"/>')
    parts.append(f'<text x="{bx:.0f}" y="42" text-anchor="middle" font-size="10" '
                 f'fill="{GREY}">الأساس {base}%</text>')

    y = 58
    for s in rows:
        p = max(0, min(100, s.get("probability", 0)))
        d = s.get("delta", 0)
        bw = plot_w * p / 100
        col = SURF if d > 3 else RED if d < -3 else GREY
        parts.append(f'<rect x="{x0}" y="{y}" width="{bw:.1f}" height="18" '
                     f'fill="{col}" opacity="0.85" rx="2"/>')
        parts.append(f'<text x="{x0+bw+6:.0f}" y="{y+13}" font-size="11" '
                     f'fill="{DEEP}">{p}%</text>')
        parts.append(f'<text x="{w-pad}" y="{y+13}" text-anchor="end" '
                     f'font-size="11" fill="{DEEP}">'
                     f'{_esc(str(s.get("label",""))[:34])}</text>')
        y += 30
    parts.append("</svg>")
    return "\n".join(parts)


def as_markdown(svg: str, caption: str = "") -> str:
    """يغلّف SVG ليُدرَج في Markdown - pdf.py يمرّر HTML كما هو."""
    if not svg:
        return ""
    cap = (f'\n<p style="text-align:center;font-size:11px;color:#6b6862">'
           f'{_esc(caption)}</p>' if caption else "")
    return f'\n\n<div style="margin:6mm 0">\n{svg}{cap}\n</div>\n\n'
