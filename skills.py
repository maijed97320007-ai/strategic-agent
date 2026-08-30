"""
Agent Skills - المعيار المفتوح (agentskills.io).

مهارة = مجلد فيه SKILL.md، ورأسه YAML يحمل `name` و`description`.
عندما يطابق وصف المهارة موضوع التشغيلة، يُحقن نصّها الكامل في سياق الوكلاء.

لماذا هذا مهم هنا: خبرتك في المياه والتناضح العكسي لا تصل للنموذج اليوم إلا
عبر تعديل الكود. المهارة تجعلها ملفاً نصياً تكتبه وتعدّله بلا برمجة، ولا
يُحمَّل إلا حين يخصّ الموضوع - فلا يتضخّم السياق بلا داعٍ.

صفر تبعيات: قارئ YAML مصغّر يكفي لرأس بسيط من مفاتيح نصية.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


def _root() -> Path:
    """مجلد المهارات - بجانب الـEXE حين يكون مجمّداً، لا داخل حزمته."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).parent
    return Path(os.getenv("SKILLS_DIR", base / "skills"))


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    keywords: tuple[str, ...] = ()

    def matches(self, topic: str) -> float:
        """
        درجة تطابق بين 0 و1 بين المهارة والموضوع.

        مطابقة كلمات مطبَّعة عربياً لا تضمين متجهي: لا تحتاج نموذجاً ولا
        شبكة، وتكفي لعشرات المهارات. لو صارت مئات، انتقل لـ memory.search.
        """
        from memory import normalize

        hay = normalize(f"{self.name} {self.description} {' '.join(self.keywords)}")
        words = {w for w in normalize(topic).split() if len(w) > 2}
        if not words:
            return 0.0
        hit = sum(1 for w in words if w in hay)
        return hit / len(words)


_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


def _parse(text: str, path: Path) -> Skill | None:
    m = _FRONTMATTER.match(text)
    if not m:
        return None

    meta: dict[str, str] = {}
    key = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s+", line) and key:          # استمرار قيمة متعددة الأسطر
            meta[key] += " " + line.strip()
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            meta[key] = val.strip().strip("'\"")

    name = meta.get("name") or path.parent.name
    desc = meta.get("description", "")
    kws = tuple(k.strip() for k in re.split(r"[,،]", meta.get("keywords", "")) if k.strip())
    return Skill(name=name, description=desc, body=m.group(2).strip(),
                 path=path, keywords=kws)


def discover(root: Path | None = None) -> list[Skill]:
    """يقرأ كل skills/*/SKILL.md."""
    root = root or _root()
    if not root.is_dir():
        return []
    out = []
    for md in sorted(root.glob("*/SKILL.md")):
        try:
            s = _parse(md.read_text(encoding="utf-8"), md)
        except OSError:
            continue
        if s:
            out.append(s)
    return out


def select(topic: str, threshold: float = 0.2, limit: int = 3,
           root: Path | None = None) -> list[Skill]:
    """يعيد المهارات المطابقة للموضوع، الأقوى أولاً."""
    scored = [(s.matches(topic), s) for s in discover(root)]
    hits = sorted(((sc, s) for sc, s in scored if sc >= threshold),
                  key=lambda x: -x[0])
    return [s for _, s in hits[:limit]]


def as_context(skills: list[Skill], max_chars: int = 6000) -> str:
    """يحوّل المهارات المختارة إلى كتلة نصية تُحقن في وصف المهمة."""
    if not skills:
        return ""
    parts = ["## معرفة متخصصة مُحمَّلة",
             "التزم بما يلي - فهو خبرة ميدانية موثوقة تفوق معرفتك العامة:"]
    budget = max_chars
    for s in skills:
        chunk = f"\n### {s.name}\n{s.body}"
        if len(chunk) > budget:
            chunk = chunk[:budget] + "\n…(اقتُطعت)"
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 0:
            break
    return "\n".join(parts)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "list":
        for s in discover():
            print(f"- {s.name}\n    {s.description[:90]}\n    {s.path}")
        print(f"\nالمجموع: {len(discover())} مهارة في {_root()}")
    elif args:
        topic = " ".join(args)
        hits = select(topic)
        print(f"الموضوع: {topic}\nمطابقات: {len(hits)}")
        for s in hits:
            print(f"  [{s.matches(topic):.2f}] {s.name}")
    else:
        print("الاستخدام: python skills.py list | python skills.py <موضوع>")
