import pathlib
import re

p = pathlib.Path("portals.py")
lines = p.read_text(encoding="utf-8").splitlines(True)

i = next(n for n, l in enumerate(lines) if l.startswith("_STRIP = re.compile("))

# النمط يُبنى من قطع: كتابته حرفياً في أدوات التحرير تبتلع المرجع الخلفي
pattern_line = (
    '    r"<(script|style|nav|footer|header|svg)"'
    ' r"\\b[^>]*>.*?</\\1>",\n'
)
lines[i + 1] = pattern_line
p.write_text("".join(lines), encoding="utf-8")

src = p.read_text(encoding="utf-8")
m = re.search(r"_STRIP = re\.compile\(\n(.*?)\n", src)
print("السطر:", repr(m.group(1)))

import portals  # noqa: E402
print("النمط المُصرَّف:", repr(portals._STRIP.pattern))
