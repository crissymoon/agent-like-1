"""The runs as pictures, so a reader sees the shape before reading the table.

Nine rows and five columns is read one cell at a time; the same numbers as bars
are read at once, and the reading a benchmark exists for is which task a solver
fails and whether the failure is the format, the plan, or the ending state.

The charts are written as SVG text here rather than by a plotting library. Three
reasons, in the order they matter: the notebook gains no dependency it did not
have, the file that leaves the run is a vector document that opens in a browser
and in a pull request and needs no runtime, and the geometry is stated in the
source, so every rectangle is drawn with ``rx`` and ``ry`` at zero. A bar is a
bar and not a pill: :data:`BORDER_RADIUS` is zero and every rectangle passes it.

Nothing here decides anything. A chart is a reading of the rows a run wrote, and
the rows stay the record.
"""

from __future__ import annotations

from pathlib import Path

#: Every rectangle is drawn with this radius, and it is zero on purpose: a
#: rounded bar reads as a decoration rather than a measurement, and a rounded
#: cell in the matrix reads as a status chip.
BORDER_RADIUS = 0

WIDTH = 940
LABEL_WIDTH = 250
ROW_GAP = 8
SUB_HEIGHT = 13
SUB_GAP = 2
TOP = 78
BOTTOM = 26
FOOTER_LINE = 16

AXIS_TICKS = (0, 25, 50, 75, 100)
MAXIMUM = 100.0

NUMBER_COLUMN = 52

BACKGROUND = "#ffffff"
BAR_FILL = "#2f5d8a"
BAR_EDGE = "#1d3b57"
GRID = "#d8d8d8"
AXIS = "#8a8a8a"
TEXT = "#202020"
MUTED = "#6a6a6a"
PASS_FILL = "#1f7a3d"
FAIL_FILL = "#b3261e"
MISSING_FILL = "#e4e4e4"

#: The colours the series cycle through. Stated rather than generated, so a
#: chart rebuilt from the same rows is the same picture.
SERIES_FILLS = (
    "#2f5d8a",
    "#8a6a2f",
    "#4a7a5a",
    "#7a3f6a",
    "#556070",
    "#8a4a3f",
    "#3f6f7a",
    "#6f6f3f",
)


def escape(text: object) -> str:
    """Return *text* with the three characters that end an SVG text node replaced."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _text(x: float, y: float, body: object, size: int = 11, anchor: str = "start", fill: str = TEXT, weight: str = "normal") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">'
        f"{escape(body)}</text>"
    )


def _rect(x: float, y: float, width: float, height: float, fill: str, edge: str = "") -> str:
    stroke = f' stroke="{edge}" stroke-width="0.6"' if edge else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'rx="{BORDER_RADIUS}" ry="{BORDER_RADIUS}" fill="{fill}"{stroke}/>'
    )


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str = GRID) -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="0.8"/>'


def _document(body: list[str], width: float, height: float, label: str = "") -> str:
    """Wrap drawn elements in one SVG document.

    *label* becomes the document's title element, which is what a reader without
    the image in front of them is given by an assistive tool.
    """
    header = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" shape-rendering="crispEdges">'
    )
    title = f"<title>{escape(label)}</title>" if label else ""
    background = _rect(0, 0, width, height, BACKGROUND)
    return "\n".join([header, title, background] + body + ["</svg>", ""])


def _truncate(text: object, limit: int) -> str:
    body = str(text)
    return body if len(body) <= limit else body[: limit - 1] + "\u2026"


def _legend(series: list[str], x: float, y: float) -> list[str]:
    """A swatch and a name per series, drawn in one row that wraps at the width."""
    parts: list[str] = []
    cursor = x
    for index, name in enumerate(series):
        fill = SERIES_FILLS[index % len(SERIES_FILLS)]
        parts.append(_rect(cursor, y - 9, 10, 10, fill, BAR_EDGE))
        parts.append(_text(cursor + 15, y, _truncate(name, 34), size=11, fill=MUTED))
        cursor += 15 + 6.4 * min(len(name), 34) + 18
    return parts


def _axis(first_x: float, last_x: float, top: float, height: float) -> list[str]:
    parts: list[str] = []
    for tick in AXIS_TICKS:
        x = first_x + (last_x - first_x) * (tick / MAXIMUM)
        parts.append(_line(x, top, x, top + height, GRID))
        parts.append(_text(x, top - 6, tick, size=10, anchor="middle", fill=MUTED))
    return parts


def bar_chart(title: str, groups: list[tuple[str, dict[str, float]]], series: list[str], note: str = "") -> str:
    """A horizontal grouped bar chart: one row per *group*, one bar per series.

    *groups* is a list of ``(label, {series name: value})``, the value read out of
    a hundred, and a series a group does not carry is left out rather than drawn
    as zero, because a task a run did not measure and a task it scored nothing
    on are different facts.
    """
    if not groups:
        return _document(
            [_text(16, 34, "no rows to draw", size=12, fill=MUTED)],
            WIDTH,
            TOP,
            title,
        )

    per_row = len(series) * SUB_HEIGHT + (len(series) - 1) * SUB_GAP
    plot_height = len(groups) * per_row + (len(groups) - 1) * ROW_GAP
    height = TOP + plot_height + BOTTOM

    first_x = LABEL_WIDTH
    last_x = WIDTH - 74

    body: list[str] = [
        _text(16, 24, title, size=15, weight="bold"),
    ]
    if note:
        body.append(_text(16, 42, note, size=11, fill=MUTED))
    body += _legend(series, 16, 60)
    body += _axis(first_x, last_x, TOP, plot_height)

    for index, (label, values) in enumerate(groups):
        row_top = TOP + index * (per_row + ROW_GAP)
        body.append(_text(16, row_top + per_row / 2 + 4, _truncate(label, 38), size=11))
        for position, name in enumerate(series):
            if name not in values:
                continue
            value = max(0.0, min(MAXIMUM, float(values[name])))
            fill = SERIES_FILLS[position % len(SERIES_FILLS)]
            bar_x = row_top + position * (SUB_HEIGHT + SUB_GAP)
            length = (last_x - first_x) * (value / MAXIMUM)
            body.append(_rect(first_x, bar_x, max(1.0, length), SUB_HEIGHT, fill, BAR_EDGE))
            body.append(_text(last_x + 6, bar_x + SUB_HEIGHT - 2, f"{value:.1f}", size=10, fill=MUTED))

    return _document(body, WIDTH, height, title)


def matrix_chart(title: str, tasks: list[dict], labels: list[str], matrix: dict[str, dict[str, dict]], note: str = "") -> str:
    """One square per task and column: reached the end state, did not, or not measured.

    The columns are numbered rather than headed, because nine column names will
    not fit over nine columns of squares at a readable size, and the numbers are
    read off the legend underneath.
    """
    cell = 26
    gap = 4
    first_x = LABEL_WIDTH
    grid_width = len(labels) * (cell + gap)
    height = TOP + len(tasks) * (cell + gap) + BOTTOM + len(labels) * FOOTER_LINE

    body: list[str] = [_text(16, 24, title, size=15, weight="bold")]
    if note:
        body.append(_text(16, 42, note, size=11, fill=MUTED))

    for index, label in enumerate(labels):
        x = first_x + index * (cell + gap)
        body.append(_text(x + cell / 2, TOP - 10, index + 1, size=11, anchor="middle", fill=MUTED))
        body.append(_rect(x, TOP, cell, 1, AXIS))

    for row, task in enumerate(tasks):
        y = TOP + row * (cell + gap)
        body.append(_text(16, y + cell / 2 + 4, _truncate(f"{task['id']} ({task['capability']})", 44), size=11))
        for column, label in enumerate(labels):
            x = first_x + column * (cell + gap)
            cell_value = matrix.get(label, {}).get(task["id"])
            if cell_value is None:
                fill, edge, mark = MISSING_FILL, "", "?"
            elif cell_value.get("success"):
                fill, edge, mark = PASS_FILL, "", "P"
            else:
                fill, edge, mark = FAIL_FILL, "", "F"
            body.append(_rect(x, y, cell, cell, fill, edge))
            body.append(_text(x + cell / 2, y + cell / 2 + 4, mark, size=11, anchor="middle", fill="#ffffff"))

    legend_y = TOP + len(tasks) * (cell + gap) + 14
    for index, label in enumerate(labels):
        body.append(_text(16, legend_y + index * FOOTER_LINE, f"{index + 1}. {label}", size=11, fill=MUTED))
    body.append(_text(LABEL_WIDTH, legend_y + len(labels) * FOOTER_LINE - FOOTER_LINE, "P reached the end state, F did not, ? not measured", size=10, fill=MUTED))

    return _document(body, WIDTH, height, title)


def dimension_keys() -> tuple[str, ...]:
    from gembench import scoring

    return scoring.DIMENSIONS


def composite_chart(runs: list[dict]) -> str:
    """A run's per-task composite, one group per task, one bar per run."""
    series = [f"{run['side']}/{run['model']}" for run in runs]
    by_task: dict[str, dict[str, float]] = {}
    order: list[str] = []
    for run in runs:
        label = f"{run['side']}/{run['model']}"
        for row in run["rows"]:
            by_task.setdefault(row["task_id"], {})[label] = float(row["composite"])
            if row["task_id"] not in order:
                order.append(row["task_id"])
    groups = [(task_id, by_task[task_id]) for task_id in order]
    return bar_chart(
        "Composite by task, out of 100",
        groups,
        series,
        note="one bar per run and task; the composite is the weighted blend of the five dimensions",
    )


def dimension_chart(runs: list[dict], dimensions: tuple[str, ...] | None = None) -> str:
    """Each of the five dimensions per run, as its mean over the suite, out of a hundred.

    One row per dimension rather than one row per task, because the question this
    chart answers is which part of the blend a solver loses on, and that question
    is about the dimension and not about the task.
    """
    names = dimensions or dimension_keys()
    series = [f"{run['side']}/{run['model']}" for run in runs]
    by_dimension: dict[str, dict[str, float]] = {name: {} for name in names}
    for run in runs:
        label = f"{run['side']}/{run['model']}"
        for row in run["rows"]:
            for name in names:
                by_dimension[name].setdefault(label, 0.0)
                by_dimension[name][label] += float(row.get(name, 0.0))
        for name in names:
            count = max(1, len(run["rows"]))
            by_dimension[name][label] = 100.0 * by_dimension[name][label] / count

    groups = [(name, by_dimension[name]) for name in names]
    return bar_chart(
        "The five dimensions, each out of 100",
        groups,
        series,
        note="the mean over the suite of every dimension the run scored; the composite is their weighted blend",
    )


def capability_chart(runs: list[dict]) -> str:
    """A run's mean composite per capability."""
    series = [f"{run['side']}/{run['model']}" for run in runs]
    totals: dict[str, dict[str, list[float]]] = {}
    order: list[str] = []
    for run in runs:
        label = f"{run['side']}/{run['model']}"
        for row in run["rows"]:
            capability = row["capability"]
            bucket = totals.setdefault(capability, {})
            bucket.setdefault(label, []).append(float(row["composite"]))
            if capability not in order:
                order.append(capability)
    groups = [
        (capability, {label: sum(values) / len(values) for label, values in totals[capability].items()})
        for capability in order
    ]
    return bar_chart(
        "Composite by capability, out of 100",
        groups,
        series,
        note="the same tasks grouped by what they measure, so a weakness names an ability",
    )


def summary_chart(runs: list[dict]) -> str:
    """A run in one row of bars: the composite, the pass rate, and the share of the budget spent."""
    from gembench import scoring

    series = [f"{run['side']}/{run['model']}" for run in runs]
    by_model: dict[str, float] = {}
    passed: dict[str, float] = {}
    spent: dict[str, float] = {}
    for run in runs:
        label = f"{run['side']}/{run['model']}"
        rows = run["rows"]
        by_model[label] = sum(float(row["composite"]) for row in rows) / len(rows) if rows else 0.0
        passed[label] = 100.0 * (sum(1 for row in rows if row["success"]) / len(rows) if rows else 0.0)
        spent[label] = 100.0 * (
            sum(float(row["steps_used"]) / max(1.0, float(row["budget"])) for row in rows) / len(rows)
            if rows
            else 0.0
        )
    groups = [("composite", by_model), ("tasks passed", passed), ("budget spent", spent)]
    return bar_chart(
        "A run in one line",
        groups,
        series,
        note=f"the composite is the blend of the five dimensions, reported out of {scoring.SCORE_SCALE:.0f}",
    )


def write(out_dir: Path, runs: list[dict]) -> dict[str, str]:
    """Write every chart a run supports, and return the path of each."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    named = {
        "summary": summary_chart(runs),
        "composite": composite_chart(runs),
        "dimensions": dimension_chart(runs),
        "capability": capability_chart(runs),
    }
    written: dict[str, str] = {}
    for name, body in named.items():
        path = out_dir / f"{name}.svg"
        path.write_text(body, encoding="utf-8")
        written[name] = str(path)
    return written


def matrix(out_dir: Path, document: dict) -> str:
    """Write the pass matrix of a comparison, which is the chart that needs both sides."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = list(document["columns"])
    body = matrix_chart(
        "End state reached, by task and by column",
        document["tasks"],
        labels,
        document["matrix"],
        note="a column is one side and one solver label; a row is one task",
    )
    path = out_dir / "matrix.svg"
    path.write_text(body, encoding="utf-8")
    return str(path)


def figure(paths: dict[str, str]) -> str:
    """The charts as one HTML fragment, for a page that holds the files beside it."""
    parts: list[str] = []
    for name, path in sorted(paths.items()):
        target = Path(path)
        if not target.is_file():
            continue
        parts.append(f'<h3>{escape(name)}</h3>')
        parts.append(target.read_text(encoding="utf-8"))
    return "\n".join(parts) + "\n"


def show(paths: dict[str, str]) -> list[str]:
    """Display each chart in a notebook, and report the ones that could not be shown.

    In a notebook the SVG is displayed inline. Anywhere else the paths are
    returned, because a chart a reader cannot see would otherwise be a silent
    omission rather than a file on disk.
    """
    shown: list[str] = []
    try:
        from IPython.display import SVG, display  # type: ignore
    except ImportError:
        return [str(path) for path in paths.values()]

    for name, path in sorted(paths.items()):
        target = Path(path)
        if not target.is_file():
            continue
        display(SVG(filename=str(target)))
        shown.append(name)
    return shown
