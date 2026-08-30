"""
واجهة طرفية للوكيل، مبنية على rich (تبعية موجودة أصلاً مع crewai).

مبادئ التصميم:
  · بلا فن ASCII للعربية - تباعد الحروف يكسر وصلها ويبدو رديئاً
  · شريط تقدّم واحد يجيب "كم بقي؟" فوراً، وقائمة مراحل تجيب "أين نحن؟"
  · لون واحد فعّال في كل لحظة: الذهبي للمرحلة الجارية فقط
  · كل مرحلة تحمل زمنها - يكشف الاختناق دون تشغيل أدوات قياس
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.align import Align
from rich.box import HEAVY, ROUNDED
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()

# لوحة مائية: من قاع البحر إلى الزبد
DEEP = "#0a2e42"
MID = "#17697a"
SURF = "#2fb3ba"
FOAM = "#8fdcd8"
GOLD = "#e0a63c"
DIM = "grey42"
FAINT = "grey30"

SPINNER = "⣾⣽⣻⢿⡿⣟⣯⣷"
DONE_MARK = "●"
IDLE_MARK = "○"


@dataclass
class Stage:
    label: str
    hint: str = ""
    state: str = "pending"          # pending | active | done
    seconds: float = 0.0
    note: str = ""


@dataclass
class Dashboard:
    """لوحة حيّة تعرض تقدّم خط الأنابيب."""

    model: str
    topic: str
    stages: list[Stage] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    _tick: int = 0
    _live: Live | None = None
    _active_since: float = 0.0

    # ---------- أجزاء العرض ----------
    # ملاحظة RTL: rich يرصف الأعمدة من اليسار دائماً. لإخراج تخطيط عربي
    # سليم نعكس ترتيب الأعمدة في الكود - فأول عمود مكتوب يظهر أقصى اليسار،
    # وآخر عمود يظهر أقصى اليمين حيث تبدأ عين القارئ العربي.
    def _title_bar(self) -> Panel:
        head = Table.grid(expand=True, padding=(0, 2))
        head.add_column(justify="left", width=8)      # يسار: الزمن
        head.add_column(justify="right", ratio=1)     # يمين: الموضوع
        head.add_row(
            Text(self._elapsed(), style=GOLD),
            Text(self.topic, style=f"bold {FOAM}", overflow="ellipsis"),
        )
        head.add_row(
            Text(f"{self._done_count()}/{len(self.stages)}", style=DIM),
            Text(self.model, style=FAINT, overflow="ellipsis"),
        )
        return Panel(head, box=ROUNDED, border_style=MID, padding=(0, 2))

    def _progress_bar(self):
        pct = self._done_count() / max(1, len(self.stages))
        bar = Progress(
            TextColumn(""),
            BarColumn(bar_width=None, complete_style=SURF,
                      finished_style=SURF, pulse_style=MID, style=FAINT),
            TextColumn("[{task.percentage:>3.0f}%]", style=DIM),
            expand=True,
        )
        bar.add_task("", total=1.0, completed=pct)
        return bar

    def _pipeline(self) -> Table:
        self._tick += 1
        spin = SPINNER[self._tick % len(SPINNER)]

        # الترتيب معكوس عمداً (RTL): الزمن يساراً، الاسم وسطاً، العلامة يميناً
        t = Table.grid(padding=(0, 2), expand=True)
        t.add_column(width=6, justify="left")       # الزمن
        t.add_column(ratio=1, justify="right")      # اسم المرحلة
        t.add_column(width=2, justify="center")     # العلامة

        for s in self.stages:
            if s.state == "done":
                mark, mstyle, lstyle = DONE_MARK, SURF, FOAM
                timing = f"{s.seconds:.0f}ث"
            elif s.state == "active":
                mark, mstyle, lstyle = spin, GOLD, f"bold {GOLD}"
                timing = f"{time.perf_counter() - self._active_since:.0f}ث"
            else:
                mark, mstyle, lstyle = IDLE_MARK, FAINT, FAINT
                timing = ""

            t.add_row(Text(timing, style=DIM),
                      Text(s.label, style=lstyle),
                      Text(mark, style=mstyle))
            if s.note and s.state != "pending":
                t.add_row("", Text(s.note, style=FAINT), "")
        return t

    def _render(self) -> Group:
        return Group(
            self._title_bar(),
            Text(""),
            self._progress_bar(),
            Text(""),
            self._pipeline(),
        )

    # ---------- أدوات ----------
    def _done_count(self) -> int:
        return sum(1 for s in self.stages if s.state == "done")

    def _elapsed(self) -> str:
        s = int(time.perf_counter() - self.started)
        return f"{s // 60:02d}:{s % 60:02d}"

    # ---------- التحكم ----------
    def __enter__(self):
        # الشعار طُبع في ask_topic - لا نكرّره هنا
        self._live = Live(self._render(), console=console, refresh_per_second=10)
        self._live.__enter__()
        return self

    def __exit__(self, *exc):
        for s in self.stages:               # لا نترك مرحلة معلّقة عند الخطأ
            if s.state == "active":
                s.state = "done"
                s.seconds = time.perf_counter() - self._active_since
        self.refresh()
        if self._live:
            self._live.__exit__(*exc)
        return False

    def refresh(self):
        if self._live:
            self._live.update(self._render())

    def start(self, index: int, note: str = ""):
        if 0 <= index < len(self.stages):
            self.stages[index].state = "active"
            self.stages[index].note = note
            self._active_since = time.perf_counter()
            self.refresh()

    def finish(self, index: int, note: str = ""):
        if 0 <= index < len(self.stages):
            s = self.stages[index]
            s.state = "done"
            s.seconds = time.perf_counter() - self._active_since
            if note:
                s.note = note
            self.refresh()

    def advance(self, note: str = ""):
        """ينهي المرحلة النشطة ويبدأ التالية."""
        cur = next((i for i, s in enumerate(self.stages) if s.state == "active"), None)
        if cur is None:
            if self.stages:
                self.start(0)
            return
        self.finish(cur, note)
        if cur + 1 < len(self.stages):
            self.start(cur + 1)


# ---------- شاشات مستقلة ----------
def wordmark() -> Group:
    """
    اسم التطبيق. نصّ عادي لا فن ASCII: تباعد الحروف العربية يكسر وصلها.
    """
    return Group(
        Text(""),
        Text.assemble(("  ◆  ", SURF),
                      ("وكيل الأفكار الاستراتيجية", f"bold {FOAM}")),
        Text("     سبعة وكلاء · بحث موثّق · حلول قابلة للتنفيذ", style=FAINT),
        Rule(style=MID),
    )


def ask_topic() -> str:
    console.print(wordmark())
    console.print(Text("  ما الموضوع الذي نشتغل عليه؟", style=f"bold {GOLD}"))
    console.print(Text("  اكتب موضوعاً أو فكرة، ثم Enter", style=FAINT))
    console.print()
    return console.input(f"[{SURF}]  ◆ [/]").strip()


def show_result(path: str, report: str, stats: dict, seconds: float) -> None:
    # RTL: العمود الأول (يسار) للقيمة، والثاني (يمين) لاسم الحقل.
    # بلا expand حتى تبقى الكتلة متراصة بدل تمدّدها لعرض اللوحة كله.
    t = Table.grid(padding=(0, 3))
    t.add_column(justify="right", style=f"bold {FOAM}")
    t.add_column(justify="right", style=DIM)

    rows = [("الملف", path),
            ("الحجم", f"{len(report):,} حرف"),
            ("الزمن", f"{int(seconds) // 60} د {int(seconds) % 60} ث")]
    if stats:
        sourced, total = stats.get("sourced_facts"), stats.get("facts")
        rows += [("حقائق مخزّنة", str(stats.get("stored", "-")))]
        if isinstance(sourced, int) and isinstance(total, int) and total:
            rows.append(("موثّقة بمصدر", f"{sourced} من {total}  ({100 * sourced // total}%)"))
        rows += [("كيانات", str(stats.get("entities", "-"))),
                 ("تعارضات مرصودة", str(stats.get("conflicts", "-")))]

    for k, v in rows:
        t.add_row(str(v), k)

    console.print()
    console.print(Panel(Align.right(t), box=ROUNDED, border_style=SURF,
                        padding=(1, 2),
                        title=Text(" اكتمل ", style=f"bold {DEEP} on {SURF}"),
                        title_align="right"))
    console.print(Text("  المصادر محذوفة من التقرير ومحفوظة في قاعدة المعرفة:", style=FAINT))
    console.print(Text("    python memory.py sources", style=DIM))
    console.print(Text("    python memory.py search <كلمة>", style=DIM))
    console.print()


def show_error(exc: BaseException) -> None:
    console.print()
    console.print(Panel(
        Text.assemble((f"{type(exc).__name__}\n", "bold red"), (str(exc), "red")),
        title=Text(" فشل التشغيل ", style="bold white on red"),
        title_align="right", box=HEAVY, border_style="red", padding=(1, 2),
    ))


def warn(msg: str) -> None:
    console.print()
    console.print(Panel(Text(msg, style=GOLD), box=ROUNDED, border_style=GOLD,
                        padding=(0, 2), title=Text(" تنبيه ", style=f"bold {GOLD}"),
                        title_align="right"))
