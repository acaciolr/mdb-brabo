"""
widgets/charts.py
Formatting helpers and Graph(Static) — a plotext time-series widget.
plotext is optional: without it Graph degrades to a unicode sparkline, so
the TUI still works on a minimal install.
"""
from __future__ import annotations

import contextlib

from rich.text import Text
from textual.widgets import Static

try:
    import plotext as plt
    _HAS_PLOTEXT = True
except ImportError:  # optional dependency
    plt = None
    _HAS_PLOTEXT = False

_SPARK = " ▁▂▃▄▅▆▇█"


class Graph(Static):
    DEFAULT_CSS = """
    Graph { height: 11; background: #0a0e1b; border: solid #2d4a3e; padding: 0; }
    """

    def __init__(self, label: str, color=(0, 237, 100), unit: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self.label = label
        self.color = color
        self.unit = unit
        self._values: list[float] = []

    def on_resize(self) -> None:
        self._draw()

    def update_data(self, values: list[float]) -> None:
        self._values = [float(v or 0) for v in values]
        self._draw()

    def _draw(self) -> None:
        if not self._values:
            self.update(Text(f"  {self.label}\n  (aguardando dados…)", style="dim"))
            return
        w, h = self.size.width, self.size.height
        if w < 8 or h < 4:
            return
        if not _HAS_PLOTEXT:
            last = self._values[-1]
            t = Text(f" {self.label}: {last:,.1f}{self.unit}\n ", style="bold")
            t.append_text(sparkline(self._values, max(8, w - 4)))
            self.update(t)
            return
        try:
            plt.clf()
            plt.canvas_color((10, 14, 27))
            plt.axes_color((10, 14, 27))
            plt.ticks_color((133, 180, 160))
            plt.plotsize(w, h)
            y = self._values[-w:]
            plt.plot(list(range(len(y))), y, marker="braille", label=self.label, color=self.color)
            lo, hi = min(y), max(y)
            pad = (hi - lo) * 0.05 if hi != lo else (abs(hi) * 0.1 or 1.0)
            lo, hi = max(0.0, lo - pad) if min(y) >= 0 else lo - pad, hi + pad
            with contextlib.suppress(Exception):
                plt.ylim(lo, hi)
            ticks = [lo + i * (hi - lo) / 4 for i in range(5)]
            dec = 0 if hi - lo >= 100 else (1 if hi - lo >= 10 else 2)
            plt.yticks(ticks, [f"{v:.{dec}f}{self.unit}" for v in ticks])
            plt.xticks([])
            self.update(Text.from_ansi(plt.build()))
        except Exception:
            pass


def sparkline(values, width: int = 30, color: str = "green") -> Text:
    if not values:
        return Text("─" * width, style="dim")
    recent = list(values)[-width:]
    mn, mx = min(recent), max(recent)
    rng = (mx - mn) or 1
    chars = "".join(_SPARK[min(8, int((v - mn) / rng * 8))] for v in recent)
    t = Text(" " * (width - len(chars)))
    t.append(chars, style=color)
    return t


def pct_bar(pct, width: int = 20, warn: float = 70, crit: float = 85) -> Text:
    p = min(max(float(pct or 0), 0), 100)
    filled = int(p / 100 * width)
    color = "green" if p < warn else ("yellow" if p < crit else "red")
    return Text("█" * filled + "░" * (width - filled) + f" {p:5.1f}%", style=color)


def fmt(val, decimals: int = 1, suffix: str = "") -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def fmt_int(val) -> str:
    if val is None:
        return "—"
    try:
        return f"{int(val):,}"
    except (TypeError, ValueError):
        return str(val)


def fmt_bytes(val) -> str:
    if val is None:
        return "—"
    try:
        n = float(val)
    except (TypeError, ValueError):
        return str(val)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} EB"


def fmt_duration(seconds) -> str:
    if seconds is None:
        return "—"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if s < 60:
        return f"{s:.0f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"
