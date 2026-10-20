"""Capture report: typed warnings, capture metadata and phase timings.

Pure Python. ``warn()`` accepts only the codes in :data:`WARNING_CODES`; a
repeated code is merged into the first warning of that code (``count`` goes up,
``samples`` are appended, at most :data:`MAX_SAMPLES`). ``to_db()`` returns the
bounded, JSON-safe ``(warnings, capture_meta)`` pair a screenshot row stores.
"""

import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

WarningLevel = Literal["info", "warning"]

# code -> default level. warn() rejects every other code.
WARNING_CODES: dict[str, WarningLevel] = {
    "http_error": "warning",
    "redirected": "info",
    "challenge_passed": "info",
    "fonts_timeout": "warning",
    "assets_pending": "warning",
    "assets_missing": "warning",
    "styles_missing": "warning",
    "assets_recovered": "info",
    "page_reloaded": "info",
    "recovery_skipped": "info",
    "dom_unstable": "warning",
    "overflow_cropped": "info",
    "screenshot_retried": "info",
    "inner_scroll_expanded": "info",
    "infinite_scroll_stopped": "warning",
    "overlays_hidden": "info",
    "carousels_paused": "info",
}

META_VERSION = 1
MAX_WARNINGS = 20
MAX_SAMPLES = 10
MAX_STRING_LENGTH = 300
# Detail keys owned by the merge logic.
_RESERVED_DETAILS = frozenset({"count", "samples"})
_LEVELS: frozenset[str] = frozenset({"info", "warning"})


@dataclass
class CaptureWarning:
    code: str
    level: WarningLevel
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "message": _clip(self.message),
            "details": _clip(self.details),
        }


@dataclass
class CaptureReport:
    """Everything one capture attempt learned besides the image itself."""

    warnings: list[CaptureWarning] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)
    current_phase: str | None = None

    def warn(
        self,
        code: str,
        message: str,
        *,
        level: WarningLevel | None = None,
        **details: Any,
    ) -> CaptureWarning:
        """Record warning *code*; *level* defaults to the code's registry level.

        Repeating a code merges into its first warning: ``details["count"]``
        goes up by one, ``samples`` (a list, or a single sample) are appended
        up to MAX_SAMPLES, other detail keys keep their first value (new keys
        are added), and the level escalates to "warning" when either call is a
        warning. The message stays the first one.
        """
        default_level = WARNING_CODES.get(code)
        if default_level is None:
            raise ValueError(f"unknown capture warning code: {code!r}")
        if level is not None and level not in _LEVELS:
            raise ValueError(f"unknown capture warning level: {level!r}")
        if "count" in details:
            raise ValueError("'count' is a reserved warning detail")
        resolved_level: WarningLevel = level or default_level
        samples = _as_samples(details.pop("samples", None))

        existing = self.warning(code)
        if existing is None:
            warning = CaptureWarning(
                code=code,
                level=resolved_level,
                message=message,
                details={**details, "count": 1},
            )
            if samples:
                warning.details["samples"] = samples[:MAX_SAMPLES]
            self.warnings.append(warning)
            return warning

        existing.details["count"] = int(existing.details.get("count", 1)) + 1
        if samples:
            kept = existing.details.setdefault("samples", [])
            kept.extend(samples[: max(0, MAX_SAMPLES - len(kept))])
        for key, value in details.items():
            existing.details.setdefault(key, value)
        if resolved_level == "warning":
            existing.level = "warning"
        return existing

    def warning(self, code: str) -> CaptureWarning | None:
        """The recorded warning for *code*, if any."""
        for warning in self.warnings:
            if warning.code == code:
                return warning
        return None

    def warning_codes(self) -> list[str]:
        return [warning.code for warning in self.warnings]

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time the block into ``timings_ms[name]`` (integer ms, accumulated
        over repeated entries) and make *name* the ``current_phase``.

        On a clean exit the previous phase becomes current again; when the
        block raises (a timeout cancellation included) *name* stays current, so
        the caller can report the phase the failure happened in.
        """
        previous = self.current_phase
        self.current_phase = name
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.timings_ms[name] = self.timings_ms.get(name, 0) + elapsed_ms
        self.current_phase = previous

    def set_meta(self, section: str, **values: Any) -> dict[str, Any]:
        """Merge *values* into ``meta[section]`` and return that section."""
        target = self.meta.setdefault(section, {})
        if not isinstance(target, dict):
            raise ValueError(f"capture meta {section!r} is not a section")
        target.update(values)
        return target

    def to_db(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """``(warnings, capture_meta)`` for the screenshot row: at most
        MAX_WARNINGS warnings, strings cut to MAX_STRING_LENGTH characters,
        only JSON types; meta carries ``v`` and ``timings_ms``. Both are new
        objects, independent of the report."""
        warnings = [warning.to_dict() for warning in self.warnings[:MAX_WARNINGS]]
        meta = _clip(
            {"v": META_VERSION, **self.meta, "timings_ms": dict(self.timings_ms)}
        )
        return warnings, meta


def _as_samples(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return list(value)
    return [value]


def _clip(value: Any) -> Any:
    """JSON-safe copy of *value* with every string cut to MAX_STRING_LENGTH."""
    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return value[: MAX_STRING_LENGTH - 3] + "..."
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _clip(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_clip(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_clip(item) for item in sorted(value, key=str)]
    return _clip(str(value))
