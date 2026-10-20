import re
from typing import Any

import pytest

from app.screenshots.capture import config as cfg
from app.screenshots.capture import jsutil, prepare, profiles
from app.screenshots.capture.network import InflightTracker
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.types import CaptureRun
from tests.capture.fakes import FakePage, make_run, results_sequence

DRAIN = "try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}"
PAGE_SCRIPTS = {
    name: getattr(prepare, name)
    for name in dir(prepare)
    if name.startswith("JS_") and name != "JS_IO_OVERRIDE"
}
DOM_CHANGING_SCRIPTS = (
    "JS_HIDE_OVERLAYS",
    "JS_TRIGGER_LAZY",
    "JS_NORMALIZE_RENDER",
    "JS_SCROLL_SETUP",
    "JS_EXPAND_SCROLLER",
    "JS_PAUSE_CAROUSELS",
)


def metrics(max_scroll: int, height: int, viewport: int = 720, io: int = 0) -> dict:
    return {
        "maxScroll": max_scroll,
        "height": height,
        "viewport": viewport,
        "ioObserves": io,
    }


def scroll_targets(page: FakePage) -> list[int]:
    return [arg["y"] for marker, arg in page.evaluate_calls if marker == "scroll-to"]


class GrowingPage:
    """A page that grows by *growth* px every time it is scrolled to its
    bottom (an infinite-scroll sentinel), as FakePage script results."""

    def __init__(self, height: int, viewport: int, growth: int) -> None:
        self.height = height
        self.viewport = viewport
        self.growth = growth

    def scroll_to(self, arg: dict[str, Any]) -> None:
        if arg["y"] >= self.height - self.viewport:
            self.height += self.growth

    def metrics(self, _arg: Any) -> dict[str, Any]:
        return metrics(max(0, self.height - self.viewport), self.height, self.viewport)

    def page(self, **results: Any) -> FakePage:
        return FakePage(
            script_results={
                "scroll-to": self.scroll_to,
                "scroll-metrics": self.metrics,
                **results,
            }
        )


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(PAGE_SCRIPTS))
def test_page_scripts_carry_their_marker_first(name: str) -> None:
    body = PAGE_SCRIPTS[name].split("{", 1)[1]
    assert re.match(r"\s*/\*qa:[a-z0-9-]+\*/", body), name


@pytest.mark.parametrize("name", DOM_CHANGING_SCRIPTS)
def test_dom_changing_scripts_drain_the_activity_tracker(name: str) -> None:
    script = getattr(prepare, name)
    assert DRAIN in script
    # ... after their last DOM change: only the result follows.
    tail = script.rsplit(DRAIN, 1)[1]
    assert "setProperty" not in tail and "setAttribute" not in tail


def test_io_override_is_the_marked_init_script() -> None:
    script = prepare.JS_IO_OVERRIDE
    assert script.lstrip().startswith("(function() { /*qa:io-override*/")
    assert "MAX_FAKE_PER_TARGET = 2" in script
    assert "MAX_FAKE_TOTAL = 300" in script
    # Subclass of the native observer: the instance itself is the delegate.
    assert "class IntersectionObserver extends NativeIO" in script
    for call in ("super.observe(", "super.unobserve(", "super.disconnect("):
        assert call in script
    assert "super.takeRecords(" in script
    assert "new WeakMap()" in script
    assert "{ fake: 0, native: 0, observes: 0 }" in script
    assert "'__qaIO'" in script
    assert script in profiles.capture_init_script(DeviceProfile())


def test_hide_overlays_tiers() -> None:
    script = prepare.JS_HIDE_OVERLAYS
    # A: exact vendor selectors.
    for selector in (
        ".cky-consent-container",
        ".cky-overlay",
        "#onetrust-consent-sdk",
        "#CybotCookiebotDialogBodyUnderlay",
        ".cmplz-cookiebanner",
        "#usercentrics-root",
        "#hubspot-messages-iframe-container",
        ".elementor-popup-modal",
        ".ReactModal__Overlay",
    ):
        assert f"'{selector}'" in script
    # B: substrings are no longer hidden unconditionally (that hid CTA
    # buttons with a "popup" class), only where the position allows.
    assert '[class*="popup"]' not in script
    assert '[class*="overlay"]' not in script
    assert "'fixed', 'sticky'" in script
    assert "POPUP_MIN_AREA = 0.15" in script
    assert "POPUP_MIN_Z_INDEX = 1000" in script
    assert "LEGACY_POPUP_MIN_AREA = 0.3" in script
    assert "ANCESTOR_LEVELS = 6" in script
    assert "we use" in script
    # C: the geometric backdrop pass, dialogs and the scroll lock as before.
    assert "MIN_COVERAGE = 0.9" in script
    assert "cs.pointerEvents === 'none'" in script
    assert "d.matches(':modal')" in script
    assert "cs.overflowY === 'hidden'" in script
    assert "'overflow-x'" not in script
    assert "node === body || node === html" in script
    assert "el === html || el === body" in script


def test_normalize_render_forces_only_auto_content_visibility() -> None:
    script = prepare.JS_NORMALIZE_RENDER
    assert "backgroundAttachment" in script
    assert "return 'local';" in script
    assert "cs.contentVisibility === 'auto'" in script
    assert "'content-visibility', 'visible', 'important'" in script
    assert "data-qa-cv-forced" in script
    assert "'hidden'" not in script
    # Containment is dropped only for WP Rocket's lazy render.
    assert "querySelectorAll('[data-wpr-lazyrender]')" in script
    assert "'contain', 'none', 'important'" in script
    assert "'contain-intrinsic-size', 'none', 'important'" in script


def test_scrolling_is_instant() -> None:
    assert "behavior: 'instant'" in prepare.JS_SCROLL_TO
    assert "'scroll-behavior', 'auto', 'important'" in prepare.JS_SCROLL_SETUP


def test_detect_lazy_content_ignores_native_lazy_images() -> None:
    # WordPress marks nearly every image loading="lazy"; JS_TRIGGER_LAZY flips
    # those to eager, so they must not select the slow scroll profile.
    assert 'img[loading="lazy"]' not in prepare.JS_DETECT_LAZY_CONTENT
    assert "img[data-src]" in prepare.JS_DETECT_LAZY_CONTENT
    assert '[data-wpr-lazyrender="1"]' in prepare.JS_DETECT_LAZY_CONTENT


async def test_every_prepare_script_is_bounded(monkeypatch) -> None:
    timeouts: list[tuple[str, Any]] = []
    evaluate = jsutil.evaluate

    async def spy(page, script, arg=None, *, timeout_ms=None):
        timeouts.append((jsutil.script_marker(script), timeout_ms))
        return await evaluate(page, script, arg, timeout_ms=timeout_ms)

    monkeypatch.setattr(jsutil, "evaluate", spy)
    run = make_run()

    run.prep = await prepare.prepare_page(run)
    await prepare.reapply_transforms(run)
    await prepare.final_touches(run, unstable=False)

    assert timeouts
    assert {timeout for _marker, timeout in timeouts} == {15_000}


# ---------------------------------------------------------------------------
# Scroll decisions
# ---------------------------------------------------------------------------


async def test_scroll_pass_positions_are_clamped_to_the_bottom() -> None:
    # height 1000, innerHeight 720 → maxScroll 280
    page = FakePage(script_results={"scroll-metrics": metrics(280, 1000)})
    run = make_run(page=page)

    steps, last = await prepare.scroll_pass(
        run,
        360,
        inner=False,
        guard=prepare.GrowthGuard(720),
        min_ms=300,
        quiet_ms=200,
        max_ms=2000,
    )

    assert scroll_targets(page) == [0, 280]
    assert steps == 2
    assert last.height == 1000
    # Re-measured after every position; no extra scroll at the bottom.
    assert page.evaluated.count("scroll-metrics") == 3
    # An idle page waits only the per-step minimum, not the 2 s cap.
    assert sum(page.waits) == 2 * 300


async def test_scroll_pass_follows_a_page_that_grew_at_the_bottom() -> None:
    page = FakePage(
        script_results={
            "scroll-metrics": results_sequence(
                metrics(280, 1000),  # before the pass
                metrics(280, 1000),  # after 0
                metrics(580, 1300),  # after 280: 300 px appeared
                metrics(580, 1300),  # after 580
            )
        }
    )
    guard = prepare.GrowthGuard(720)

    steps, last = await prepare.scroll_pass(
        make_run(page=page),
        360,
        inner=False,
        guard=guard,
        min_ms=100,
        quiet_ms=100,
        max_ms=100,
    )

    assert scroll_targets(page) == [0, 280, 580]
    assert steps == 3
    assert last.max_scroll == 580
    # 300 px < 0.5 * 720: not an infinite-scroll growth.
    assert guard.events == 0 and guard.stopped is False


async def test_scroll_pass_scrolls_the_marked_inner_scroller() -> None:
    page = FakePage(script_results={"scroll-metrics": metrics(500, 1100, 600)})

    await prepare.scroll_pass(
        make_run(page=page),
        300,
        inner=True,
        guard=prepare.GrowthGuard(600),
        min_ms=100,
        quiet_ms=100,
        max_ms=100,
    )

    assert {
        marker: arg
        for marker, arg in page.evaluate_calls
        if marker in ("scroll-to", "scroll-metrics")
    }["scroll-metrics"] == {"inner": True}
    assert [arg for marker, arg in page.evaluate_calls if marker == "scroll-to"] == [
        {"y": 0, "inner": True},
        {"y": 300, "inner": True},
        {"y": 500, "inner": True},
    ]


async def test_scroll_pass_without_metrics_visits_the_top_once() -> None:
    page = FakePage()  # scroll-metrics answers nothing

    steps, last = await prepare.scroll_pass(
        make_run(page=page),
        360,
        inner=False,
        guard=prepare.GrowthGuard(720),
        min_ms=100,
        quiet_ms=100,
        max_ms=100,
    )

    assert scroll_targets(page) == [0]
    assert steps == 1
    assert last == prepare.ScrollMetrics()


def test_growth_guard_needs_three_consecutive_bottom_growths() -> None:
    guard = prepare.GrowthGuard(800)  # threshold 400 px

    assert guard.at_bottom(400, 2000) is False
    assert guard.at_bottom(1000, 3000) is False
    assert guard.at_bottom(100, 3100) is False  # small growth: streak resets
    assert guard.at_bottom(500, 3600) is False
    assert guard.at_bottom(500, 4100) is False
    assert guard.at_bottom(500, 4600) is True
    assert guard.stopped is True
    assert guard.events == 5


def test_growth_guard_stops_very_tall_pages() -> None:
    guard = prepare.GrowthGuard(800)

    assert guard.check_height(98_301) is False
    assert guard.check_height(98_302) is True
    assert guard.stopped is True
    assert guard.events == 0


async def test_scroll_pass_stops_an_infinite_scroll() -> None:
    growing = GrowingPage(height=1500, viewport=800, growth=2000)
    page = growing.page()
    guard = prepare.GrowthGuard(800)

    steps, last = await prepare.scroll_pass(
        make_run(page=page),
        400,
        inner=False,
        guard=guard,
        min_ms=100,
        quiet_ms=100,
        max_ms=100,
    )

    assert guard.stopped is True
    assert guard.events == 3
    assert last.height == 1500 + 3 * 2000
    assert scroll_targets(page)[-1] == 1500 + 2 * 2000 - 800
    assert steps == len(scroll_targets(page))


async def test_scroll_pass_does_not_scroll_a_too_tall_page() -> None:
    page = FakePage(script_results={"scroll-metrics": metrics(99_000, 99_720)})
    guard = prepare.GrowthGuard(720)

    steps, _last = await prepare.scroll_pass(
        make_run(page=page),
        360,
        inner=False,
        guard=guard,
        min_ms=100,
        quiet_ms=100,
        max_ms=100,
    )

    assert steps == 0
    assert scroll_targets(page) == []
    assert guard.stopped is True


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (prepare.Pass2Signals(), False),
        (prepare.Pass2Signals(lazy_markers=True), True),
        (prepare.Pass2Signals(height_growth=49), False),
        (prepare.Pass2Signals(height_growth=50), True),
        (prepare.Pass2Signals(height_growth=-400), False),
        (prepare.Pass2Signals(requests_started=1), True),
        (prepare.Pass2Signals(io_observes_growth=1), True),
        (prepare.Pass2Signals(pending_visible_images=1), True),
        (prepare.Pass2Signals(cv_forced=1), True),
        (
            prepare.Pass2Signals(
                lazy_markers=True,
                height_growth=500,
                requests_started=3,
                io_observes_growth=2,
                pending_visible_images=1,
                cv_forced=4,
            ),
            True,
        ),
    ],
)
def test_should_run_pass2(signals: prepare.Pass2Signals, expected: bool) -> None:
    assert prepare.should_run_pass2(signals) is expected


def test_pass2_reasons_name_every_signal() -> None:
    signals = prepare.Pass2Signals(
        lazy_markers=True,
        height_growth=50,
        requests_started=1,
        io_observes_growth=1,
        pending_visible_images=1,
        cv_forced=1,
    )

    assert prepare.pass2_reasons(signals) == (
        "lazy_markers",
        "height_growth",
        "requests",
        "io_observes",
        "pending_images",
        "content_visibility",
    )


# ---------------------------------------------------------------------------
# Scroll passes
# ---------------------------------------------------------------------------


async def test_quiet_page_gets_one_pass_then_returns_to_top() -> None:
    page = FakePage(script_results={"scroll-metrics": metrics(280, 1000)})
    run = make_run(page=page)

    outcome = await prepare.scroll_to_trigger_lazy(run, has_lazy_content=False)

    # Pass 1 ends at the bottom; nothing asks for pass 2; back to the top.
    assert scroll_targets(page) == [0, 280, 0]
    assert outcome.pass2_ran is False
    assert outcome.pass2_reasons == ()
    assert outcome.steps == 2
    assert outcome.height == 1000
    assert outcome.growth_stopped is False
    assert page.evaluated.count("pass2-signals") == 1


async def test_second_pass_steps_by_most_of_the_viewport() -> None:
    page = FakePage(
        script_results={
            "scroll-metrics": metrics(2000, 2720),
            "pass2-signals": {"pendingVisibleImages": 1, "cvForced": 0},
        }
    )

    outcome = await prepare.scroll_to_trigger_lazy(
        make_run(page=page), has_lazy_content=False
    )

    # Pass 1: half the viewport (360); pass 2: 0.9 * 720 = 648.
    assert scroll_targets(page) == [
        *[0, 360, 720, 1080, 1440, 1800, 2000],
        *[0, 648, 1296, 1944, 2000],
        0,
    ]
    assert outcome.pass2_ran is True
    assert outcome.pass2_reasons == ("pending_images",)
    assert outcome.steps == 12


async def test_second_pass_runs_when_pass_one_started_requests() -> None:
    page = FakePage(script_results={"scroll-metrics": metrics(280, 1000)})
    run = make_run(page=page)
    assert isinstance(run.tracker, InflightTracker)

    def request_started(wait_count: int) -> None:
        if wait_count == 1:
            run.tracker.started_count += 1

    page.on_wait = request_started

    outcome = await prepare.scroll_to_trigger_lazy(run, has_lazy_content=False)

    assert outcome.pass2_reasons == ("requests",)
    assert outcome.pass2_ran is True


async def test_second_pass_signals_height_and_observers() -> None:
    page = FakePage(
        script_results={
            "scroll-metrics": results_sequence(
                metrics(280, 1000, io=4),
                metrics(280, 1000, io=4),
                metrics(280, 1000, io=4),
                metrics(400, 1120, io=6),  # after the settle
            ),
        }
    )

    outcome = await prepare.scroll_to_trigger_lazy(
        make_run(page=page), has_lazy_content=True
    )

    assert outcome.pass2_reasons == ("lazy_markers", "height_growth", "io_observes")


async def test_force_pass2_runs_the_second_pass(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "FORCE_PASS2", True)
    page = FakePage(script_results={"scroll-metrics": metrics(280, 1000)})

    outcome = await prepare.scroll_to_trigger_lazy(
        make_run(page=page), has_lazy_content=False
    )

    assert outcome.pass2_ran is True
    # Forced, and no signal would have asked for it.
    assert outcome.pass2_reasons == ("forced",)
    assert scroll_targets(page) == [0, 280, 0, 280, 0]


async def test_forced_pass2_still_records_the_signals(monkeypatch) -> None:
    monkeypatch.setattr(cfg, "FORCE_PASS2", True)
    page = FakePage(script_results={"detect-lazy": True})
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert result.pass2_reasons == ("forced", "lazy_markers")
    assert run.report.meta["page"]["pass2"] is True
    assert run.report.meta["page"]["pass2_reasons"] == ["forced", "lazy_markers"]


async def test_infinite_scroll_skips_the_second_pass() -> None:
    page = GrowingPage(height=1500, viewport=800, growth=2000).page(
        **{"pass2-signals": {"pendingVisibleImages": 3}}
    )

    outcome = await prepare.scroll_to_trigger_lazy(
        make_run(page=page), has_lazy_content=True
    )

    assert outcome.growth_stopped is True
    assert outcome.growth_events == 3
    assert outcome.pass2_ran is False
    assert "pass2-signals" not in page.evaluated
    assert outcome.height == 7500
    assert scroll_targets(page)[-1] == 0


# ---------------------------------------------------------------------------
# prepare_page
# ---------------------------------------------------------------------------


async def test_prepare_page_runs_transforms_then_scrolls() -> None:
    page = FakePage()
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert page.evaluated == [
        "hide-overlays",
        "pause-videos",
        "wait-fonts",
        "detect-lazy",
        "trigger-lazy",
        "normalize-render",
        "scroll-setup",
        "scroll-metrics",
        "scroll-to",
        "scroll-metrics",
        "scroll-metrics",
        "pass2-signals",
        "scroll-to",
        "pause-carousels",
        "pause-videos",
    ]
    # Fonts get at most 3 s before scrolling.
    assert ("wait-fonts", 3000) in page.evaluate_calls
    # No lazy markers: the adaptive post-trigger pause ends at its 150 ms cap.
    assert page.waits[:2] == [100, 50]
    assert result.has_lazy_content is False
    assert result.pass2_ran is False
    assert result.scroll_steps == 1
    assert result.inner_scroller is False
    assert result.growth_stopped is False
    assert result.overlays_hidden == 0
    assert result.carousels_paused == 0
    assert run.report.warnings == []
    assert run.report.meta["page"] == {
        "inner_scroller": False,
        "scroll_steps": 1,
        "pass2": False,
        "pass2_reasons": [],
    }
    assert run.report.meta["overlays"] == {"hidden": 0, "samples": []}
    assert "scroll" in run.report.timings_ms
    assert run.report.current_phase is None


async def test_prepare_page_uses_the_slow_profile_for_lazy_pages() -> None:
    page = FakePage(script_results={"detect-lazy": True})

    result = await prepare.prepare_page(make_run(page=page))

    assert result.has_lazy_content is True
    # Quiet after 150 ms (100 + 100 polls) within the 500 ms cap.
    assert page.waits[:2] == [100, 100]
    # Lazy markers always ask for the second pass.
    assert result.pass2_ran is True
    assert result.pass2_reasons == ("lazy_markers",)
    assert result.scroll_steps == 2


async def test_prepare_page_continues_when_fonts_are_not_ready() -> None:
    page = FakePage(script_results={"wait-fonts": False})

    await prepare.prepare_page(make_run(page=page))

    assert "detect-lazy" in page.evaluated
    assert page.evaluated[-1] == "pause-videos"


def overlay(tier: str, rule: str, element: str) -> dict[str, Any]:
    return {
        "tier": tier,
        "rule": rule,
        "element": element,
        "rect": {"x": 0, "y": 680, "width": 1280, "height": 120},
    }


async def test_prepare_page_reports_hidden_overlays() -> None:
    banner = overlay("A", ".cky-consent-container", "div.cky-consent-container")
    modal = overlay("B", "popup", "div#nl.newsletter-popup")
    page = FakePage(
        script_results={
            "hide-overlays": results_sequence(
                {"count": 2, "records": [banner, modal]}, {"count": 0, "records": []}
            )
        }
    )
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert result.overlays_hidden == 2
    assert run.report.meta["overlays"] == {"hidden": 2, "samples": [banner, modal]}
    warning = run.report.warning("overlays_hidden")
    assert warning is not None
    assert warning.level == "info"
    assert warning.details == {"count": 2, "samples": [banner, modal]}
    assert [w.code for w in run.report.warnings] == ["overlays_hidden"]


async def test_overlays_hidden_later_add_to_the_report() -> None:
    popup = overlay("C", "backdrop", "div.timer-popup")
    page = FakePage(
        script_results={
            "hide-overlays": results_sequence(
                {"count": 1, "records": [overlay("A", ".klaro", "div.klaro")]},
                {"count": 0, "records": []},  # reapply_transforms
                {"count": 12, "records": [popup]},  # final touches
            )
        }
    )
    run = make_run(page=page)
    run.prep = await prepare.prepare_page(run)
    await prepare.reapply_transforms(run)

    await prepare.final_touches(run, unstable=False)

    assert run.prep.overlays_hidden == 13
    overlays = run.report.meta["overlays"]
    assert overlays["hidden"] == 13
    assert [sample["element"] for sample in overlays["samples"]] == [
        "div.klaro",
        "div.timer-popup",
    ]
    warning = run.report.warning("overlays_hidden")
    assert warning is not None
    assert warning.details["count"] == 13
    assert len(warning.details["samples"]) == 2


async def test_overlay_records_are_sanitized_and_capped() -> None:
    records = [
        {"tier": "B", "rule": "chat", "element": "x" * 200, "rect": {"width": 9.6}},
        "not a record",
        *[overlay("B", "cookie", f"div.c{i}") for i in range(15)],
    ]
    page = FakePage(
        script_results={
            "hide-overlays": results_sequence(
                {"count": 16, "records": records}, {"count": 0}
            )
        }
    )
    run = make_run(page=page)

    await prepare.prepare_page(run)

    samples = run.report.meta["overlays"]["samples"]
    assert len(samples) == cfg.OVERLAY_SAMPLES
    assert samples[0] == {
        "tier": "B",
        "rule": "chat",
        "element": "x" * 80,
        "rect": {"x": 0, "y": 0, "width": 9, "height": 0},
    }
    warning = run.report.warning("overlays_hidden")
    assert warning is not None and warning.details["count"] == 16


async def test_reprepared_page_reports_only_the_new_page() -> None:
    page = FakePage(
        script_results={
            "hide-overlays": {"count": 1, "records": [overlay("A", ".klaro", "div")]},
            "pause-carousels": {"swiper": 1},
        }
    )
    run = make_run(page=page)
    run.report.warn("http_error", "HTTP 503", status=503)

    run.prep = await prepare.prepare_page(run)
    run.prep = await prepare.prepare_page(run)  # after a reload

    assert run.report.meta["overlays"]["hidden"] == 1
    overlays = run.report.warning("overlays_hidden")
    assert overlays is not None and overlays.details["count"] == 1
    carousels = run.report.warning("carousels_paused")
    assert carousels is not None and carousels.details["count"] == 1
    # Other stages' warnings stay.
    assert run.report.warning("http_error") is not None


async def test_prepare_page_expands_an_inner_scroller() -> None:
    page = FakePage(
        script_results={
            "scroll-setup": {"inner": True, "element": "div#app"},
            "scroll-metrics": metrics(3200, 4000, 800),
            "expand-scroller": {"element": "div#app", "scrollHeight": 4000},
        }
    )
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert {
        arg["inner"] for marker, arg in page.evaluate_calls if marker == "scroll-to"
    } == {True}
    assert page.evaluated[-3:] == ["pause-carousels", "expand-scroller", "pause-videos"]
    assert result.inner_scroller is True
    assert result.document_height == 4000
    assert run.report.meta["page"]["inner_scroller"] is True
    warning = run.report.warning("inner_scroll_expanded")
    assert warning is not None and warning.level == "info"
    assert warning.details == {"scroll_height": 4000, "element": "div#app", "count": 1}


async def test_inner_scroller_stays_expanded_until_the_screenshot() -> None:
    page = FakePage(
        script_results={
            "scroll-setup": {"inner": True},
            "expand-scroller": {"element": "main", "scrollHeight": 2500},
        }
    )
    run = make_run(page=page)
    run.prep = await prepare.prepare_page(run)
    page.events.clear()

    await prepare.reapply_transforms(run)
    await prepare.final_touches(run, unstable=False)

    assert [marker for kind, marker in page.events if kind == "evaluate"] == [
        "trigger-lazy",
        "normalize-render",
        "hide-overlays",
        "expand-scroller",
        "pause-carousels",
        "hide-overlays",
        "expand-scroller",
        "next-frame",
        "pause-videos",
        "normalize-render",
    ]
    assert run.report.warning("inner_scroll_expanded").details["count"] == 1


async def test_prepare_page_without_a_scroller_left_to_expand() -> None:
    page = FakePage(script_results={"scroll-setup": {"inner": True}})
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert result.inner_scroller is False
    assert run.report.warning("inner_scroll_expanded") is None


async def test_prepare_page_reports_a_stopped_infinite_scroll() -> None:
    page = GrowingPage(height=1500, viewport=800, growth=2000).page()
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert result.growth_stopped is True
    assert result.pass2_ran is False
    assert result.document_height == 7500
    warning = run.report.warning("infinite_scroll_stopped")
    assert warning is not None and warning.level == "warning"
    assert warning.details == {"height": 7500, "growth_events": 3, "count": 1}


async def test_carousels_are_reported_once() -> None:
    page = FakePage(
        script_results={"pause-carousels": {"swiper": 2, "slick": 1, "owl": 0}}
    )
    run = make_run(page=page)

    run.prep = await prepare.prepare_page(run)
    await prepare.reapply_transforms(run)

    assert run.prep.carousels_paused == 3
    warning = run.report.warning("carousels_paused")
    assert warning is not None and warning.level == "info"
    assert warning.details == {"total": 3, "swiper": 2, "slick": 1, "count": 1}
    assert page.evaluated.count("pause-carousels") == 2


async def test_carousels_initialized_late_are_reported_by_reapply() -> None:
    page = FakePage(
        script_results={"pause-carousels": results_sequence({}, {"splide": 1})}
    )
    run = make_run(page=page)
    run.prep = await prepare.prepare_page(run)
    assert run.report.warning("carousels_paused") is None

    await prepare.reapply_transforms(run)

    assert run.prep.carousels_paused == 1
    assert run.report.warning("carousels_paused").details["splide"] == 1


# ---------------------------------------------------------------------------
# reapply_transforms / final_touches
# ---------------------------------------------------------------------------


async def test_reapply_transforms_order() -> None:
    page = FakePage()

    await prepare.reapply_transforms(make_run(page=page))

    assert page.evaluated == [
        "trigger-lazy",
        "normalize-render",
        "hide-overlays",
        "pause-carousels",
    ]


async def test_final_touches_for_a_stable_page() -> None:
    page = FakePage()

    await prepare.final_touches(make_run(page=page), unstable=False)

    # A double requestAnimationFrame instead of the old fixed 500 ms.
    assert page.events == [
        ("evaluate", "hide-overlays"),
        ("evaluate", "next-frame"),
        ("evaluate", "pause-videos"),
        ("evaluate", "normalize-render"),
    ]


async def test_final_touches_for_an_unstable_page() -> None:
    page = FakePage()

    await prepare.final_touches(make_run(page=page), unstable=True)

    assert page.events == [
        ("evaluate", "hide-overlays"),
        ("evaluate", "normalize-render"),
        ("evaluate", "next-frame"),
        ("evaluate", "pause-videos"),
        ("evaluate", "normalize-render"),
    ]


async def test_final_touches_without_prepare_state() -> None:
    page = FakePage(
        script_results={
            "hide-overlays": {"count": 1, "records": [overlay("B", "chat", "div")]}
        }
    )
    run: CaptureRun = make_run(page=page)

    await prepare.final_touches(run, unstable=False)

    assert run.report.meta["overlays"]["hidden"] == 1
    assert "expand-scroller" not in page.evaluated
