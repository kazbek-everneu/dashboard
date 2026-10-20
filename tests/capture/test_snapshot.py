from app.screenshots.capture import snapshot
from app.screenshots.capture.types import SnapshotResult
from tests.capture.fakes import FakePage, make_config, make_run


async def test_take_snapshot_jpeg_full_page() -> None:
    page = FakePage()

    result = await snapshot.take_snapshot(make_run(page=page))

    assert result == SnapshotResult(data=b"img")
    assert page.screenshot_kwargs == {
        "full_page": True,
        "type": "jpeg",
        "timeout": 1000,
        "animations": "disabled",
        "caret": "hide",
        "style": snapshot.SCREENSHOT_STYLE,
        "quality": 85,
    }


async def test_take_snapshot_png_has_no_quality() -> None:
    page = FakePage(screenshot_data=b"png")

    result = await snapshot.take_snapshot(
        make_run(page=page, config=make_config(fmt="png", timeout=30000))
    )

    assert result.data == b"png"
    assert page.screenshot_kwargs is not None
    assert "quality" not in page.screenshot_kwargs
    assert page.screenshot_kwargs["type"] == "png"
    assert page.screenshot_kwargs["timeout"] == 30000


def test_screenshot_style_neutralises_animations() -> None:
    assert "animation-duration: 0.001s !important;" in snapshot.SCREENSHOT_STYLE
    assert ".aos-init:not(.aos-animate)" in snapshot.SCREENSHOT_STYLE
