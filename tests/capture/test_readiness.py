from unittest.mock import AsyncMock, patch

import pytest

from app.screenshots.capture import readiness
from app.screenshots.capture.errors import (
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.types import AssetIssue, NavigationResult
from tests.capture.fakes import READY_STABILITY_STATE, FakePage, make_run

MISSING_IMAGE_STATE = {
    "visibleImageCount": 1,
    "visibleBackgroundCount": 0,
    "missingVisibleImageCount": 1,
    "missingBackgroundImageCount": 0,
    "hiddenPendingImageCount": 2,
    "missingSamples": [{"kind": "img", "url": "https://cdn.example.com/missing.jpg"}],
}


def test_activity_tracker_is_a_placeholder() -> None:
    assert readiness.JS_ACTIVITY_TRACKER == ""


async def test_wait_for_fonts_raises_when_page_reports_timeout() -> None:
    page = FakePage(script_results={"wait-fonts": False})

    with pytest.raises(PageStabilizationTimeoutError):
        await readiness.wait_for_fonts(page, 1000)

    assert page.evaluate_calls == [("wait-fonts", 1000)]


async def test_decode_visible_images_raises_when_page_reports_timeout() -> None:
    page = FakePage(script_results={"decode-images": {"timedOut": True}})

    with pytest.raises(PageAssetLoadError, match="Timed out"):
        await readiness.decode_visible_images(page, 1000)


async def test_decode_visible_images_attaches_the_verdict_to_the_error() -> None:
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})

    with pytest.raises(PageAssetLoadError) as exc_info:
        await readiness.decode_visible_images(page, 1000)

    assert str(exc_info.value) == (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg; ignored hidden pending images: 2"
    )
    assert exc_info.value.assets is not None
    assert exc_info.value.assets.missing_count == 1


async def test_wait_for_dom_stability_needs_three_identical_ready_samples() -> None:
    page = FakePage()

    await readiness.wait_for_dom_stability(page, 8000)

    assert page.evaluated == ["stability-state"] * 4
    assert page.waits == [500, 500, 500]


async def test_finalize_ignores_hidden_pending_images() -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                "visibleImageCount": 0,
                "visibleBackgroundCount": 0,
                "missingVisibleImageCount": 0,
                "missingBackgroundImageCount": 0,
                "hiddenPendingImageCount": 6,
                "missingSamples": [],
            },
            "stability-state": {
                **READY_STABILITY_STATE,
                "scrollHeight": 900,
                "pendingImages": 6,
                "hiddenPendingImages": 6,
            },
        }
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.stable is True
    assert result.issues == ()
    assert result.blank is False
    assert result.assets is not None
    assert result.assets.hidden_pending_image_count == 6


async def test_finalize_counts_visible_background_images_as_content() -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                "visibleImageCount": 0,
                "visibleBackgroundCount": 1,
                "missingVisibleImageCount": 0,
                "missingBackgroundImageCount": 0,
                "hiddenPendingImageCount": 0,
                "missingSamples": [],
            },
            "stability-state": {**READY_STABILITY_STATE, "textLength": 0},
        }
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is False
    assert result.stable is True
    assert "decode-images" in page.evaluated
    assert "backgroundImage" in readiness.JS_DECODE_VISIBLE_IMAGES
    assert "visibleBackgroundCount" in readiness.JS_DECODE_VISIBLE_IMAGES


async def test_finalize_bounds_fonts_and_decode_within_the_window() -> None:
    wide = FakePage()
    narrow = FakePage()

    await readiness.finalize(make_run(page=wide), window_ms=25000)
    await readiness.finalize(make_run(page=narrow), window_ms=8000)

    assert ("wait-fonts", 5000) in wide.evaluate_calls
    assert ("decode-images", 12000) in wide.evaluate_calls
    assert ("wait-fonts", 5000) in narrow.evaluate_calls
    assert ("decode-images", 8000) in narrow.evaluate_calls


async def test_finalize_reports_fonts_timeout_as_unstable() -> None:
    page = FakePage(script_results={"wait-fonts": False})

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.stable is False
    assert result.unstable_reason == "fonts"
    assert result.fonts_ready is False
    assert page.evaluated == ["wait-fonts"]  # stops at the first problem


async def test_finalize_reports_dom_timeout_as_unstable() -> None:
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        result = await readiness.finalize(make_run(), window_ms=8000)

    assert result.stable is False
    assert result.unstable_reason == "dom"
    assert result.fonts_ready is True
    assert result.assets is not None
    assert stability.await_args.args[1] == 8000


async def test_finalize_returns_missing_assets_as_issues() -> None:
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.issues == (
        AssetIssue(
            kind="img",
            url="https://cdn.example.com/missing.jpg",
            category="unknown",
            reason=run.scratch["asset_error"],
        ),
    )
    assert run.scratch["asset_error"] == (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg; ignored hidden pending images: 2"
    )
    assert result.assets is not None and result.assets.missing_count == 1
    assert "stability-state" not in page.evaluated


async def test_finalize_reports_a_decode_timeout_as_pending() -> None:
    page = FakePage(script_results={"decode-images": {"timedOut": True}})
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert run.scratch["asset_error"] == (
        "Timed out waiting for visible image/background assets"
    )
    assert [issue.category for issue in result.issues] == ["pending"]
    assert result.assets is None


async def test_finalize_issue_without_samples() -> None:
    decode = AsyncMock(
        side_effect=PageAssetLoadError("Visible page assets did not load")
    )
    run = make_run()

    with patch("app.screenshots.capture.readiness.decode_visible_images", decode):
        result = await readiness.finalize(run, window_ms=8000)

    assert len(result.issues) == 1
    assert result.issues[0].category == "unknown"
    assert run.scratch["asset_error"] == "Visible page assets did not load"


async def test_finalize_flags_a_blank_page() -> None:
    page = FakePage(
        script_results={"stability-state": {**READY_STABILITY_STATE, "textLength": 0}}
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is True
    assert result.stable is True


async def test_finalize_accepts_interactive_after_a_dom_only_navigation() -> None:
    interactive = {**READY_STABILITY_STATE, "readyState": "interactive"}
    run = make_run(page=FakePage(script_results={"stability-state": interactive}))
    run.nav = NavigationResult(
        "https://example.com/",
        "https://example.com/",
        200,
        load_event_fired=False,
        wait_strategy="domcontentloaded",
    )

    result = await readiness.finalize(run, window_ms=8000)

    assert result.stable is True


async def test_finalize_requires_complete_after_a_load_navigation() -> None:
    interactive = {**READY_STABILITY_STATE, "readyState": "interactive"}
    run = make_run(page=FakePage(script_results={"stability-state": interactive}))

    # A 1 ms window: the "interactive" page can never become ready in time.
    result = await readiness.finalize(
        run, window_ms=1, allow_interactive_ready_state=False
    )

    assert result.stable is False
    assert result.unstable_reason == "dom"


async def test_retry_in_place_leaves_every_issue_broken_for_now() -> None:
    issues = [AssetIssue(kind="img", url="https://cdn.example.com/a.jpg")]

    assert await readiness.retry_in_place(make_run(), issues) == tuple(issues)
