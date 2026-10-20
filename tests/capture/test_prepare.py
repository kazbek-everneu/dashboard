from app.screenshots.capture import prepare
from app.screenshots.capture.network import InflightTracker
from tests.capture.fakes import FakePage, make_run


def test_hide_overlays_does_not_match_generic_overlay_selectors() -> None:
    assert '[class*="overlay"]' not in prepare.JS_HIDE_OVERLAYS
    assert '[id*="overlay"]' not in prepare.JS_HIDE_OVERLAYS
    assert '[class*="popup"]' in prepare.JS_HIDE_OVERLAYS
    assert '[class*="modal"]' in prepare.JS_HIDE_OVERLAYS
    assert '[class*="banner"]' in prepare.JS_HIDE_OVERLAYS


def test_hide_overlays_hides_backdrops_and_unlocks_scroll() -> None:
    script = prepare.JS_HIDE_OVERLAYS
    assert "'.cky-overlay'" in script
    assert "el === document.body" in script
    assert "d.matches(':modal')" in script
    assert "cs.position !== 'fixed'" in script
    assert "cs.pointerEvents === 'none'" in script
    assert "MIN_COVERAGE" in script
    assert "data-qa-overlay-hidden" in script
    assert "cs.overflowY === 'hidden'" in script
    assert "'overflow-x'" not in script


def test_normalize_fixed_backgrounds_script_targets_background_attachment() -> None:
    script = prepare.JS_NORMALIZE_FIXED_BACKGROUNDS
    assert "backgroundAttachment" in script
    assert "background-attachment" in script
    assert "value.toLowerCase() === 'fixed'" in script
    assert "return 'local';" in script


def test_detect_lazy_content_ignores_native_lazy_images() -> None:
    # WordPress marks nearly every image loading="lazy"; JS_TRIGGER_LAZY flips
    # those to eager, so they must not select the slow scroll profile.
    assert 'img[loading="lazy"]' not in prepare.JS_DETECT_LAZY_CONTENT
    assert "img[data-src]" in prepare.JS_DETECT_LAZY_CONTENT
    assert '[data-wpr-lazyrender="1"]' in prepare.JS_DETECT_LAZY_CONTENT


async def test_scroll_pass_waits_adaptively_per_step() -> None:
    page = FakePage(script_results={"scroll-height": 1000})

    steps = await prepare.scroll_pass(
        page, 300, tracker=InflightTracker(), min_ms=300, quiet_ms=200, max_ms=2000
    )

    scrolls = [arg for marker, arg in page.evaluate_calls if marker == "scroll-to"]
    # scrollTo(0, 0) reset + positions 0, 300, 600, 900
    assert scrolls == [0, 0, 300, 600, 900]
    assert steps == 4
    # An idle page waits only the per-step minimum, not the 2 s cap.
    assert sum(page.waits) == 4 * 300


async def test_scroll_to_trigger_lazy_runs_two_passes_then_returns_to_top() -> None:
    page = FakePage(script_results={"scroll-height": 700})

    steps = await prepare.scroll_to_trigger_lazy(
        page, 720, has_lazy_content=False, tracker=InflightTracker()
    )

    scroll_markers = [
        (marker, arg)
        for marker, arg in page.evaluate_calls
        if marker.startswith("scroll-to")
    ]
    # Each pass: reset to 0, then positions 0 and 360 (step = 720 // 2).
    assert scroll_markers == [
        ("scroll-to", 0),
        ("scroll-to", 0),
        ("scroll-to", 360),
        ("scroll-to", 0),
        ("scroll-to", 0),
        ("scroll-to", 360),
        ("scroll-to-bottom", None),
        ("scroll-to", 0),
    ]
    assert steps == 4


async def test_prepare_page_runs_transforms_then_scrolls() -> None:
    page = FakePage()
    run = make_run(page=page)

    result = await prepare.prepare_page(run)

    assert page.evaluated[:7] == [
        "hide-overlays",
        "pause-videos",
        "wait-fonts",
        "detect-lazy",
        "trigger-lazy",
        "wp-rocket-render",
        "normalize-fixed-bg",
    ]
    assert page.evaluated[-1] == "pause-videos"
    # Fonts get at most 5 s before scrolling.
    assert ("wait-fonts", 5000) in page.evaluate_calls
    # No lazy markers: the short post-trigger pause.
    assert page.waits[0] == 150
    assert result.has_lazy_content is False
    assert result.pass2_ran is True
    assert result.scroll_steps == 0


async def test_prepare_page_uses_the_slow_profile_for_lazy_pages() -> None:
    page = FakePage(script_results={"detect-lazy": True})

    result = await prepare.prepare_page(make_run(page=page))

    assert result.has_lazy_content is True
    assert page.waits[0] == 500


async def test_prepare_page_continues_when_fonts_are_not_ready() -> None:
    page = FakePage(script_results={"wait-fonts": False})

    await prepare.prepare_page(make_run(page=page))

    assert "detect-lazy" in page.evaluated
    assert page.evaluated[-1] == "pause-videos"


async def test_reapply_transforms_order() -> None:
    page = FakePage()

    await prepare.reapply_transforms(make_run(page=page))

    assert page.evaluated == [
        "trigger-lazy",
        "wp-rocket-render",
        "normalize-fixed-bg",
        "hide-overlays",
    ]


async def test_final_touches_for_a_stable_page() -> None:
    page = FakePage()

    await prepare.final_touches(make_run(page=page), unstable=False)

    assert page.events == [
        ("evaluate", "hide-overlays"),
        ("wait", 500),
        ("evaluate", "pause-videos"),
        ("evaluate", "normalize-fixed-bg"),
    ]


async def test_final_touches_for_an_unstable_page() -> None:
    page = FakePage()

    await prepare.final_touches(make_run(page=page), unstable=True)

    assert page.events == [
        ("evaluate", "hide-overlays"),
        ("evaluate", "normalize-fixed-bg"),
        ("wait", 300),
        ("evaluate", "pause-videos"),
        ("evaluate", "normalize-fixed-bg"),
    ]
