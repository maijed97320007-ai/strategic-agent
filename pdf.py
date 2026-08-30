"""
تحويل تقارير Markdown العربية إلى PDF.

المسار: Markdown ← HTML بتنسيق RTL ← Edge بوضع headless ← PDF

لماذا Edge لا مكتبة PDF؟ العربية تحتاج تشكيل حروف (letter shaping) واتجاه
ثنائي (bidi). مكتبات مثل reportlab وfpdf تطبع الحروف منفصلة ومقلوبة ما لم
تُضف arabic-reshaper وpython-bidi، ومع ذلك تنكسر الجداول والروابط.
محرّك المتصفح يفعل هذا كله بإتقان، وEdge مثبّت مع ويندوز 11 - فصفر تبعيات.
"""
from __future__ import annotations

import html as _html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }

:root {
  --deep: #0b3d5c; --mid: #1f7a8c; --surf: #3fb8bd;
  --ink: #16232b; --soft: #5b6b74; --line: #dbe6ea; --wash: #f4fafb;
}

* { box-sizing: border-box; }

body {
  direction: rtl; text-align: right;
  font-family: "Segoe UI", "Tahoma", "Arial", sans-serif;
  font-size: 10.5pt; line-height: 1.85; color: var(--ink);
  margin: 0; padding: 0;
}

h1 {
  font-size: 21pt; color: var(--deep); line-height: 1.35;
  margin: 0 0 4mm; padding-bottom: 3mm;
  border-bottom: 2.5pt solid var(--surf);
}
h2 {
  font-size: 14.5pt; color: var(--mid); margin: 9mm 0 3mm;
  padding-right: 4mm; border-right: 3.5pt solid var(--surf);
  break-after: avoid;
}
h3 {
  font-size: 12pt; color: var(--deep); margin: 6mm 0 2mm;
  break-after: avoid;
}
h4 { font-size: 10.5pt; color: var(--soft); margin: 4mm 0 1.5mm; }

p { margin: 0 0 3mm; }
strong { color: var(--deep); }
em { color: var(--soft); }

ul, ol { margin: 0 0 3mm; padding-right: 6mm; padding-left: 0; }
li { margin-bottom: 1.2mm; }

/* الجداول تحتاج كسراً نظيفاً بين الصفحات */
table {
  width: 100%; border-collapse: collapse; margin: 3mm 0 5mm;
  font-size: 9.5pt; break-inside: auto;
}
thead { display: table-header-group; }
tr { break-inside: avoid; }
th {
  background: var(--deep); color: #fff; font-weight: 600;
  padding: 2mm 2.5mm; text-align: right; border: 0.4pt solid var(--deep);
}
td { padding: 1.8mm 2.5mm; border: 0.4pt solid var(--line); vertical-align: top; }
tbody tr:nth-child(even) { background: var(--wash); }

blockquote {
  margin: 3mm 0; padding: 2.5mm 4mm; background: var(--wash);
  border-right: 3pt solid var(--surf); color: var(--soft);
}

code {
  font-family: "Consolas", "Courier New", monospace; font-size: 9pt;
  background: var(--wash); padding: 0.4mm 1.2mm; border-radius: 2px;
  direction: ltr; display: inline-block;
}
pre {
  background: var(--wash); border: 0.4pt solid var(--line);
  padding: 3mm; overflow-x: auto; direction: ltr; text-align: left;
  break-inside: avoid;
}
pre code { background: none; padding: 0; }

hr { border: 0; border-top: 0.6pt solid var(--line); margin: 6mm 0; }

/* الروابط تُطبع بنصّها الكامل حتى تبقى المصادر قابلة للتحقق على الورق */
a { color: var(--mid); text-decoration: none; word-break: break-all; }
a[href^="http"]::after {
  content: " (" attr(href) ")";
  font-size: 7.5pt; color: var(--soft); direction: ltr;
  unicode-bidi: embed; word-break: break-all;
}

.meta {
  color: var(--soft); font-size: 9pt; margin: 0 0 6mm;
  padding: 2mm 3mm; background: var(--wash); border-radius: 3px;
}
"""

_SHELL = """<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<title>{title}</title><style>{css}</style></head>
<body>{body}</body></html>"""


def _render_markdown(md: str) -> str:
    """يحوّل Markdown إلى HTML، مع جداول GFM."""
    try:
        from markdown_it import MarkdownIt
        return (MarkdownIt("commonmark")
                .enable(["table", "strikethrough"])
                .render(md))
    except ImportError:
        return _fallback_render(md)


def _fallback_render(md: str) -> str:
    """محوّل بدائي إن غابت markdown-it - يغطي العناوين والقوائم والفقرات."""
    out, in_list = [], False
    for line in md.splitlines():
        s = line.rstrip()
        if m := re.match(r"^(#{1,6})\s+(.*)", s):
            if in_list:
                out.append("</ul>"); in_list = False
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_html.escape(m.group(2))}</h{lvl}>")
        elif re.match(r"^\s*[-*]\s+", s):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_html.escape(re.sub(r'^\s*[-*]\s+', '', s))}</li>")
        elif not s.strip():
            if in_list:
                out.append("</ul>"); in_list = False
        else:
            if in_list:
                out.append("</ul>"); in_list = False
            out.append(f"<p>{_html.escape(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def find_browser() -> str | None:
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "chromium"):
        if found := shutil.which(name):
            return found
    return None


def markdown_to_html(md: str, title: str = "تقرير") -> str:
    body = _render_markdown(md)
    return _SHELL.format(title=_html.escape(title), css=_CSS, body=body)


def markdown_to_pdf(md_path: str | Path, pdf_path: str | Path | None = None,
                    keep_html: bool = False) -> Path:
    """
    يحوّل ملف Markdown إلى PDF.

    يرمي RuntimeError إن لم يوجد متصفح - ملف HTML يبقى محفوظاً عندها
    ليطبعه المستخدم يدوياً.
    """
    md_path = Path(md_path)
    pdf_path = Path(pdf_path) if pdf_path else md_path.with_suffix(".pdf")
    md = md_path.read_text(encoding="utf-8")

    title = next((l.lstrip("# ").strip() for l in md.splitlines()
                  if l.startswith("# ")), md_path.stem)
    html_doc = markdown_to_html(md, title)

    html_path = (md_path.with_suffix(".html") if keep_html
                 else Path(tempfile.gettempdir()) / f"_rpt_{os.getpid()}.html")
    html_path.write_text(html_doc, encoding="utf-8")

    browser = find_browser()
    if not browser:
        raise RuntimeError(
            f"لم يُعثر على Edge أو Chrome. حُفظ HTML في {html_path} - "
            "افتحه واطبعه إلى PDF يدوياً (Ctrl+P)."
        )

    # user-data-dir منفصل حتى لا يتصادم مع نسخة Edge المفتوحة عند المستخدم.
    # ونكتب لمسار لاتيني مؤقت ثم ننقل: Edge يفشل صامتاً (رمز 0 بلا ملف)
    # عندما يحتوي مسار --print-to-pdf حروفاً عربية.
    with tempfile.TemporaryDirectory(prefix="edgepdf_") as profile:
        staged = Path(profile) / "report.pdf"
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={staged}",
            html_path.resolve().as_uri(),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if staged.exists() and staged.stat().st_size > 0:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged, pdf_path)

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError(
            f"فشل توليد PDF (رمز {r.returncode}). "
            f"{(r.stderr or '').strip()[:200]} | HTML محفوظ في {html_path}"
        )

    if not keep_html:
        html_path.unlink(missing_ok=True)
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("الاستخدام: python pdf.py <ملف.md> [مخرج.pdf]")
        sys.exit(1)
    try:
        out = markdown_to_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
        print(f"تم: {out}  ({out.stat().st_size / 1024:.0f} كيلوبايت)")
    except Exception as e:
        print(f"فشل: {e}")
        sys.exit(1)
