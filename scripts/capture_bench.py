"""Capture bench: run the real capture pipeline over a fixed set of cases and
record what every capture did — phase timings, warnings, capture metadata and
the image — so that two runs (before / after a change) can be compared with
``python -m scripts.bench_compare``.

Cases come from a TSV file (``--cases``: columns ``id url browser WxH device
wait_until timeout_ms``; the columns after ``url`` may be left out, ``#``
starts a comment line, ``device`` ``auto`` or empty follows the width) or from
a URL list (``--urls``, one URL per line) crossed with ``--browsers`` and
``--viewports`` (``1920x1080:desktop,390x844:mobile``; without ``:device`` the
type follows the width). Every case runs ``pipeline.capture_with_retries`` in a
fresh browser context, on one shared Browser per engine connected through
``app.tasks.worker._launch_browser``, with the device profile of
``profiles.build_profile``.

Output in ``--out``:

- ``results.jsonl``: one JSON object per case and repeat, appended as soon as a
  case finishes, so an interrupted run keeps what it did (``--resume`` runs only
  the missing or skipped cases, ``--summarize`` only rebuilds the summary);
- ``img/<id>[-r<n>].<jpg|png>``: the screenshots (``-r<n>`` with ``--repeat``);
- ``summary.json``: counts and timing percentiles per browser/device, quality
  metrics and the failures; a compact text version is printed at the end.

``--set NAME=VALUE`` overrides a tunable of ``app.screenshots.capture.config``
for the run; VALUE is parsed as JSON when it can be (``true``, ``0.5``,
``["a"]``), else used as a string — for A/B gates such as
``--set FORCE_PASS2=true``. ``--host-delay`` and ``--max-loads-per-host`` pace
sites whose WAF blocks bursts (zempleo, taservices: at most 3 loads per run,
20 s apart); a case is a load, its retries and reloads count too, and cases
past the limit are recorded as skipped. The limits hold per invocation: a later
``--resume`` with the same arguments runs the skipped cases (reference.tsv has
5 taservices cases, so with ``--max-loads-per-host 3`` the last two run on the
resume).

Run it where the browser server is reachable, e.g. in a throwaway worker-image
container on the dev compose network (from the repository root)::

    docker run --rm --network everneu-dashboard_default \\
      -e BROWSER_WS_ENDPOINT=ws://browser -e PYTHONDONTWRITEBYTECODE=1 \\
      --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/app -w /app \\
      everneu-dashboard-worker python -m scripts.capture_bench \\
      --cases scripts/bench/reference.tsv --out data/bench/base-ref \\
      --host-delay 20 --max-loads-per-host 3

The bench never touches the database or Redis: when neither the environment nor
a ``.env`` file provides the settings the app requires, placeholders are set.
Playwright and the app are imported only when a run starts, so the helpers
below import without a browser.
"""

import argparse
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

BROWSERS = ("chrome", "msedge", "firefox", "webkit")
_BROWSER_ALIASES = {"chromium": "chrome"}
DEVICE_TYPES = ("desktop", "tablet", "mobile")
WAIT_UNTIL_LEVELS = ("load", "domcontentloaded", "networkidle", "commit")
TSV_COLUMNS = ("id", "url", "browser", "WxH", "device", "wait_until", "timeout_ms")
DEFAULT_BROWSER = "chrome"
DEFAULT_VIEWPORTS = "1920x1080:desktop"
DEFAULT_WAIT_UNTIL = "load"
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_QUALITY = 85
DEFAULT_CASE_TIMEOUT_S = 1200.0
# capture_meta.timings_ms phases in pipeline order; unknown phases follow, sorted.
PHASE_ORDER = (
    "context",
    "goto",
    "load_wait",
    "quiet",
    "prepare",
    "scroll",
    "readiness",
    "recovery",
    "reload",
    "snapshot",
    "total",
)
META_SECTIONS = ("navigation", "page", "readiness", "device", "overlays")
RESULTS_FILE = "results.jsonl"
SUMMARY_FILE = "summary.json"
IMAGE_DIR = "img"
SKIPPED_CLASS = "Skipped"
ERROR_MAX_CHARS = 300
META_MAX_CHARS = 300
DETAIL_MAX_CHARS = 120
DETAIL_MAX_SAMPLES = 3
# Same thresholds as profiles.resolve_device_type / ViewportEntry.
_DESKTOP_MIN_WIDTH = 1024
_TABLET_MIN_WIDTH = 768
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WXH_RE = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")
_SLUG_MAX = 60
# Settings the app refuses to start without; the bench uses none of them.
_APP_ENV_PLACEHOLDERS = {
    "DATABASE_URL": "postgresql+asyncpg://bench:bench@localhost:5432/bench",
    "REDIS_URL": "redis://localhost:6379",
    "SECRET_KEY": "capture-bench-placeholder-secret-key-unused",
}

logger = logging.getLogger("capture_bench")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class CaseError(ValueError):
    """An invalid case definition: TSV row, URL list or viewport spec."""


@dataclass(frozen=True)
class Case:
    id: str
    url: str
    browser: str = DEFAULT_BROWSER
    width: int = 1920
    height: int = 1080
    device: str | None = None  # None: follows the width
    wait_until: str = DEFAULT_WAIT_UNTIL
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    @property
    def viewport(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}

    @property
    def host(self) -> str:
        return host_key(self.url)


@dataclass(frozen=True)
class Job:
    """One run of a case: *repeat* counts from 1 up to *repeats*."""

    case: Case
    repeat: int = 1
    repeats: int = 1

    @property
    def key(self) -> tuple[str, int]:
        return (self.case.id, self.repeat)

    @property
    def image_stem(self) -> str:
        if self.repeats <= 1:
            return self.case.id
        return f"{self.case.id}-r{self.repeat}"


def host_key(url: str) -> str:
    """The pacing key of *url*: its host name without a leading ``www.``."""
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def normalize_browser(name: str) -> str:
    value = name.strip().lower()
    value = _BROWSER_ALIASES.get(value, value)
    if value not in BROWSERS:
        raise CaseError(
            f"unknown browser {name!r} (expected one of {', '.join(BROWSERS)})"
        )
    return value


def parse_browsers(spec: str) -> list[str]:
    """``"chrome,firefox"`` → ``["chrome", "firefox"]`` (aliases applied)."""
    names = [normalize_browser(part) for part in spec.split(",") if part.strip()]
    if not names:
        raise CaseError("no browsers given")
    return list(dict.fromkeys(names))


def parse_wxh(text: str) -> tuple[int, int]:
    """``"1920x1080"`` → ``(1920, 1080)``."""
    match = _WXH_RE.match(text)
    if match is None:
        raise CaseError(f"invalid viewport {text!r} (expected WxH, e.g. 1920x1080)")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise CaseError(f"invalid viewport {text!r} (width and height must be > 0)")
    return width, height


def normalize_device(value: str | None) -> str | None:
    """A device type, or None for ``auto`` / empty (follows the width)."""
    text = (value or "").strip().lower()
    if text in ("", "auto"):
        return None
    if text not in DEVICE_TYPES:
        raise CaseError(
            f"unknown device {value!r} (expected {', '.join(DEVICE_TYPES)} or auto)"
        )
    return text


def device_for_width(width: int) -> str:
    if width >= _DESKTOP_MIN_WIDTH:
        return "desktop"
    if width >= _TABLET_MIN_WIDTH:
        return "tablet"
    return "mobile"


def parse_viewport_specs(spec: str) -> list[tuple[int, int, str | None]]:
    """``"1920x1080:desktop,390x844"`` → ``[(1920, 1080, "desktop"),
    (390, 844, None)]``; duplicates are dropped."""
    items: list[tuple[int, int, str | None]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        size, _, device = part.partition(":")
        width, height = parse_wxh(size)
        items.append((width, height, normalize_device(device)))
    if not items:
        raise CaseError("no viewports given")
    return list(dict.fromkeys(items))


def normalize_wait_until(value: str) -> str:
    text = value.strip().lower()
    if text not in WAIT_UNTIL_LEVELS:
        raise CaseError(
            f"unknown wait_until {value!r} "
            f"(expected one of {', '.join(WAIT_UNTIL_LEVELS)})"
        )
    return text


def parse_timeout_ms(value: str | int) -> int:
    try:
        timeout = int(str(value).strip())
    except ValueError:
        raise CaseError(f"invalid timeout_ms {value!r}") from None
    if timeout <= 0:
        raise CaseError(f"invalid timeout_ms {value!r} (must be > 0)")
    return timeout


def validate_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CaseError(f"invalid URL {url!r} (expected http(s)://host/...)")
    return url


def validate_case_id(case_id: str) -> str:
    if not _ID_RE.match(case_id):
        raise CaseError(
            f"invalid case id {case_id!r} (letters, digits, '.', '_' and '-'; "
            "it names the image file)"
        )
    return case_id


def parse_cases_tsv(
    text: str,
    *,
    wait_until: str = DEFAULT_WAIT_UNTIL,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> list[Case]:
    """Cases of a TSV (columns: :data:`TSV_COLUMNS`).

    Blank lines and lines starting with ``#`` are skipped, as is a header row
    starting with ``id<TAB>url``. Columns after ``url`` are optional: browser
    chrome, viewport 1920x1080, device from the width, *wait_until* and
    *timeout_ms*. Case ids must be unique.
    """
    cases: list[Case] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = [col.strip() for col in raw.split("\t")]
        if [col.lower() for col in cols[:2]] == ["id", "url"]:
            continue
        try:
            cases.append(_case_from_columns(cols, wait_until, timeout_ms))
        except CaseError as exc:
            raise CaseError(f"line {lineno}: {exc}") from None
    duplicates = sorted(
        case_id
        for case_id, count in Counter(case.id for case in cases).items()
        if count > 1
    )
    if duplicates:
        raise CaseError(f"duplicate case ids: {', '.join(duplicates)}")
    return cases


def _case_from_columns(cols: list[str], wait_until: str, timeout_ms: int) -> Case:
    if len(cols) < 2 or not cols[0] or not cols[1]:
        raise CaseError("expected tab-separated columns: " + " ".join(TSV_COLUMNS))
    if len(cols) > len(TSV_COLUMNS):
        raise CaseError(f"{len(cols)} columns, expected at most {len(TSV_COLUMNS)}")
    cols = cols + [""] * (len(TSV_COLUMNS) - len(cols))
    case_id, url, browser, size, device, wait, timeout = cols
    width, height = parse_wxh(size) if size else (1920, 1080)
    return Case(
        id=validate_case_id(case_id),
        url=validate_url(url),
        browser=normalize_browser(browser) if browser else DEFAULT_BROWSER,
        width=width,
        height=height,
        device=normalize_device(device),
        wait_until=normalize_wait_until(wait or wait_until),
        timeout_ms=parse_timeout_ms(timeout) if timeout else timeout_ms,
    )


def parse_url_list(text: str) -> list[str]:
    """URLs of a list file: one per line, ``#`` comments and blanks skipped,
    duplicates dropped."""
    urls: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            urls.append(validate_url(line))
        except CaseError as exc:
            raise CaseError(f"line {lineno}: {exc}") from None
    return list(dict.fromkeys(urls))


def url_slug(url: str) -> str:
    """A file-name-safe id stem for *url*: host (without ``www.``) + path;
    a short URL hash is appended when there is a query or the slug is cut."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"[^A-Za-z0-9]+", "-", parts.path).strip("-")
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", f"{host}-{path}" if path else host)
    slug = slug.strip("-.") or "page"
    if parts.query or len(slug) > _SLUG_MAX:
        digest = hashlib.sha1(url.encode()).hexdigest()[:6]
        slug = f"{slug[:_SLUG_MAX].rstrip('-.')}-{digest}"
    return slug


def dedupe_ids(cases: Iterable[Case]) -> list[Case]:
    """Cases with unique ids: a repeated id gets ``-2``, ``-3``, ..."""
    seen: set[str] = set()
    unique: list[Case] = []
    for case in cases:
        case_id, n = case.id, 1
        while case_id in seen:
            n += 1
            case_id = f"{case.id}-{n}"
        seen.add(case_id)
        unique.append(case if case_id == case.id else _with_id(case, case_id))
    return unique


def _with_id(case: Case, case_id: str) -> Case:
    return Case(
        id=case_id,
        url=case.url,
        browser=case.browser,
        width=case.width,
        height=case.height,
        device=case.device,
        wait_until=case.wait_until,
        timeout_ms=case.timeout_ms,
    )


def cases_from_urls(
    urls: Sequence[str],
    browsers: Sequence[str],
    viewports: Sequence[tuple[int, int, str | None]],
    *,
    wait_until: str = DEFAULT_WAIT_UNTIL,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> list[Case]:
    """Every URL x browser x viewport, with ids ``<slug>-<W>x<H>-<browser>``
    (``-<device>`` added when one size is listed with several devices)."""
    sizes = Counter((width, height) for width, height, _device in viewports)
    cases = []
    for url in urls:
        slug = url_slug(url)
        for browser in browsers:
            for width, height, device in viewports:
                case_id = f"{slug}-{width}x{height}-{browser}"
                if sizes[(width, height)] > 1:
                    case_id += f"-{device or 'auto'}"
                cases.append(
                    Case(
                        id=case_id,
                        url=url,
                        browser=browser,
                        width=width,
                        height=height,
                        device=device,
                        wait_until=wait_until,
                        timeout_ms=timeout_ms,
                    )
                )
    return dedupe_ids(cases)


def filter_cases(
    cases: Iterable[Case],
    *,
    only: str | None = None,
    browsers: Sequence[str] | None = None,
) -> list[Case]:
    """Cases whose id matches one of the comma-separated globs in *only* and
    whose browser is in *browsers* (each filter only when given)."""
    patterns = [part.strip() for part in (only or "").split(",") if part.strip()]
    selected = []
    for case in cases:
        if patterns and not any(fnmatchcase(case.id, p) for p in patterns):
            continue
        if browsers and case.browser not in browsers:
            continue
        selected.append(case)
    return selected


def pending_jobs(
    jobs: Iterable[Job], records: Iterable[Mapping[str, Any]]
) -> list[Job]:
    """*jobs* without the ones *records* already hold (skipped ones run again)."""
    done = {record_key(r) for r in records if not r.get("skipped")}
    return [job for job in jobs if job.key not in done]


def expand_jobs(cases: Sequence[Case], repeat: int = 1) -> list[Job]:
    """All cases once, then all again, ... (*repeat* rounds)."""
    repeats = max(1, repeat)
    return [
        Job(case=case, repeat=round_no, repeats=repeats)
        for round_no in range(1, repeats + 1)
        for case in cases
    ]


# ---------------------------------------------------------------------------
# --set overrides
# ---------------------------------------------------------------------------

_PYTHON_LITERALS = {"True": True, "False": False, "None": None}


def parse_set_value(raw: str) -> Any:
    """JSON when *raw* parses as JSON (``true``, ``3``, ``0.5``, ``[1]``,
    ``"x"``), Python's ``True`` / ``False`` / ``None``, else the string."""
    try:
        return json.loads(raw)
    except ValueError:
        pass
    if raw in _PYTHON_LITERALS:
        return _PYTHON_LITERALS[raw]
    return raw


def parse_set_option(text: str) -> tuple[str, Any]:
    """``"NAME=VALUE"`` → ``(NAME, parsed VALUE)``."""
    name, sep, raw = text.partition("=")
    name = name.strip()
    if not sep or not name.isidentifier():
        raise CaseError(f"--set expects NAME=VALUE, got {text!r}")
    return name, parse_set_value(raw.strip())


def coerce_override(current: Any, value: Any) -> Any:
    """*value* in the container type of *current* (a JSON list for a tuple /
    set / frozenset tunable; an int for a float tunable)."""
    if isinstance(current, tuple | set | frozenset) and isinstance(value, list):
        return type(current)(value)
    if (
        isinstance(current, float)
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return float(value)
    return value


def apply_overrides(module: Any, overrides: Mapping[str, Any]) -> list[str]:
    """``setattr(module, name, value)`` for every override; returns notes on
    names the module does not define and on changed value types."""
    notes: list[str] = []
    for name, value in overrides.items():
        if not hasattr(module, name):
            notes.append(f"{name}: not defined by the module, set anyway")
            setattr(module, name, value)
            continue
        current = getattr(module, name)
        new_value = coerce_override(current, value)
        if current is not None and type(new_value) is not type(current):
            notes.append(
                f"{name}: {type(current).__name__} replaced by "
                f"{type(new_value).__name__} {new_value!r}"
            )
        setattr(module, name, new_value)
    return notes


# ---------------------------------------------------------------------------
# Host pacing and scheduling
# ---------------------------------------------------------------------------


class HostGate:
    """Per-host pacing: with *delay_s* a load of a host starts at least that
    long after the previous one of the same host finished (so loads of one
    host never overlap); with *max_loads* a host gets at most that many loads
    in the run (a case is one load; its retries and reloads are added when it
    finishes)."""

    def __init__(self, delay_s: float = 0.0, max_loads: int | None = None) -> None:
        self.delay_s = max(0.0, float(delay_s or 0.0))
        self.max_loads = max_loads if max_loads and max_loads > 0 else None
        self.loads: Counter[str] = Counter()
        self.running: Counter[str] = Counter()
        self.last_end: dict[str, float] = {}

    def over_limit(self, host: str) -> bool:
        return self.max_loads is not None and self.loads[host] >= self.max_loads

    def ready_at(self, host: str, now: float) -> float:
        """Earliest start time of a load of *host*: *now* when it may start
        now, inf while another load of the host is running (with a delay)."""
        if self.delay_s <= 0:
            return now
        if self.running[host]:
            return math.inf
        last_end = self.last_end.get(host)
        if last_end is None:
            return now
        return max(now, last_end + self.delay_s)

    def start(self, host: str) -> None:
        self.loads[host] += 1
        self.running[host] += 1

    def finish(self, host: str, now: float, extra_loads: int = 0) -> None:
        self.running[host] = max(0, self.running[host] - 1)
        self.loads[host] += max(0, extra_loads)
        self.last_end[host] = now


class Scheduler:
    """Hands out jobs to the bench workers: the first pending job whose host
    may load now; jobs of a host past its load limit come out as skips."""

    def __init__(
        self,
        jobs: Iterable[Job],
        gate: HostGate,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pending = list(jobs)
        self.gate = gate
        self.clock = clock
        self._cond = asyncio.Condition()

    def pick(self, now: float) -> tuple[str, Job] | float | None:
        """``("run", job)`` / ``("skip", job)``, else the time the next job may
        start (inf: when a running load finishes), None when nothing is left."""
        if not self.pending:
            return None
        earliest = math.inf
        for index, job in enumerate(self.pending):
            host = job.case.host
            if self.gate.over_limit(host):
                del self.pending[index]
                return "skip", job
            ready_at = self.gate.ready_at(host, now)
            if ready_at <= now:
                del self.pending[index]
                self.gate.start(host)
                return "run", job
            earliest = min(earliest, ready_at)
        return earliest

    async def next(self) -> tuple[str, Job] | None:
        async with self._cond:
            while True:
                now = self.clock()
                picked = self.pick(now)
                if picked is None or isinstance(picked, tuple):
                    return picked
                timeout = None if math.isinf(picked) else max(0.0, picked - now)
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(timeout):
                        await self._cond.wait()

    async def done(self, job: Job, extra_loads: int = 0) -> None:
        async with self._cond:
            self.gate.finish(job.case.host, self.clock(), extra_loads)
            self._cond.notify_all()


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def jsonable(value: Any, max_chars: int = META_MAX_CHARS) -> Any:
    """A JSON-safe copy of *value*, strings cut to *max_chars*."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return clip(value, max_chars)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item, max_chars) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [jsonable(item, max_chars) for item in items]
    return clip(str(value), max_chars)


def error_class_name(error: BaseException | None) -> str | None:
    """The class name of *error*; Playwright's own errors get a
    ``playwright.`` prefix (its TimeoutError is not the builtin one)."""
    if error is None:
        return None
    cls = type(error)
    if cls.__module__.split(".", 1)[0] == "playwright":
        return f"playwright.{cls.__name__}"
    return cls.__name__


def warning_entries(report: Any) -> list[dict[str, Any]]:
    """``[{code, level, count, details?}]`` of a CaptureReport's warnings
    (warning objects or dicts); details are shortened, without ``count``."""
    entries = []
    for warning in getattr(report, "warnings", None) or ():
        if isinstance(warning, Mapping):
            code = warning.get("code")
            level = warning.get("level")
            details = warning.get("details")
        else:
            code = getattr(warning, "code", None)
            level = getattr(warning, "level", None)
            details = getattr(warning, "details", None)
        if not code:
            continue
        details = details if isinstance(details, Mapping) else {}
        count = details.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int):
            count = 1
        entry: dict[str, Any] = {
            "code": str(code),
            "level": str(level or "warning"),
            "count": count,
        }
        brief = _brief_details(details)
        if brief:
            entry["details"] = brief
        entries.append(entry)
    return entries


def _brief_details(details: Mapping[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {}
    for key, value in details.items():
        if key == "count":
            continue
        if key == "samples" and isinstance(value, list | tuple):
            value = list(value)[:DETAIL_MAX_SAMPLES]
        brief[str(key)] = jsonable(value, DETAIL_MAX_CHARS)
    return brief


def timings_of(report: Any) -> dict[str, int]:
    timings = getattr(report, "timings_ms", None)
    if not isinstance(timings, Mapping):
        return {}
    return {
        str(name): round(value)
        for name, value in timings.items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }


def meta_subset(report: Any) -> dict[str, Any]:
    """The :data:`META_SECTIONS` of the report's capture meta."""
    meta = getattr(report, "meta", None)
    if not isinstance(meta, Mapping):
        return {}
    return {name: jsonable(meta[name]) for name in META_SECTIONS if name in meta}


def warning_codes(record: Mapping[str, Any]) -> list[str]:
    codes = []
    for warning in record.get("warnings") or ():
        if isinstance(warning, Mapping) and warning.get("code"):
            codes.append(str(warning["code"]))
    return codes


def meta_value(record: Mapping[str, Any], section: str, key: str) -> Any:
    meta = record.get("meta")
    if not isinstance(meta, Mapping):
        return None
    values = meta.get(section)
    return values.get(key) if isinstance(values, Mapping) else None


def is_unstable(record: Mapping[str, Any]) -> bool:
    return bool(record.get("captured_with_unstable_dom")) or (
        "dom_unstable" in warning_codes(record)
    )


def reload_count(record: Mapping[str, Any]) -> int:
    """Reloads of a case: meta.navigation.reloads or the page_reloaded count."""
    reloads = meta_value(record, "navigation", "reloads")
    count = reloads if isinstance(reloads, int) and not isinstance(reloads, bool) else 0
    for warning in record.get("warnings") or ():
        if isinstance(warning, Mapping) and warning.get("code") == "page_reloaded":
            count = max(count, int(warning.get("count") or 1))
    return count


def extra_loads(record: Mapping[str, Any]) -> int:
    """Loads of a finished case beyond the first: retries and reloads."""
    attempts = record.get("attempts")
    retries = attempts - 1 if isinstance(attempts, int) and attempts > 1 else 0
    return retries + reload_count(record)


def viewport_width(record: Mapping[str, Any]) -> int | None:
    viewport = record.get("viewport")
    if isinstance(viewport, Mapping):
        width = viewport.get("width")
        return width if isinstance(width, int) else None
    return None


def record_key(record: Mapping[str, Any]) -> tuple[str, int]:
    repeat = record.get("repeat")
    return (str(record.get("id")), repeat if isinstance(repeat, int) else 1)


def read_results(out: Path) -> list[dict[str, Any]]:
    """The records of ``<out>/results.jsonl``, one per (id, repeat): a later
    line replaces an earlier one (a resumed run re-runs skipped cases).
    Unreadable lines are skipped."""
    path = Path(out) / RESULTS_FILE
    if not path.exists():
        return []
    records: dict[tuple[str, int], dict[str, Any]] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            print(f"{path}:{lineno}: skipping an unreadable line", file=sys.stderr)
            continue
        if isinstance(record, dict) and record.get("id"):
            records[record_key(record)] = record
    return list(records.values())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def percentile(values: Iterable[float | int | None], q: float) -> float | None:
    """The *q*-th percentile (0-100) with linear interpolation; None when
    there are no values."""
    data = sorted(
        float(value)
        for value in values
        if isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
    if not data:
        return None
    position = (len(data) - 1) * min(max(q, 0.0), 100.0) / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    return data[lower] + (data[upper] - data[lower]) * (position - lower)


def stats(values: Iterable[float | int | None], digits: int = 0) -> dict[str, Any]:
    """``{n, p50, p90, max}`` of *values*, rounded to *digits*."""
    data = [
        value
        for value in values
        if isinstance(value, int | float) and not isinstance(value, bool)
    ]

    def rounded(value: float | None) -> float | int | None:
        if value is None:
            return None
        return round(value) if digits == 0 else round(value, digits)

    return {
        "n": len(data),
        "p50": rounded(percentile(data, 50)),
        "p90": rounded(percentile(data, 90)),
        "max": rounded(max(data)) if data else None,
    }


def ratio(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def phase_sort_key(name: str) -> tuple[int, str]:
    if name in PHASE_ORDER:
        return (PHASE_ORDER.index(name), name)
    return (len(PHASE_ORDER), name)


def group_key(record: Mapping[str, Any]) -> str:
    return f"{record.get('browser') or '?'}/{record.get('device') or '?'}"


def group_sort_key(key: str) -> tuple[int, int, str]:
    browser, _, device = key.partition("/")
    browser_rank = BROWSERS.index(browser) if browser in BROWSERS else len(BROWSERS)
    device_rank = DEVICE_TYPES.index(device) if device in DEVICE_TYPES else 9
    return (browser_rank, device_rank, key)


def group_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts, timing percentiles and quality metrics of *records*.

    Timings are over the ok cases (``wall_s``: every case that ran); warnings
    count the cases that carry each code; ``fast_path`` is over the stable ok
    cases (``known``: how many of them report meta.readiness.fast_path);
    ``width_eq_viewport`` is over the ok cases with an image.
    """
    executed = [r for r in records if not r.get("skipped")]
    ok = [r for r in executed if r.get("ok")]
    failed = [r for r in executed if not r.get("ok")]
    failed_by_class = Counter(str(r.get("error_class") or "Unknown") for r in failed)

    phases: defaultdict[str, list[int]] = defaultdict(list)
    for record in ok:
        for name, value in (record.get("timings_ms") or {}).items():
            if name != "total":
                phases[name].append(value)

    stable_ok = [r for r in ok if not is_unstable(r)]
    fast_known = [
        r
        for r in stable_ok
        if isinstance(r.get("meta"), Mapping)
        and isinstance(r["meta"].get("readiness"), Mapping)
        and "fast_path" in r["meta"]["readiness"]
    ]
    fast = sum(1 for r in fast_known if r["meta"]["readiness"]["fast_path"] is True)
    with_image = [r for r in ok if isinstance(r.get("image_width"), int)]
    width_eq = sum(1 for r in with_image if r["image_width"] == viewport_width(r))
    warnings = Counter(code for r in executed for code in set(warning_codes(r)))
    return {
        "cases": len(records),
        "ok": len(ok),
        "failed": len(failed),
        "skipped": len(records) - len(executed),
        "failed_by_class": dict(sorted(failed_by_class.items())),
        "total_ms": stats((r.get("timings_ms") or {}).get("total") for r in ok),
        "phases_ms": {
            name: stats(phases[name]) for name in sorted(phases, key=phase_sort_key)
        },
        "duration_s": stats((r.get("duration") for r in ok), digits=3),
        "wall_s": stats((r.get("wall_s") for r in executed), digits=3),
        "unstable": sum(1 for r in ok if is_unstable(r)),
        "fast_path": {
            "count": fast,
            "of": len(stable_ok),
            "known": len(fast_known),
            "rate": ratio(fast, len(stable_ok)),
        },
        "warnings": dict(sorted(warnings.items())),
        "reloads": sum(1 for r in executed if reload_count(r) > 0),
        "screenshot_retries": sum(
            1 for r in executed if "screenshot_retried" in warning_codes(r)
        ),
        "retried": sum(1 for r in executed if (r.get("attempts") or 0) > 1),
        "width_eq_viewport": {
            "count": width_eq,
            "of": len(with_image),
            "rate": ratio(width_eq, len(with_image)),
        },
    }


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """``{overall, groups: {"<browser>/<device>": ...}, failures, skipped}``;
    see :func:`group_stats` for the metrics."""
    records = list(records)
    groups: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups[group_key(record)].append(record)
    failures = [
        {
            "id": r.get("id"),
            "repeat": r.get("repeat", 1),
            "group": group_key(r),
            "url": r.get("url"),
            "error_class": r.get("error_class"),
            "error": r.get("error"),
            "phase": r.get("phase"),
            "attempts": r.get("attempts"),
        }
        for r in records
        if not r.get("skipped") and not r.get("ok")
    ]
    skipped = [
        {"id": r.get("id"), "repeat": r.get("repeat", 1), "reason": r.get("error")}
        for r in records
        if r.get("skipped")
    ]
    return {
        "overall": group_stats(records),
        "groups": {
            key: group_stats(groups[key]) for key in sorted(groups, key=group_sort_key)
        },
        "failures": failures,
        "skipped": skipped,
    }


def fmt_seconds(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f}"


def fmt_count(part: Mapping[str, Any]) -> str:
    return f"{part.get('count', 0)}/{part.get('of', 0)}"


def text_table(rows: Sequence[Sequence[str]], right: Iterable[int] = ()) -> list[str]:
    """Rows of cells aligned in columns; the column indexes in *right* are
    right-aligned."""
    if not rows:
        return []
    right_cols = set(right)
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for row in rows:
        cells = [
            cell.rjust(widths[i]) if i in right_cols else cell.ljust(widths[i])
            for i, cell in enumerate(row)
        ]
        lines.append("  ".join(cells).rstrip())
    return lines


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def format_summary(summary: Mapping[str, Any]) -> str:
    """A compact text version of a summary (as written to summary.json)."""
    run = summary.get("run") or {}
    overall = summary["overall"]
    head = f"capture bench {run['label']}" if run.get("label") else "capture bench"
    head += (
        f": {overall['cases']} cases, ok {overall['ok']}, "
        f"failed {overall['failed']}, skipped {overall['skipped']}"
    )
    if run.get("elapsed_s") is not None:
        head += f", elapsed {format_elapsed(run['elapsed_s'])}"
    lines = [head]
    if run.get("note"):
        lines.append(f"note: {run['note']}")
    if run.get("browser_versions"):
        versions = ", ".join(f"{k} {v}" for k, v in run["browser_versions"].items())
        lines.append(f"browsers: {versions}")
    if run.get("overrides"):
        lines.append(f"overrides: {json.dumps(run['overrides'], sort_keys=True)}")
    groups = [*summary["groups"].items(), ("overall", overall)]
    rows = [
        [
            "group",
            "cases",
            "ok",
            "fail",
            "skip",
            "total p50/p90 s",
            "wall p90 s",
            "unstable",
            "fast-path",
            "w=vp",
            "reloads",
            "shot-retry",
        ]
    ]
    for name, group in groups:
        fast = fmt_count(group["fast_path"])
        if group["fast_path"].get("of") and not group["fast_path"].get("known"):
            fast += " n/a"
        rows.append(
            [
                name,
                str(group["cases"]),
                str(group["ok"]),
                str(group["failed"]),
                str(group["skipped"]),
                f"{fmt_seconds(group['total_ms']['p50'])}/"
                f"{fmt_seconds(group['total_ms']['p90'])}",
                "-"
                if group["wall_s"]["p90"] is None
                else f"{group['wall_s']['p90']:.1f}",
                str(group["unstable"]),
                fast,
                fmt_count(group["width_eq_viewport"]),
                str(group["reloads"]),
                str(group["screenshot_retries"]),
            ]
        )
    lines += text_table(rows, right=range(1, 12))
    lines.append("phases p50/p90 ms (ok cases):")
    for name, group in groups:
        phases = " ".join(
            f"{phase} {values['p50']}/{values['p90']}"
            for phase, values in group["phases_ms"].items()
        )
        lines.append(f"  {name}: {phases or '-'}")
    if overall["warnings"]:
        codes = ", ".join(f"{code} {n}" for code, n in overall["warnings"].items())
        lines.append(f"warnings (cases): {codes}")
    if overall["failed_by_class"]:
        classes = ", ".join(
            f"{cls} {n}" for cls, n in overall["failed_by_class"].items()
        )
        lines.append(f"failed by class: {classes}")
    for failure in summary.get("failures") or ():
        lines.append(
            f"  FAIL {failure['id']} [{failure['group']}] "
            f"{failure['error_class']}: {clip(str(failure['error'] or ''), 140)}"
        )
    for skip in summary.get("skipped") or ():
        repeat = f" r{skip['repeat']}" if (skip.get("repeat") or 1) > 1 else ""
        lines.append(f"  skip {skip['id']}{repeat}: {skip['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def prepare_app_env(endpoint: str | None) -> None:
    """Environment for importing the app: BROWSER_WS_ENDPOINT from
    *endpoint*, placeholders for the required settings the bench never uses
    (only when there is no ``.env`` to read them from)."""
    if endpoint is not None:
        os.environ["BROWSER_WS_ENDPOINT"] = endpoint
    if Path(".env").is_file():
        return
    for name, value in _APP_ENV_PLACEHOLDERS.items():
        os.environ.setdefault(name, value)


def call_accepting(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """``func(*args, **kwargs)`` without the keyword arguments *func* does not
    accept — tolerates older signatures of the capture API."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return func(*args, **kwargs)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return func(*args, **kwargs)
    return func(*args, **{k: v for k, v in kwargs.items() if k in params})


def _is_connected(browser: Any) -> bool:
    check = getattr(browser, "is_connected", None)
    if not callable(check):
        return True
    try:
        return bool(check())
    except Exception:
        return False


class BrowserPool:
    """One shared Browser per engine, reconnected when it dropped."""

    def __init__(self, pw: Any, launch: Callable[[Any, str], Awaitable[Any]]) -> None:
        self._pw = pw
        self._launch = launch
        self._browsers: dict[str, Any] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get(self, name: str) -> Any:
        async with self._locks[name]:
            browser = self._browsers.get(name)
            if browser is not None and _is_connected(browser):
                return browser
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            browser = await self._launch(self._pw, name)
            self._browsers[name] = browser
            return browser

    def provider(self, name: str) -> Callable[[], Awaitable[Any]]:
        """A pipeline ``browser_provider``: the engine's browser, reconnected
        if needed, or None when that fails."""

        async def provide() -> Any:
            try:
                return await self.get(name)
            except Exception as exc:
                logger.warning("reconnecting %s failed: %s", name, exc)
                return None

        return provide

    async def close(self) -> None:
        for browser in self._browsers.values():
            with contextlib.suppress(Exception):
                await browser.close()
        self._browsers.clear()


def code_fingerprint() -> str | None:
    """A short hash of the capture package sources: tells runs of different
    code apart."""
    try:
        import app.screenshots.capture as capture_package

        root = Path(capture_package.__file__).parent
        digest = hashlib.sha1()
        for path in sorted(root.glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()[:12]
    except Exception:
        return None


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Bench:
    """Runs the jobs of one bench invocation and writes the results."""

    def __init__(self, args: argparse.Namespace, jobs: list[Job]) -> None:
        from app.screenshots.capture import pipeline, profiles
        from app.screenshots.capture.config import EffectiveConfig

        self.args = args
        self.out: Path = args.out
        self.jobs = jobs
        self.pipeline = pipeline
        self.profiles = profiles
        self.effective_config = EffectiveConfig
        self.ext = "jpg" if args.fmt == "jpeg" else "png"
        self.done = 0
        self.lock = asyncio.Lock()
        self.browser_versions: dict[str, str] = {}

    async def run(self, pool: BrowserPool) -> None:
        gate = HostGate(self.args.host_delay, self.args.max_loads_per_host)
        scheduler = Scheduler(self.jobs, gate)
        workers = [
            asyncio.create_task(self._worker(scheduler, gate, pool))
            for _ in range(max(1, self.args.concurrency))
        ]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self, scheduler: Scheduler, gate: HostGate, pool: Any) -> None:
        while True:
            picked = await scheduler.next()
            if picked is None:
                return
            action, job = picked
            if action == "skip":
                reason = (
                    f"host limit: {job.case.host} had its {gate.max_loads} "
                    "load(s) (--max-loads-per-host)"
                )
                await self._emit(self._skipped_record(job, reason))
                continue
            try:
                record = await self._run_job(job, pool)
            except Exception as exc:  # a bench bug must not end the run
                logger.exception("bench error on %s", job.case.id)
                record = self._base_record(job)
                record.update(
                    ok=False,
                    skipped=False,
                    error_class=error_class_name(exc),
                    error=clip(f"bench error: {exc}", ERROR_MAX_CHARS),
                    wall_s=0,
                )
            await scheduler.done(job, extra_loads(record))
            await self._emit(record)

    def _base_record(self, job: Job) -> dict[str, Any]:
        case = job.case
        return {
            "id": case.id,
            "repeat": job.repeat,
            "url": case.url,
            "host": case.host,
            "browser": case.browser,
            "viewport": case.viewport,
            "device": case.device or device_for_width(case.width),
            "wait_until": case.wait_until,
            "timeout_ms": case.timeout_ms,
            "started_at": _utc_now(),
        }

    def _skipped_record(self, job: Job, reason: str) -> dict[str, Any]:
        record = self._base_record(job)
        record.update(
            ok=False, skipped=True, error_class=SKIPPED_CLASS, error=reason, wall_s=0
        )
        return record

    async def _run_job(self, job: Job, pool: BrowserPool) -> dict[str, Any]:
        case = job.case
        record = self._base_record(job)
        record.update(ok=False, skipped=False, error_class=None, error=None)
        started = time.monotonic()
        try:
            browser = await pool.get(case.browser)
        except Exception as exc:
            record.update(
                error_class=error_class_name(exc),
                error=clip(f"browser connect failed: {exc}", ERROR_MAX_CHARS),
                phase="connect",
                wall_s=round(time.monotonic() - started, 3),
            )
            return record
        version = getattr(browser, "version", None)
        record["browser_version"] = version
        if version:
            self.browser_versions[case.browser] = version
        profiles = self.profiles
        resolve = getattr(profiles, "resolve_device_type", None)
        device = (
            resolve(case.viewport, case.device)
            if callable(resolve)
            else case.device or device_for_width(case.width)
        )
        record["device"] = device
        profile = None
        build = getattr(profiles, "build_profile", None)
        if callable(build):
            profile = call_accepting(
                build, case.browser, device, browser_version=version
            )
            profile_meta = getattr(profile, "meta", None)
            if callable(profile_meta):
                record["profile"] = jsonable(profile_meta())
        config = self.effective_config(
            browser=case.browser,
            fmt=self.args.fmt,
            quality=self.args.quality,
            wait_until=case.wait_until,
            timeout=case.timeout_ms,
        )
        timeout_cm = asyncio.timeout(self.args.case_timeout)
        try:
            async with timeout_cm:
                result = await call_accepting(
                    self.pipeline.capture_with_retries,
                    browser=browser,
                    url=case.url,
                    viewport=case.viewport,
                    config=config,
                    profile=profile,
                    browser_provider=pool.provider(case.browser),
                )
        except TimeoutError:
            if not timeout_cm.expired():
                raise
            record.update(
                error_class="BenchCaseTimeout",
                error=f"case did not finish within {self.args.case_timeout:g} s "
                "(--case-timeout)",
            )
        except Exception as exc:
            record.update(
                error_class=error_class_name(exc),
                error=clip(str(exc) or repr(exc), ERROR_MAX_CHARS),
            )
        else:
            await self._fill_from_result(record, job, result)
        record["wall_s"] = round(time.monotonic() - started, 3)
        return record

    async def _fill_from_result(
        self, record: dict[str, Any], job: Job, result: Any
    ) -> None:
        error = getattr(result, "error", None)
        outcome = getattr(result, "outcome", None)
        ok = getattr(result, "ok", None)
        if not isinstance(ok, bool):
            ok = error is None and outcome is not None
        report = getattr(result, "report", None) or getattr(outcome, "report", None)
        record.update(
            ok=ok,
            error_class=None if ok else (error_class_name(error) or "Unknown"),
            error=None
            if ok
            else clip(str(error) or repr(error) or "failed", ERROR_MAX_CHARS),
            phase=None if ok else getattr(report, "current_phase", None),
            attempts=getattr(result, "attempts", None),
            duration=getattr(result, "duration", None),
            captured_with_unstable_dom=bool(
                getattr(outcome, "captured_with_unstable_dom", False)
            ),
            timings_ms=timings_of(report),
            warnings=warning_entries(report),
            meta=meta_subset(report),
        )
        data = getattr(outcome, "data", None) if ok else None
        if not isinstance(data, bytes | bytearray) or not data:
            return
        image = f"{IMAGE_DIR}/{job.image_stem}.{self.ext}"
        await asyncio.to_thread((self.out / image).write_bytes, bytes(data))
        size = _image_size(bytes(data))
        record.update(
            image=image,
            image_width=size[0] if size else None,
            image_height=size[1] if size else None,
            bytes=len(data),
        )

    async def _emit(self, record: dict[str, Any]) -> None:
        async with self.lock:
            with (self.out / RESULTS_FILE).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.done += 1
            print(_progress_line(record, self.done, len(self.jobs)), file=sys.stderr)


def _image_size(data: bytes) -> tuple[int, int] | None:
    try:
        from app.screenshots.image_dimensions import read_image_dimensions_from_bytes
    except ImportError:
        return None
    return read_image_dimensions_from_bytes(data)


def _progress_line(record: Mapping[str, Any], done: int, total: int) -> str:
    width = len(str(total))
    if record.get("skipped"):
        status, detail = "skip", str(record.get("error") or "")
    elif record.get("ok"):
        status = "ok"
        codes = warning_codes(record)
        size = f"{record.get('image_width')}x{record.get('image_height')}"
        detail = size + (f"  warnings: {', '.join(codes)}" if codes else "")
    else:
        status = "FAIL"
        detail = f"{record.get('error_class')}: {clip(str(record.get('error')), 120)}"
    wall = record.get("wall_s") or 0
    return (
        f"[{done:>{width}}/{total}] {status:<4} {wall:7.1f}s  "
        f"{record.get('id')}"
        + (f" r{record['repeat']}" if (record.get("repeat") or 1) > 1 else "")
        + f"  {detail}"
    )


def load_cases(args: argparse.Namespace) -> list[Case]:
    browsers = parse_browsers(args.browsers) if args.browsers else None
    if args.cases:
        cases = parse_cases_tsv(
            Path(args.cases).read_text(encoding="utf-8"),
            wait_until=args.wait_until,
            timeout_ms=args.timeout,
        )
        cases = filter_cases(cases, only=args.only, browsers=browsers)
    else:
        urls = parse_url_list(Path(args.urls).read_text(encoding="utf-8"))
        cases = cases_from_urls(
            urls,
            browsers or [DEFAULT_BROWSER],
            parse_viewport_specs(args.viewports),
            wait_until=args.wait_until,
            timeout_ms=args.timeout,
        )
        cases = filter_cases(cases, only=args.only)
    if not cases:
        raise CaseError("no cases to run")
    return cases


def run_info(args: argparse.Namespace, overrides: Mapping[str, Any]) -> dict[str, Any]:
    source: dict[str, Any]
    if args.cases:
        source = {"cases": str(args.cases)}
    else:
        source = {"urls": str(args.urls), "viewports": args.viewports}
    if args.browsers:
        source["browsers"] = args.browsers
    if args.only:
        source["only"] = args.only
    return {
        "label": Path(args.out).name,
        "note": args.note,
        "source": source,
        "repeat": args.repeat,
        "concurrency": args.concurrency,
        "host_delay_s": args.host_delay,
        "max_loads_per_host": args.max_loads_per_host or None,
        "fmt": args.fmt,
        "quality": args.quality,
        "overrides": jsonable(dict(overrides)),
        "endpoint": os.environ.get("BROWSER_WS_ENDPOINT", ""),
    }


def write_summary(out: Path, run: Mapping[str, Any]) -> dict[str, Any]:
    summary = {"run": dict(run), **summarize(read_results(out))}
    (out / SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


async def run_bench(
    args: argparse.Namespace,
    jobs: list[Job],
    overrides: Mapping[str, Any],
    run: dict[str, Any],
) -> int:
    from playwright.async_api import async_playwright

    from app.screenshots.capture import config as capture_config
    from app.tasks.worker import _launch_browser

    for note in apply_overrides(capture_config, overrides):
        print(f"--set {note}", file=sys.stderr)
    run["code_fingerprint"] = code_fingerprint()
    if not os.environ.get("BROWSER_WS_ENDPOINT"):
        print("BROWSER_WS_ENDPOINT is empty: launching local browsers", file=sys.stderr)
    bench = Bench(args, jobs)
    async with async_playwright() as pw:
        pool = BrowserPool(pw, _launch_browser)
        try:
            for engine in sorted(
                {job.case.browser for job in jobs}, key=BROWSERS.index
            ):
                try:
                    browser = await pool.get(engine)
                except Exception as exc:
                    print(f"cannot connect to {engine}: {exc}", file=sys.stderr)
                    return 1
                bench.browser_versions[engine] = getattr(browser, "version", "")
            run["browser_versions"] = bench.browser_versions
            versions = ", ".join(
                f"{name} {version}" for name, version in bench.browser_versions.items()
            )
            print(
                f"capture bench: {len(jobs)} cases -> {args.out} ({versions})",
                file=sys.stderr,
            )
            await bench.run(pool)
        finally:
            await pool.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.capture_bench",
        description="Run the capture pipeline over bench cases and record "
        "timings, warnings, capture metadata and images.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--cases",
        type=Path,
        help="TSV: id url browser WxH device wait_until timeout_ms ('#' comments)",
    )
    source.add_argument("--urls", type=Path, help="file with one URL per line")
    parser.add_argument(
        "--browsers",
        help="comma list for --urls (default chrome); with --cases it filters rows",
    )
    parser.add_argument(
        "--viewports",
        default=DEFAULT_VIEWPORTS,
        help="viewports for --urls: WxH[:device],... (default %(default)s)",
    )
    parser.add_argument(
        "--wait-until",
        default=DEFAULT_WAIT_UNTIL,
        choices=WAIT_UNTIL_LEVELS,
        help="default wait_until (default %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="default capture timeout in ms (default %(default)s)",
    )
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--endpoint",
        help="browser server base URL without port, e.g. ws://browser "
        "(default: BROWSER_WS_ENDPOINT)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=1, help="cases at once (default 1)"
    )
    parser.add_argument(
        "--host-delay",
        type=float,
        default=0.0,
        help="seconds between the end of a load and the next load of the same host",
    )
    parser.add_argument(
        "--max-loads-per-host",
        type=int,
        default=0,
        help="loads per host in this run; later cases are skipped (0 = no limit)",
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="rounds over all cases (default 1)"
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override app.screenshots.capture.config.NAME (VALUE as JSON if "
        "possible); repeatable",
    )
    parser.add_argument("--fmt", choices=("jpeg", "png"), default="jpeg")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    parser.add_argument("--only", help="comma list of case-id globs to run")
    parser.add_argument(
        "--case-timeout",
        type=float,
        default=DEFAULT_CASE_TIMEOUT_S,
        help="bench safety net per case in seconds (default %(default)s)",
    )
    parser.add_argument("--note", help="free text stored in summary.json")
    existing = parser.add_mutually_exclusive_group()
    existing.add_argument(
        "--resume",
        action="store_true",
        help="keep results.jsonl and run only the cases missing from it "
        "(or skipped there)",
    )
    existing.add_argument(
        "--overwrite",
        action="store_true",
        help="discard the results and images already in --out",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="only rebuild summary.json from results.jsonl and print it",
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not (args.cases or args.urls):
        parser.error("one of --cases / --urls is required")
    if args.concurrency < 1 or args.repeat < 1:
        parser.error("--concurrency and --repeat must be >= 1")
    if args.timeout <= 0 or args.case_timeout <= 0:
        parser.error("--timeout and --case-timeout must be > 0")
    if args.host_delay < 0 or args.max_loads_per_host < 0:
        parser.error("--host-delay and --max-loads-per-host must be >= 0")
    if not 0 <= args.quality <= 100:
        parser.error("--quality must be within 0-100")
    if args.endpoint:
        parts = urlsplit(args.endpoint)
        try:
            port = parts.port
        except ValueError:
            port = -1
        if (
            parts.scheme not in ("ws", "wss")
            or not parts.hostname
            or port is not None
            or parts.path.strip("/")
        ):
            parser.error("--endpoint must be ws(s)://host without port or path")


def _clear_outputs(out: Path) -> None:
    for name in (RESULTS_FILE, SUMMARY_FILE):
        (out / name).unlink(missing_ok=True)
    image_dir = out / IMAGE_DIR
    if image_dir.is_dir():
        for path in image_dir.iterdir():
            if path.suffix in (".jpg", ".png") and path.is_file():
                path.unlink()


def _previous_run_info(out: Path) -> dict[str, Any]:
    """The ``run`` section of an existing summary.json, else just the label."""
    try:
        stored = json.loads((out / SUMMARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = None
    if isinstance(stored, dict) and isinstance(stored.get("run"), dict):
        return stored["run"]
    return {"label": out.name}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out: Path = args.out
    if args.summarize:
        if not (out / RESULTS_FILE).exists():
            parser.error(f"{out / RESULTS_FILE} does not exist")
        print(format_summary(write_summary(out, _previous_run_info(out))))
        return 0

    _validate_args(parser, args)
    try:
        cases = load_cases(args)
        overrides = dict(parse_set_option(item) for item in args.overrides)
    except (CaseError, OSError) as exc:
        parser.error(str(exc))
    jobs = expand_jobs(cases, args.repeat)
    results = out / RESULTS_FILE
    resumed = False
    if results.exists() and results.stat().st_size:
        if args.resume:
            resumed = True
            jobs = pending_jobs(jobs, read_results(out))
        elif args.overwrite:
            _clear_outputs(out)
        else:
            parser.error(
                f"{results} already exists: use --resume, --overwrite or another --out"
            )
    (out / IMAGE_DIR).mkdir(parents=True, exist_ok=True)
    if not jobs:
        print("nothing to run: every case is in results.jsonl", file=sys.stderr)
        print(format_summary(write_summary(out, _previous_run_info(out))))
        return 0

    prepare_app_env(args.endpoint)
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    run = run_info(args, overrides)
    if resumed:
        run["resumed_from"] = _previous_run_info(out).get("started_at")
    run["started_at"] = _utc_now()
    started = time.monotonic()
    code = 130
    try:
        code = asyncio.run(run_bench(args, jobs, overrides, run))
    except KeyboardInterrupt:
        print("interrupted: summarizing the finished cases", file=sys.stderr)
    finally:
        run["finished_at"] = _utc_now()
        run["elapsed_s"] = round(time.monotonic() - started, 1)
        if (out / RESULTS_FILE).exists():
            print(format_summary(write_summary(out, run)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
