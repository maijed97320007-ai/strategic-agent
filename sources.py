"""
سجل المصادر المرقّمة.

كل نتيجة بحث تأخذ معرّفاً ثابتاً (S1, S2, …) يحمل العنوان والرابط والمقتطف.
الوكلاء يستشهدون بالمعرّف `[S1]` لا بالرابط الخام.

لماذا هذا أقوى من الروابط المباشرة؟
  · قابل للتحقق آلياً - نتأكد أن كل معرّف مذكور موجود فعلاً في السجل،
    بينما عدّ الروابط لا يكشف رابطاً مخترعاً أو منسوباً للرقم الخطأ
  · موجز - النموذج يكتب [S3] لا رابطاً بطول 200 حرف، فيتوفّر سياق
  · قابل للطيّ - نعرض قائمة المصادر أو نخفيها دون لمس نص التقرير
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CITE = re.compile(r"\[(S\d+)\]")


@dataclass
class Source:
    id: str
    title: str
    url: str
    snippet: str
    query: str = ""

    def as_line(self) -> str:
        return (f"[{self.id}] {self.title}\n"
                f"      {self.snippet}\n"
                f"      {self.url}")


@dataclass
class Registry:
    items: list[Source] = field(default_factory=list)

    def add(self, title: str, url: str, snippet: str, query: str = "") -> Source:
        # نتجنّب تكرار الرابط نفسه بمعرّفين مختلفين
        for s in self.items:
            if s.url == url:
                return s
        s = Source(id=f"S{len(self.items) + 1}", title=(title or "").strip(),
                   url=(url or "").strip(), snippet=(snippet or "").strip(),
                   query=query)
        self.items.append(s)
        return s

    def get(self, sid: str) -> Source | None:
        return next((s for s in self.items if s.id == sid.upper()), None)

    @property
    def ids(self) -> set[str]:
        return {s.id for s in self.items}

    def as_block(self) -> str:
        """كتلة المصادر التي تُحقن في وصف المهام."""
        if not self.items:
            return "(لا توجد مصادر - صرّح بذلك في مخرجاتك)"
        return "\n\n".join(s.as_line() for s in self.items)

    def as_markdown(self) -> str:
        """قائمة المصادر لتذييل التقرير."""
        if not self.items:
            return ""
        rows = "\n".join(f"- **[{s.id}]** [{s.title}]({s.url})" for s in self.items)
        return f"\n\n---\n\n## المصادر\n\n{rows}\n"

    def cited(self, text: str) -> set[str]:
        """المعرّفات المستشهد بها في نص."""
        return {m.upper() for m in CITE.findall(text or "")}

    def validate(self, text: str) -> tuple[set[str], set[str]]:
        """
        يعيد (معرّفات صحيحة، معرّفات مخترعة).

        المخترعة هي الفائدة الحقيقية: نموذج يكتب [S9] وسجلّنا فيه 6 مصادر
        يكون قد لفّق إسناداً - وهذا ما لا يكشفه عدّ الروابط أبداً.
        """
        found = self.cited(text)
        return found & self.ids, found - self.ids

    def used(self, text: str) -> list[Source]:
        ok, _ = self.validate(text)
        return [s for s in self.items if s.id in ok]
