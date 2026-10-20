"""Before/after comparison of two capture bench runs (``scripts.capture_bench``).

    python -m scripts.bench_compare data/bench/base-ref data/bench/after-ref \\
        --out data/bench/compare-ref.html

Writes one self-contained HTML page:

- the aggregate metrics of both runs side by side with their deltas, overall
  and per browser/device: ok / failed (by error class) / skipped, p50 and p90 of
  the total time and of every phase, unstable captures, fast-path rate,
  warnings by code, reloads, screenshot retries, share of images as wide as the
  viewport;
- one row per case (and repeat): both screenshots as thumbnails linking to the
  full image, image size, durations, phase timings, warnings and the key
  capture metadata, with flags for what changed (status, height > 10 %, more
  than 1.3x slower, warnings added / removed).

Image links are relative to the HTML file, so the page works from the host as
well as inside a container that ran the bench. A plain-text summary goes to
stdout. Aggregates are recomputed from both ``results.jsonl`` files, so runs
made with an older bench compare fine.
"""

import argparse
import html
import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts.capture_bench import (
    META_SECTIONS,
    RESULTS_FILE,
    SUMMARY_FILE,
    clip,
    group_sort_key,
    is_unstable,
    phase_sort_key,
    read_results,
    record_key,
    reload_count,
    summarize,
    text_table,
    viewport_width,
    warning_codes,
)

HEIGHT_CHANGE_PCT = 10.0  # flag a height change beyond this (plan: > 10 %)
SLOWER_RATIO = 1.3  # flag a case this much slower (plan: > 1.3 x base)
HEIGHT_MATCH_PX = 2  # A/B gate of the second scroll pass: heights within 2 px
TIME_TONE_PCT = 5.0  # time deltas within +/-5 % are not colored
TIME_TONE_MS = 20  # ... nor deltas below 20 ms

CaseKey = tuple[str, int]


class CompareError(Exception):
    pass


@dataclass
class BenchRun:
    path: Path
    label: str
    records: list[dict[str, Any]]
    info: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def by_key(self) -> dict[CaseKey, dict[str, Any]]:
        return {record_key(record): record for record in self.records}


@dataclass
class CasePair:
    key: CaseKey
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    flags: list[str] = field(default_factory=list)


@dataclass
class MetricRow:
    label: str
    before: str
    after: str
    delta: str
    tone: str = ""  # "worse" | "better" | ""
    pct: str = ""  # relative change of a timing, e.g. "+12%"


def load_run(path: Path) -> BenchRun:
    """The records of a bench output directory, its run info and summary."""
    path = Path(path)
    if not (path / RESULTS_FILE).is_file():
        raise CompareError(f"{path / RESULTS_FILE} does not exist")
    records = read_results(path)
    info: dict[str, Any] = {}
    try:
        stored = json.loads((path / SUMMARY_FILE).read_text(encoding="utf-8"))
        if isinstance(stored, dict) and isinstance(stored.get("run"), dict):
            info = stored["run"]
    except (OSError, ValueError):
        pass
    label = str(info.get("label") or path.name)
    return BenchRun(path, label, records, info, summarize(records))


# ---------------------------------------------------------------------------
# Case pairs
# ---------------------------------------------------------------------------


def status_of(record: Mapping[str, Any] | None) -> str:
    if record is None:
        return "absent"
    if record.get("skipped"):
        return "skipped"
    return "ok" if record.get("ok") else "failed"


def total_ms(record: Mapping[str, Any]) -> float | None:
    """The capture's total time: timings_ms.total, else the duration."""
    total = (record.get("timings_ms") or {}).get("total")
    if isinstance(total, int | float):
        return float(total)
    duration = record.get("duration")
    if isinstance(duration, int | float):
        return float(duration) * 1000
    return None


def height_change_pct(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> float | None:
    hb, ha = before.get("image_height"), after.get("image_height")
    if not isinstance(hb, int) or not isinstance(ha, int) or hb <= 0:
        return None
    return (ha - hb) / hb * 100


def case_flags(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> list[str]:
    """What changed between the two captures of one case, as short labels."""
    if before is None:
        return ["only in after"]
    if after is None:
        return ["only in before"]
    flags: list[str] = []
    sb, sa = status_of(before), status_of(after)
    if sb != sa:
        flags.append(f"{sb} -> {sa}")
    elif sa == "failed" and before.get("error_class") != after.get("error_class"):
        flags.append(f"{before.get('error_class')} -> {after.get('error_class')}")
    if sb == sa == "ok":
        change = height_change_pct(before, after)
        if change is not None and abs(change) > HEIGHT_CHANGE_PCT:
            flags.append(f"height {change:+.0f}%")
        wb, wa = before.get("image_width"), after.get("image_width")
        if wb != wa:
            flags.append(f"width {wb} -> {wa}")
        tb, ta = total_ms(before), total_ms(after)
        if tb and ta and ta > SLOWER_RATIO * tb:
            reloaded = "reloaded" if reload_count(after) else "no reload"
            flags.append(f"slower x{ta / tb:.1f} ({reloaded})")
        if is_unstable(before) != is_unstable(after):
            flags.append("now unstable" if is_unstable(after) else "now stable")
    codes_b, codes_a = set(warning_codes(before)), set(warning_codes(after))
    flags += [f"+{code}" for code in sorted(codes_a - codes_b)]
    flags += [f"-{code}" for code in sorted(codes_b - codes_a)]
    return flags


def pair_cases(before: BenchRun, after: BenchRun) -> list[CasePair]:
    """Cases of both runs matched by (id, repeat): the before order first,
    then the cases only the after run has."""
    before_by_key, after_by_key = before.by_key(), after.by_key()
    keys = list(before_by_key)
    keys += [key for key in after_by_key if key not in before_by_key]
    pairs = []
    for key in keys:
        b, a = before_by_key.get(key), after_by_key.get(key)
        pairs.append(CasePair(key, b, a, case_flags(b, a)))
    return pairs


# ---------------------------------------------------------------------------
# Aggregate metric rows
# ---------------------------------------------------------------------------


def _tone(delta: float, worse_if: str | None) -> str:
    if not delta or worse_if is None:
        return ""
    worse = delta > 0 if worse_if == "up" else delta < 0
    return "worse" if worse else "better"


def count_row(
    label: str, before: Any, after: Any, worse_if: str | None = None
) -> MetricRow:
    """*worse_if*: "up" (more is worse), "down" (less is worse) or None."""
    b = before if isinstance(before, int) else None
    a = after if isinstance(after, int) else None
    if b is None or a is None:
        return MetricRow(label, _txt(b), _txt(a), "")
    return MetricRow(label, str(b), str(a), f"{a - b:+d}", _tone(a - b, worse_if))


def time_row(label: str, before: Any, after: Any, unit: str = "ms") -> MetricRow:
    """A timing in ms shown in *unit* ("ms" or "s"); slower is worse."""
    b = before if isinstance(before, int | float) else None
    a = after if isinstance(after, int | float) else None

    def show(value: float | None) -> str:
        if value is None:
            return "-"
        return f"{value / 1000:.2f}" if unit == "s" else f"{value:.0f}"

    if b is None or a is None:
        return MetricRow(label, show(b), show(a), "")
    diff = a - b
    pct = diff / b * 100 if b else None
    shown = f"{diff / 1000:+.2f}" if unit == "s" else f"{diff:+.0f}"
    pct_text = "" if pct is None else f"{pct:+.0f}%"
    delta = shown + (f" ({pct_text})" if pct_text else "")
    tone = ""
    if abs(diff) >= TIME_TONE_MS and (pct is None or abs(pct) > TIME_TONE_PCT):
        tone = "worse" if diff > 0 else "better"
    return MetricRow(label, show(b), show(a), delta, tone, pct_text)


def rate_row(
    label: str, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> MetricRow:
    """A ``{count, of, rate[, known]}`` share; higher is better. ``known`` = 0
    (no case reported the underlying meta) is shown as "no data"."""

    def show(part: Mapping[str, Any] | None) -> str:
        if not part or part.get("rate") is None:
            return "-" if not part else f"- ({part.get('count', 0)}/0)"
        no_data = part.get("known") == 0
        return f"{part['rate'] * 100:.0f}% ({part['count']}/{part['of']}" + (
            ", no data)" if no_data else ")"
        )

    rb = (before or {}).get("rate")
    ra = (after or {}).get("rate")
    if rb is None or ra is None:
        return MetricRow(label, show(before), show(after), "")
    diff = (ra - rb) * 100
    return MetricRow(
        label, show(before), show(after), f"{diff:+.0f} pp", _tone(diff, "down")
    )


def _txt(value: Any) -> str:
    return "-" if value is None else str(value)


def _stat(group: Mapping[str, Any] | None, *path: str) -> Any:
    value: Any = group or {}
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def metric_rows(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> list[MetricRow]:
    """The before/after rows of one group's stats (capture_bench.group_stats);
    either side may be missing."""
    b, a = before or {}, after or {}
    rows = [
        count_row("cases", b.get("cases"), a.get("cases")),
        count_row("ok", b.get("ok"), a.get("ok"), "down"),
        count_row("failed", b.get("failed"), a.get("failed"), "up"),
    ]
    classes = sorted(
        set(b.get("failed_by_class") or {}) | set(a.get("failed_by_class") or {})
    )
    for cls in classes:
        rows.append(
            count_row(
                f"failed: {cls}",
                (b.get("failed_by_class") or {}).get(cls, 0) if b else None,
                (a.get("failed_by_class") or {}).get(cls, 0) if a else None,
                "up",
            )
        )
    rows.append(count_row("skipped", b.get("skipped"), a.get("skipped")))
    for q in ("p50", "p90"):
        rows.append(
            time_row(
                f"total {q} (s)",
                _stat(b, "total_ms", q),
                _stat(a, "total_ms", q),
                unit="s",
            )
        )
    for q in ("p50", "p90"):
        wall_b, wall_a = _stat(b, "wall_s", q), _stat(a, "wall_s", q)
        rows.append(
            time_row(
                f"wall {q} (s)",
                None if wall_b is None else wall_b * 1000,
                None if wall_a is None else wall_a * 1000,
                unit="s",
            )
        )
    phases = sorted(
        set(b.get("phases_ms") or {}) | set(a.get("phases_ms") or {}),
        key=phase_sort_key,
    )
    for phase in phases:
        for q in ("p50", "p90"):
            rows.append(
                time_row(
                    f"{phase} {q} (ms)",
                    _stat(b, "phases_ms", phase, q),
                    _stat(a, "phases_ms", phase, q),
                )
            )
    rows += [
        count_row("unstable", b.get("unstable"), a.get("unstable"), "up"),
        rate_row("fast-path (stable ok)", b.get("fast_path"), a.get("fast_path")),
        rate_row(
            "width = viewport", b.get("width_eq_viewport"), a.get("width_eq_viewport")
        ),
        count_row("reloaded cases", b.get("reloads"), a.get("reloads")),
        count_row(
            "screenshot retries",
            b.get("screenshot_retries"),
            a.get("screenshot_retries"),
            "up",
        ),
        count_row("retried cases", b.get("retried"), a.get("retried"), "up"),
    ]
    codes = sorted(set(b.get("warnings") or {}) | set(a.get("warnings") or {}))
    for code in codes:
        rows.append(
            count_row(
                f"warning: {code}",
                (b.get("warnings") or {}).get(code, 0) if b else None,
                (a.get("warnings") or {}).get(code, 0) if a else None,
            )
        )
    return rows


def group_names(before: BenchRun, after: BenchRun) -> list[str]:
    names = set(before.summary.get("groups") or {}) | set(
        after.summary.get("groups") or {}
    )
    return ["overall", *sorted(names, key=group_sort_key)]


def group_stats_of(run: BenchRun, name: str) -> Mapping[str, Any] | None:
    if name == "overall":
        return run.summary.get("overall")
    return (run.summary.get("groups") or {}).get(name)


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------


# The metric rows of the one-line per-group text summary.
_SHORT_ROWS = (
    "ok",
    "failed",
    "total p50 (s)",
    "total p90 (s)",
    "unstable",
    "fast-path (stable ok)",
    "width = viewport",
)


def _short_group_line(name: str, b: Mapping | None, a: Mapping | None) -> str:
    parts = []
    for row in metric_rows(b, a):
        if row.label not in _SHORT_ROWS:
            continue
        delta = f" ({row.pct})" if row.pct else ""
        parts.append(f"{row.label} {row.before} -> {row.after}{delta}")
    return f"  {name}: " + "; ".join(parts)


def text_summary(
    before: BenchRun, after: BenchRun, pairs: Sequence[CasePair], html_path: Path
) -> str:
    matched = sum(1 for p in pairs if p.before is not None and p.after is not None)
    only_b = sum(1 for p in pairs if p.after is None)
    only_a = sum(1 for p in pairs if p.before is None)
    lines = [
        f"bench compare: before={before.label} ({len(before.records)} cases) "
        f"after={after.label} ({len(after.records)} cases): {matched} matched, "
        f"{only_b} only before, {only_a} only after",
        "overall:",
    ]
    rows = [["metric", "before", "after", "delta"]]
    rows += [
        [r.label, r.before, r.after, r.delta + (" !" if r.tone == "worse" else "")]
        for r in metric_rows(before.summary["overall"], after.summary["overall"])
    ]
    lines += ["  " + line for line in text_table(rows)]
    lines.append("groups:")
    for name in group_names(before, after)[1:]:
        lines.append(
            _short_group_line(
                name, group_stats_of(before, name), group_stats_of(after, name)
            )
        )

    def listing(title: str, items: list[str]) -> None:
        lines.append(f"{title}: {len(items) or 'none'}")
        lines.extend(f"  {item}" for item in items)

    both = [p for p in pairs if p.before is not None and p.after is not None]
    listing(
        "status changes",
        [
            f"{_case_name(p.key)}: {status_of(p.before)} -> {status_of(p.after)}"
            + _error_suffix(p.after)
            for p in both
            if status_of(p.before) != status_of(p.after)
        ],
    )
    ok_pairs = [p for p in both if status_of(p.before) == status_of(p.after) == "ok"]
    height_items = []
    within = 0
    off_by: list[str] = []  # pairs whose heights differ by more than 2 px
    for p in ok_pairs:
        hb, ha = p.before.get("image_height"), p.after.get("image_height")
        if isinstance(hb, int) and isinstance(ha, int):
            if abs(ha - hb) <= HEIGHT_MATCH_PX:
                within += 1
            else:
                off_by.append(f"{_case_name(p.key)} {ha - hb:+d} px")
        change = height_change_pct(p.before, p.after)
        if change is not None and abs(change) > HEIGHT_CHANGE_PCT:
            height_items.append(
                f"{_case_name(p.key)}: {hb} -> {ha} px ({change:+.0f}%)"
                + _warning_diff(p.before, p.after)
            )
    listing(f"height changes > {HEIGHT_CHANGE_PCT:g}%", height_items)
    lines.append(
        f"heights within {HEIGHT_MATCH_PX} px: {within}/{len(ok_pairs)} ok pairs"
        + (f" (others: {', '.join(off_by)})" if off_by else "")
    )
    listing(
        "width changes",
        [
            f"{_case_name(p.key)}: {p.before.get('image_width')} -> "
            f"{p.after.get('image_width')} px (viewport {viewport_width(p.after)})"
            for p in ok_pairs
            if p.before.get("image_width") != p.after.get("image_width")
        ],
    )
    slower = []
    for p in ok_pairs:
        tb, ta = total_ms(p.before), total_ms(p.after)
        if tb and ta and ta > SLOWER_RATIO * tb:
            reloaded = "page_reloaded" if reload_count(p.after) else "NO page_reloaded"
            slower.append(
                f"{_case_name(p.key)}: {tb / 1000:.1f} -> {ta / 1000:.1f} s "
                f"(x{ta / tb:.1f}, {reloaded})"
            )
    listing(f"slower > {SLOWER_RATIO:g}x", slower)
    listing(
        "warning changes",
        [
            f"{_case_name(p.key)}: {' '.join(f for f in p.flags if f[0] in '+-')}"
            for p in both
            if any(f[0] in "+-" for f in p.flags)
        ],
    )
    lines.append(f"html: {html_path}")
    return "\n".join(lines)


def _case_name(key: CaseKey) -> str:
    case_id, repeat = key
    return case_id if repeat == 1 else f"{case_id} r{repeat}"


def _error_suffix(record: Mapping[str, Any] | None) -> str:
    if not record or record.get("ok") or record.get("skipped"):
        return ""
    return f" ({record.get('error_class')}: {clip(str(record.get('error')), 100)})"


def _warning_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    added = sorted(set(warning_codes(after)) - set(warning_codes(before)))
    removed = sorted(set(warning_codes(before)) - set(warning_codes(after)))
    parts = [f"+{c}" for c in added] + [f"-{c}" for c in removed]
    return f" [{' '.join(parts)}]" if parts else ""


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1f2328;
  --muted: #656d76; --line: #d8dee4; --card: #f6f8fa; --worse: #cf222e;
  --better: #1a7f37; --flag-bg: #fff1e5; --flag-fg: #953800;
  --warn-bg: #fff8c5; --warn-fg: #7d4e00; --info-bg: #ddf4ff; --info-fg: #0a3069; }
@media (prefers-color-scheme: dark) { :root { --bg: #0d1117; --fg: #e6edf3;
  --muted: #8d96a0; --line: #30363d; --card: #161b22; --worse: #ff7b72;
  --better: #3fb950; --flag-bg: #3d2410; --flag-fg: #ffa657;
  --warn-bg: #3b2e00; --warn-fg: #e3b341; --info-bg: #0c2d6b; --info-fg: #a5d6ff; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px 24px 48px; background: var(--bg); color: var(--fg);
  font: 13px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 28px 0 8px; }
a { color: inherit; }
table { border-collapse: collapse; }
th, td { border-bottom: 1px solid var(--line); padding: 4px 8px; text-align: left;
  vertical-align: top; }
th { font-weight: 600; }
.muted { color: var(--muted); }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.worse { color: var(--worse); font-weight: 600; }
.better { color: var(--better); font-weight: 600; }
.runs td, .runs th { font-size: 12px; }
.groups { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
.group { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 10px; }
.group h3 { margin: 0 0 6px; font-size: 14px; }
.controls { position: sticky; top: 0; z-index: 2; background: var(--bg);
  padding: 8px 0; display: flex; gap: 16px; align-items: center; }
.controls input[type=search] { width: 280px; padding: 4px 6px; }
.cases > tbody > tr > td { border-bottom: 2px solid var(--line); }
.cases .info { width: 260px; word-break: break-all; }
.cases .id { font-weight: 600; }
.shot { width: 300px; max-height: 420px; overflow: auto; border: 1px solid var(--line);
  border-radius: 4px; background: #fff; }
.shot img { display: block; width: 100%; height: auto; }
.side { width: 330px; }
.line { margin-top: 3px; }
.flag { display: inline-block; background: var(--flag-bg); color: var(--flag-fg);
  border-radius: 4px; padding: 0 6px; margin: 3px 4px 0 0; font-size: 12px; }
.w { display: inline-block; border-radius: 4px; padding: 0 5px; margin: 2px 3px 0 0;
  font-size: 12px; }
.w-warning { background: var(--warn-bg); color: var(--warn-fg); }
.w-info { background: var(--info-bg); color: var(--info-fg); }
.status-failed, .status-skipped { font-weight: 600; color: var(--worse); }
.status-skipped { color: var(--muted); }
.meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.meta b { color: var(--fg); font-weight: 600; }
.changed { color: var(--flag-fg); font-weight: 600; }
tr.hidden { display: none; }
"""

_JS = """
const rows = Array.from(document.querySelectorAll("tr.case"));
const flagged = document.getElementById("flagged");
const query = document.getElementById("query");
function applyFilter() {
  const q = query.value.trim().toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const show = (!flagged.checked || row.dataset.flagged === "1") &&
      (!q || row.dataset.search.includes(q));
    row.classList.toggle("hidden", !show);
    if (show) shown += 1;
  }
  document.getElementById("shown").textContent = shown + " / " + rows.length;
}
flagged.addEventListener("change", applyFilter);
query.addEventListener("input", applyFilter);
applyFilter();
"""

_e = html.escape


def rel_link(target: Path, html_dir: Path) -> str:
    """URL of *target* relative to the directory of the HTML file."""
    relative = os.path.relpath(os.path.abspath(target), os.path.abspath(html_dir))
    return quote(Path(relative).as_posix())


def _value_text(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) for item in value):
            return f"[{len(value)} items]"
        return clip(", ".join(str(item) for item in value[:5]), 80)
    if isinstance(value, Mapping):
        return clip(json.dumps(value, ensure_ascii=False), 80)
    return clip(str(value), 80)


def _meta_items(record: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """``(section, key, shown value)`` of a record's meta, then its profile."""
    items = []
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    sections: list[tuple[str, Any]] = [(name, meta.get(name)) for name in META_SECTIONS]
    if "device" not in meta:
        sections.append(("profile", record.get("profile")))
    for section, values in sections:
        if not isinstance(values, Mapping):
            continue
        for key, value in values.items():
            items.append((section, str(key), _value_text(value)))
    return items


def _side_html(
    record: Mapping[str, Any] | None,
    run: BenchRun,
    html_dir: Path,
    other: Mapping[str, Any] | None,
) -> str:
    if record is None:
        return '<div class="muted">not in this run</div>'
    status = status_of(record)
    parts: list[str] = []
    if status == "skipped":
        return (
            f'<div class="status-skipped">skipped</div>'
            f'<div class="line muted">{_e(str(record.get("error") or ""))}</div>'
        )
    if status == "failed":
        parts.append(
            f'<div class="status-failed">FAILED {_e(str(record.get("error_class")))}'
            "</div>"
            f'<div class="line">{_e(str(record.get("error") or ""))}</div>'
        )
        if record.get("phase"):
            parts.append(
                f'<div class="line muted">phase {_e(str(record["phase"]))}</div>'
            )
    image = record.get("image")
    if isinstance(image, str) and (run.path / image).is_file():
        href = rel_link(run.path / image, html_dir)
        parts.append(
            f'<div class="shot"><a href="{href}" target="_blank">'
            f'<img src="{href}" loading="lazy" alt=""></a></div>'
        )
    elif status == "ok":
        parts.append('<div class="muted">image missing</div>')
    if status == "ok":
        size = f"{record.get('image_width')} x {record.get('image_height')} px"
        if isinstance(record.get("bytes"), int):
            size += f", {record['bytes'] / 1024:.0f} KB"
        if other is not None and status_of(other) == "ok":
            change = height_change_pct(other, record)
            if change is not None and change:
                cls = "changed" if abs(change) > HEIGHT_CHANGE_PCT else "muted"
                size += f' <span class="{cls}">({change:+.1f}% h)</span>'
        parts.append(f'<div class="line">{size}</div>')
    times = []
    if isinstance(record.get("duration"), int | float):
        times.append(f"duration {record['duration']:.2f} s")
    total = (record.get("timings_ms") or {}).get("total")
    if isinstance(total, int | float):
        times.append(f"total {total / 1000:.2f} s")
    if isinstance(record.get("wall_s"), int | float):
        times.append(f"wall {record['wall_s']:.1f} s")
    if record.get("attempts"):
        times.append(f"attempts {record['attempts']}")
    if times:
        parts.append(f'<div class="line">{_e(", ".join(times))}</div>')
    phases = {k: v for k, v in (record.get("timings_ms") or {}).items() if k != "total"}
    if phases:
        shown = " &middot; ".join(
            f"{_e(name)} {phases[name]}" for name in sorted(phases, key=phase_sort_key)
        )
        parts.append(f'<div class="line muted">{shown}</div>')
    warnings = record.get("warnings") or []
    if warnings:
        chips = []
        for warning in warnings:
            if not isinstance(warning, Mapping):
                continue
            level = "warning" if warning.get("level") == "warning" else "info"
            count = warning.get("count") or 1
            title = json.dumps(warning.get("details") or {}, ensure_ascii=False)
            chips.append(
                f'<span class="w w-{level}" title="{_e(title)}">'
                f"{_e(str(warning.get('code')))}"
                + (f" x{count}" if count > 1 else "")
                + "</span>"
            )
        parts.append(f'<div class="line">{"".join(chips)}</div>')
    other_items = {
        (section, key): value
        for section, key, value in (_meta_items(other) if other else [])
    }
    meta_lines: dict[str, list[str]] = {}
    for section, key, value in _meta_items(record):
        changed = other is not None and other_items.get((section, key)) not in (
            None,
            value,
        )
        cls = ' class="changed"' if changed else ""
        meta_lines.setdefault(section, []).append(
            f"{_e(key)} <span{cls}>{_e(value)}</span>"
        )
    if meta_lines:
        parts.append(
            '<div class="meta">'
            + "".join(
                f"<div><b>{_e(section)}</b> {', '.join(items)}</div>"
                for section, items in meta_lines.items()
            )
            + "</div>"
        )
    return "".join(parts)


def _case_row(pair: CasePair, before: BenchRun, after: BenchRun, html_dir: Path) -> str:
    record = pair.after or pair.before or {}
    viewport = record.get("viewport") or {}
    url = str(record.get("url") or "")
    context = " / ".join(
        str(part)
        for part in (
            record.get("browser"),
            record.get("device"),
            f"{viewport.get('width')}x{viewport.get('height')}",
            record.get("wait_until"),
        )
        if part
    )
    flags = "".join(f'<span class="flag">{_e(flag)}</span>' for flag in pair.flags)
    search = f"{pair.key[0]} {url} {context} {' '.join(pair.flags)}".lower()
    return (
        f'<tr class="case" data-flagged="{1 if pair.flags else 0}" '
        f'data-search="{_e(search)}">'
        f'<td class="info"><div class="id">{_e(_case_name(pair.key))}</div>'
        f'<div class="muted">{_e(context)}</div>'
        f'<div><a href="{_e(url)}" target="_blank" rel="noreferrer">{_e(url)}</a>'
        f"</div><div>{flags}</div></td>"
        f'<td class="side">{_side_html(pair.before, before, html_dir, None)}</td>'
        f'<td class="side">'
        f"{_side_html(pair.after, after, html_dir, pair.before)}</td></tr>"
    )


def _metric_table(rows: Iterable[MetricRow]) -> str:
    body = "".join(
        f"<tr><td>{_e(row.label)}</td><td class='num'>{_e(row.before)}</td>"
        f"<td class='num'>{_e(row.after)}</td>"
        f"<td class='num {row.tone}'>{_e(row.delta)}</td></tr>"
        for row in rows
    )
    return (
        "<table><thead><tr><th>metric</th><th class='num'>before</th>"
        "<th class='num'>after</th><th class='num'>delta</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _run_table(before: BenchRun, after: BenchRun, html_dir: Path) -> str:
    fields: list[tuple[str, Callable[[BenchRun], str]]] = [
        ("directory", lambda run: rel_link(run.path, html_dir)),
        ("cases", lambda run: str(len(run.records))),
        ("started", lambda run: str(run.info.get("started_at") or "-")),
        ("elapsed", lambda run: _txt(run.info.get("elapsed_s")) + " s"),
        ("note", lambda run: str(run.info.get("note") or "")),
        ("source", lambda run: json.dumps(run.info.get("source") or {})),
        ("overrides", lambda run: json.dumps(run.info.get("overrides") or {})),
        (
            "browsers",
            lambda run: ", ".join(
                f"{k} {v}" for k, v in (run.info.get("browser_versions") or {}).items()
            ),
        ),
        ("code", lambda run: str(run.info.get("code_fingerprint") or "-")),
        (
            "settings",
            lambda run: (
                f"fmt {run.info.get('fmt', '-')}, concurrency "
                f"{run.info.get('concurrency', '-')}, repeat "
                f"{run.info.get('repeat', '-')}, host delay "
                f"{run.info.get('host_delay_s', '-')} s, max loads/host "
                f"{run.info.get('max_loads_per_host') or '-'}"
            ),
        ),
    ]
    body = "".join(
        f"<tr><th>{_e(name)}</th><td>{_e(get(before))}</td>"
        f"<td>{_e(get(after))}</td></tr>"
        for name, get in fields
    )
    return (
        f"<table class='runs'><thead><tr><th></th><th>before: {_e(before.label)}</th>"
        f"<th>after: {_e(after.label)}</th></tr></thead><tbody>{body}</tbody></table>"
    )


def render_html(
    before: BenchRun,
    after: BenchRun,
    pairs: Sequence[CasePair],
    html_path: Path,
    title: str | None = None,
) -> str:
    html_dir = Path(html_path).parent
    title = title or f"Capture bench: {before.label} vs {after.label}"
    groups = "".join(
        f'<div class="group"><h3>{_e(name)}</h3>'
        + _metric_table(
            metric_rows(group_stats_of(before, name), group_stats_of(after, name))
        )
        + "</div>"
        for name in group_names(before, after)
    )
    case_rows = "".join(_case_row(pair, before, after, html_dir) for pair in pairs)
    flagged = sum(1 for pair in pairs if pair.flags)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_CSS}</style></head>
<body>
<h1>{_e(title)}</h1>
<div class="muted">generated {generated}; timings over ok cases (wall: every case
that ran); warnings count cases; fast-path is over stable ok cases</div>
<h2>Runs</h2>
{_run_table(before, after, html_dir)}
<h2>Aggregates</h2>
<div class="groups">{groups}</div>
<h2>Cases</h2>
<div class="controls">
<label><input type="checkbox" id="flagged"> only flagged ({flagged})</label>
<input type="search" id="query" placeholder="filter: id, url, browser, flag">
<span class="muted" id="shown"></span>
</div>
<table class="cases"><thead><tr><th>case</th><th>before: {_e(before.label)}</th>
<th>after: {_e(after.label)}</th></tr></thead>
<tbody>{case_rows}</tbody></table>
<script>{_JS}</script>
</body></html>
"""


def compare(
    before_dir: Path, after_dir: Path, out: Path, title: str | None = None
) -> str:
    """Write the HTML comparison to *out*; returns the text summary."""
    before, after = load_run(before_dir), load_run(after_dir)
    pairs = pair_cases(before, after)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(before, after, pairs, out, title), encoding="utf-8")
    return text_summary(before, after, pairs, out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.bench_compare",
        description="Compare two capture bench runs: HTML page + text summary.",
    )
    parser.add_argument("before", type=Path, help="bench output directory (before)")
    parser.add_argument("after", type=Path, help="bench output directory (after)")
    parser.add_argument("--out", type=Path, required=True, help="HTML file to write")
    parser.add_argument("--title", help="page title")
    args = parser.parse_args(argv)
    try:
        print(compare(args.before, args.after, args.out, args.title))
    except CompareError as exc:
        print(f"bench_compare: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
