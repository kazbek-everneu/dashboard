import math
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import snapshot
from app.screenshots.capture.errors import EvaluateTimeout, ScreenshotTimeoutError
from app.screenshots.capture.types import Deadline, SnapshotResult
from tests.capture.fakes import FakePage, make_config, make_run


class SequencePage(FakePage):
    """A FakePage whose screenshots answer in order: bytes are returned,
    exceptions raised; the last answer repeats."""

    def __init__(self, *answers: bytes | BaseException, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.answers = list(answers) or [b"img"]

    async def screenshot(self, **kwargs: Any) -> bytes:
        self.screenshot_calls.append(kwargs)
        self.events.append(("screenshot", kwargs))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def metrics(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "docWidth": 1280,
        "docHeight": 3000,
        "innerWidth": 1280,
        "overflowXHidden": False,
        "dir": "ltr",
    }
    values.update(overrides)
    return values


def _page(*answers: bytes | BaseException, **page_metrics: Any) -> SequencePage:
    results = {"page-metrics": metrics(**page_metrics)} if page_metrics else {}
    return SequencePage(*answers, script_results=results)


async def test_take_snapshot_jpeg_full_page() -> None:
    page = FakePage()  # answers no page metrics

    result = await snapshot.take_snapshot(make_run(page=page))

    assert result == SnapshotResult(data=b"img", timeout_ms=90_000)
    assert page.screenshot_kwargs == {
        "full_page": True,
        "type": "jpeg",
        "timeout": 90_000,
        "animations": "disabled",
        "caret": "hide",
        "style": snapshot.SCREENSHOT_STYLE,
        "quality": 85,
    }
    # The metrics are read right before the screenshot.
    assert page.events[-2:] == [
        ("evaluate", "page-metrics"),
        ("screenshot", page.screenshot_kwargs),
    ]


async def test_take_snapshot_png_has_no_quality_and_keeps_a_longer_timeout() -> None:
    page = FakePage(screenshot_data=b"png")

    result = await snapshot.take_snapshot(
        make_run(page=page, config=make_config(fmt="png", timeout=120_000))
    )

    assert result.data == b"png"
    assert page.screenshot_kwargs is not None
    assert "quality" not in page.screenshot_kwargs
    assert page.screenshot_kwargs["type"] == "png"
    assert page.screenshot_kwargs["timeout"] == 120_000


async def test_take_snapshot_of_a_fitting_page_records_its_size() -> None:
    page = _page(b"img", docWidth=1280, docHeight=4321)
    run = make_run(page=page)

    result = await snapshot.take_snapshot(run)

    assert (result.document_width, result.document_height) == (1280, 4321)
    assert result.clip_width is None
    assert result.layout_width == 1280
    assert page.screenshot_kwargs is not None and "clip" not in page.screenshot_kwargs
    assert run.report.warnings == []
    assert run.report.meta["page"] == {
        "viewport_width": 1280,
        "viewport_height": 720,
        "document_width": 1280,
        "document_height": 4321,
        "clipped_width": None,
    }


async def test_take_snapshot_crops_hidden_overflow_with_an_info() -> None:
    page = _page(b"img", docWidth=3518, docHeight=5000, overflowXHidden=True)
    run = make_run(page=page)

    result = await snapshot.take_snapshot(run)

    assert page.screenshot_kwargs is not None
    assert page.screenshot_kwargs["full_page"] is True
    assert page.screenshot_kwargs["clip"] == {
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 10_000,
    }
    assert result.clip_width == 1280
    assert result.overflow_hidden is True
    warning = run.report.warning("overflow_cropped")
    assert warning is not None and warning.level == "info"
    assert warning.details == {
        "document_width": 3518,
        "viewport_width": 1280,
        "count": 1,
    }
    assert run.report.meta["page"]["clipped_width"] == 1280
    assert run.report.meta["page"]["document_width"] == 3518


async def test_take_snapshot_warns_about_visible_overflow() -> None:
    run = make_run(page=_page(b"img", docWidth=1500))

    await snapshot.take_snapshot(run)

    warning = run.report.warning("overflow_cropped")
    assert warning is not None and warning.level == "warning"


async def test_take_snapshot_anchors_a_right_to_left_page_on_the_right() -> None:
    page = _page(b"img", docWidth=2000, docHeight=900, dir="rtl")

    result = await snapshot.take_snapshot(make_run(page=page))

    assert page.screenshot_kwargs is not None
    assert page.screenshot_kwargs["clip"] == {
        "x": 720,
        "y": 0,
        "width": 1280,
        "height": 1800,
    }
    assert result.rtl is True


@pytest.mark.parametrize("browser", ["firefox", "webkit"])
async def test_take_snapshot_right_to_left_on_firefox_and_webkit(browser: str) -> None:
    # Their screenshot coordinates start at the page's scroll origin.
    page = _page(b"img", docWidth=2000, docHeight=900, dir="rtl")

    await snapshot.take_snapshot(
        make_run(page=page, config=make_config(browser=browser))
    )

    assert page.screenshot_kwargs is not None
    assert page.screenshot_kwargs["clip"]["x"] == 0


async def test_take_snapshot_crops_to_the_layout_viewport_not_the_config() -> None:
    # An emulated mobile page without a meta viewport lays out at 980 px.
    page = _page(b"img", docWidth=1400, innerWidth=980, docHeight=300)
    run = make_run(page=page, viewport={"width": 390, "height": 844})

    await snapshot.take_snapshot(run)

    assert page.screenshot_kwargs is not None
    # The clip is never shorter than twice the viewport height.
    assert page.screenshot_kwargs["clip"] == {
        "x": 0,
        "y": 0,
        "width": 980,
        "height": 1688,
    }
    assert run.report.meta["page"]["viewport_width"] == 390


async def test_take_snapshot_tolerates_one_pixel_of_overflow() -> None:
    page = _page(b"img", docWidth=1281)

    result = await snapshot.take_snapshot(make_run(page=page))

    assert result.clip_width is None
    assert page.screenshot_kwargs is not None and "clip" not in page.screenshot_kwargs


async def test_take_snapshot_timeout_scales_with_the_document_size() -> None:
    page = _page(b"img", docWidth=2000, innerWidth=2000, docHeight=30_000)

    result = await snapshot.take_snapshot(make_run(page=page))

    # 60 megapixels x 3 s
    assert result.timeout_ms == 180_000
    assert page.screenshot_kwargs is not None
    assert page.screenshot_kwargs["timeout"] == 180_000


async def test_take_snapshot_timeout_is_capped_by_the_budget() -> None:
    page = FakePage()
    run = make_run(page=page, deadline=Deadline.start(50))

    await snapshot.take_snapshot(run)

    assert page.screenshot_kwargs is not None
    assert 49_000 <= page.screenshot_kwargs["timeout"] <= 50_000


async def test_take_snapshot_without_metrics_when_the_script_fails() -> None:
    page = FakePage(script_results={"page-metrics": EvaluateTimeout("slow")})

    result = await snapshot.take_snapshot(make_run(page=page))

    assert result.data == b"img"
    assert result.document_width == 0
    assert page.screenshot_kwargs is not None and "clip" not in page.screenshot_kwargs


async def test_take_snapshot_retries_a_timed_out_screenshot_in_place() -> None:
    page = SequencePage(PlaywrightTimeoutError("Timeout 90000ms exceeded"), b"second")
    run = make_run(page=page)
    final_touches = AsyncMock()

    with patch("app.screenshots.capture.prepare.final_touches", final_touches):
        result = await snapshot.take_snapshot(run, unstable=True)

    assert result.data == b"second"
    assert result.retried is True
    assert len(page.screenshot_calls) == 2
    final_touches.assert_awaited_once_with(run, unstable=True)
    # A 1 s pause, then the final touches, then the second screenshot.
    first, second = (
        index for index, (kind, _) in enumerate(page.events) if kind == "screenshot"
    )
    assert page.events[first + 1 : second] == [("wait", 1000)]
    warning = run.report.warning("screenshot_retried")
    assert warning is not None and warning.level == "info"
    assert warning.details["timeout_ms"] == 90_000


async def test_take_snapshot_fails_after_a_second_timeout() -> None:
    page = SequencePage(PlaywrightTimeoutError("Timeout 90000ms exceeded"))
    run = make_run(page=page)

    with (
        patch("app.screenshots.capture.prepare.final_touches", AsyncMock()),
        pytest.raises(ScreenshotTimeoutError, match="timed out twice") as exc_info,
    ):
        await snapshot.take_snapshot(run)

    assert not isinstance(exc_info.value, TimeoutError)
    assert len(page.screenshot_calls) == 2
    assert run.report.warning("screenshot_retried") is not None


async def test_take_snapshot_skips_the_retry_without_budget() -> None:
    page = SequencePage(PlaywrightTimeoutError("Timeout 20000ms exceeded"))
    run = make_run(page=page, deadline=Deadline.start(20))
    final_touches = AsyncMock()

    with (
        patch("app.screenshots.capture.prepare.final_touches", final_touches),
        pytest.raises(ScreenshotTimeoutError),
    ):
        await snapshot.take_snapshot(run)

    assert len(page.screenshot_calls) == 1
    final_touches.assert_not_awaited()
    assert run.report.warning("screenshot_retried") is None
    skipped = run.report.warning("recovery_skipped")
    assert skipped is not None and skipped.level == "info"
    assert skipped.details["step"] == "screenshot_retry"
    assert 0 < skipped.details["remaining_s"] <= 20


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "nope",
        {},
        {"docWidth": 0, "docHeight": 10, "innerWidth": 1280},
        {"docWidth": 1280, "docHeight": 10, "innerWidth": 0},
        {"docWidth": "wide", "innerWidth": 1280},
        {"docWidth": math.inf, "innerWidth": 1280},
    ],
)
def test_page_metrics_without_a_usable_size_are_no_data(raw: object) -> None:
    assert snapshot.page_metrics_from_raw(raw) is None


def test_page_metrics_from_raw() -> None:
    parsed = snapshot.page_metrics_from_raw(
        metrics(docWidth=2000.4, overflowXHidden=True, dir="RTL")
    )

    assert parsed == snapshot.PageMetrics(
        doc_width=2000,
        doc_height=3000,
        inner_width=1280,
        overflow_hidden=True,
        rtl=True,
    )
    assert parsed.megapixels == 6.0


def test_layout_width_is_client_width_when_emulation_widened_inner_width() -> None:
    # Chrome mobile emulation on perimeter.net: innerWidth follows the
    # zoomed-out content, the root's clientWidth stays the layout width.
    parsed = snapshot.page_metrics_from_raw(
        metrics(docWidth=5785, innerWidth=1560, clientWidth=390)
    )

    assert parsed is not None
    assert parsed.inner_width == 390
    clip = snapshot.overflow_clip(
        parsed, {"width": 390, "height": 844}, browser="chrome"
    )
    assert clip is not None and clip["width"] == 390


def test_mobile_layout_width_is_client_width_even_for_a_small_overflow() -> None:
    # elmearts.com/thank-you at 390: innerWidth 404 — within scrollbar range,
    # but an emulated mobile page has no scrollbars.
    raw = metrics(docWidth=404, innerWidth=404, clientWidth=390)

    assert snapshot.page_metrics_from_raw(raw, mobile=True).inner_width == 390
    assert snapshot.page_metrics_from_raw(raw).inner_width == 404


@pytest.mark.parametrize("client_width", [1265, 1280, 0, None])
def test_a_scrollbar_wide_or_missing_client_width_keeps_inner_width(
    client_width: int | None,
) -> None:
    parsed = snapshot.page_metrics_from_raw(metrics(clientWidth=client_width))

    assert parsed is not None
    assert parsed.inner_width == 1280


def test_screenshot_style_neutralises_animations() -> None:
    assert "animation-duration: 0.001s !important;" in snapshot.SCREENSHOT_STYLE
    assert ".aos-init:not(.aos-animate)" in snapshot.SCREENSHOT_STYLE
