import json
from unittest.mock import patch

import pytest

from app.screenshots.capture import report as report_module
from app.screenshots.capture.report import (
    MAX_SAMPLES,
    MAX_STRING_LENGTH,
    MAX_WARNINGS,
    WARNING_CODES,
    CaptureReport,
    CaptureWarning,
)


def test_registry_has_exactly_the_planned_codes_and_levels() -> None:
    assert WARNING_CODES == {
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


def test_warn_rejects_unknown_codes_levels_and_reserved_details() -> None:
    report = CaptureReport()

    with pytest.raises(ValueError, match="unknown capture warning code"):
        report.warn("not_a_code", "nope")
    with pytest.raises(ValueError, match="level"):
        report.warn("http_error", "nope", level="error")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="count"):
        report.warn("http_error", "nope", count=3)

    assert report.warnings == []


def test_warn_takes_the_registry_level_unless_overridden() -> None:
    report = CaptureReport()

    http = report.warn("http_error", "HTTP 404", status=404)
    cropped = report.warn("overflow_cropped", "cropped to 390 px", level="warning")

    assert http == CaptureWarning(
        code="http_error",
        level="warning",
        message="HTTP 404",
        details={"status": 404, "count": 1},
    )
    assert cropped.level == "warning"
    assert report.warning_codes() == ["http_error", "overflow_cropped"]
    assert report.warning("http_error") is http
    assert report.warning("redirected") is None


def test_repeated_code_merges_count_and_bounded_samples() -> None:
    report = CaptureReport()

    first = report.warn(
        "overlays_hidden",
        "Hid 6 overlays",
        samples=[f"div#a{i}" for i in range(6)],
        rule="cookie",
    )
    again = report.warn(
        "overlays_hidden",
        "Hid 7 overlays",
        samples=[f"div#b{i}" for i in range(7)],
        rule="popup",
        rect=[0, 0, 10, 10],
    )
    report.warn("overlays_hidden", "Hid 1 overlay", samples="div#c0")

    assert again is first
    assert len(report.warnings) == 1
    assert first.message == "Hid 6 overlays"  # the first message stays
    assert first.details["count"] == 3
    assert len(first.details["samples"]) == MAX_SAMPLES
    assert first.details["samples"][:6] == [f"div#a{i}" for i in range(6)]
    assert first.details["samples"][6:] == [f"div#b{i}" for i in range(4)]
    assert first.details["rule"] == "cookie"  # first value wins
    assert first.details["rect"] == [0, 0, 10, 10]  # new keys are added


def test_repeated_code_escalates_to_warning_level() -> None:
    report = CaptureReport()

    report.warn("redirected", "redirected to /en/")
    merged = report.warn("redirected", "redirected to login", level="warning")
    report.warn("redirected", "redirected again")

    assert merged.level == "warning"
    assert merged.details["count"] == 3


def test_phase_accumulates_integer_milliseconds() -> None:
    report = CaptureReport()
    ticks = iter([1.0, 1.25, 2.0, 2.1004])

    with patch.object(
        report_module.time, "perf_counter", side_effect=lambda: next(ticks)
    ):
        with report.phase("goto"):
            pass
        with report.phase("goto"):
            pass

    assert report.timings_ms == {"goto": 350}
    assert isinstance(report.timings_ms["goto"], int)


def test_phase_tracks_the_current_phase_and_restores_it() -> None:
    report = CaptureReport()

    with report.phase("total"):
        assert report.current_phase == "total"
        with report.phase("prepare"):
            assert report.current_phase == "prepare"
        assert report.current_phase == "total"

    assert report.current_phase is None
    assert set(report.timings_ms) == {"total", "prepare"}


def test_phase_keeps_the_failing_phase_current() -> None:
    report = CaptureReport()

    with pytest.raises(RuntimeError), report.phase("total"), report.phase("readiness"):
        raise RuntimeError("boom")

    assert report.current_phase == "readiness"
    assert set(report.timings_ms) == {"total", "readiness"}


def test_phase_does_not_read_the_capture_clock() -> None:
    # Tests patch clock.now with finite iterators; phases must not consume them.
    report = CaptureReport()

    with (
        patch(
            "app.screenshots.capture.clock.now",
            side_effect=AssertionError("phase read clock.now"),
        ),
        report.phase("snapshot"),
    ):
        pass

    assert "snapshot" in report.timings_ms


def test_set_meta_merges_into_a_section() -> None:
    report = CaptureReport()

    report.set_meta("navigation", final_url="https://example.com/", http_status=200)
    section = report.set_meta("navigation", http_status=404, reloads=1)

    assert section == {
        "final_url": "https://example.com/",
        "http_status": 404,
        "reloads": 1,
    }
    assert report.meta["navigation"] is section

    report.meta["attempts"] = 2
    with pytest.raises(ValueError):
        report.set_meta("attempts", value=3)


def test_to_db_caps_warnings_and_clips_strings() -> None:
    report = CaptureReport()
    long_text = "x" * 1000
    report.warn("assets_missing", long_text, samples=[{"url": long_text}])
    for index in range(MAX_WARNINGS + 5):
        report.warnings.append(
            CaptureWarning(code="dom_unstable", level="warning", message=str(index))
        )

    warnings, _meta = report.to_db()

    assert len(warnings) == MAX_WARNINGS
    assert warnings[0]["code"] == "assets_missing"
    assert len(warnings[0]["message"]) == MAX_STRING_LENGTH
    assert warnings[0]["message"].endswith("...")
    assert len(warnings[0]["details"]["samples"][0]["url"]) == MAX_STRING_LENGTH
    assert warnings[0]["details"]["count"] == 1


def test_to_db_meta_is_json_safe_and_independent() -> None:
    report = CaptureReport()
    report.set_meta(
        "page",
        document_width=1920,
        scale=float("inf"),
        tags=("a", "b"),
        kinds={"img"},
        final_url="https://example.com/" + "p" * 400,
    )
    report.timings_ms["goto"] = 120

    warnings, meta = report.to_db()
    json.dumps([warnings, meta])  # JSONB-safe
    meta["timings_ms"]["goto"] = 0
    meta["page"]["document_width"] = 0

    assert warnings == []
    assert meta["v"] == 1
    assert meta["page"]["scale"] is None
    assert meta["page"]["tags"] == ["a", "b"]
    assert meta["page"]["kinds"] == ["img"]
    assert len(meta["page"]["final_url"]) == MAX_STRING_LENGTH
    assert report.timings_ms == {"goto": 120}
    assert report.meta["page"]["document_width"] == 1920


def test_to_db_of_an_empty_report() -> None:
    assert CaptureReport().to_db() == ([], {"v": 1, "timings_ms": {}})
