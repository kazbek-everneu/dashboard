from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.screenshots.capture import config as cfg
from app.screenshots.capture import profiles, readiness
from app.screenshots.capture.errors import (
    EvaluateTimeout,
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.types import AssetIssue, NavigationResult, ResourceRecord
from tests.capture.fakes import (
    EMPTY_ASSET_STATE,
    READY_STABILITY_STATE,
    FakeConsoleMessage,
    FakePage,
    FakeRequest,
    FakeResponse,
    make_run,
    results_sequence,
)

MISSING_URL = "https://cdn.example.com/missing.jpg"
MISSING_IMAGE_STATE = {
    "visibleImageCount": 1,
    "visibleBackgroundCount": 0,
    "missingVisibleImageCount": 1,
    "missingBackgroundImageCount": 0,
    "hiddenPendingImageCount": 2,
    "missingSamples": [{"kind": "img", "url": MISSING_URL}],
}
GONE = "https://cdn.example.com/gone.png"
BLOCKED = "https://cdn.example.com/blocked.png"
RESET = "https://cdn.example.com/reset.png"
SLOW = "https://cdn.example.com/slow.png"
BACKGROUND = "https://cdn.example.com/hero.jpg"
STYLE = "https://example.com/app.css"
FONT = "https://fonts.example.com/brand.woff2"


class FakeNet(NetworkDiagnostics):
    """NetworkDiagnostics answering lookup / problem_resources /
    refused_stylesheets from fixed records."""

    def __init__(
        self,
        records: list[ResourceRecord] | None = None,
        *,
        problems: list[ResourceRecord] | None = None,
        refused: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.records = {record.url: record for record in records or []}
        self.problems = list(problems or [])
        self.refused = list(refused or [])

    def lookup(self, url: str) -> ResourceRecord | None:
        return self.records.get(url)

    def problem_resources(self, *, kinds=("stylesheet", "image", "font")):
        return [record for record in self.problems if record.resource_type in kinds]

    def refused_stylesheets(self) -> list[str]:
        return list(self.refused)


class BusyTracker(InflightTracker):
    def __init__(self, inflight: int = 1) -> None:
        super().__init__()
        self.count = inflight

    def inflight(self, *, include_data: bool = False) -> int:
        return self.count


def _record(url: str, resource_type: str = "image", **values: Any) -> ResourceRecord:
    values.setdefault("finished", True)
    return ResourceRecord(url, resource_type, **values)


def _recorder(answer: Any = None) -> tuple[list[Any], Any]:
    calls: list[Any] = []

    def record(arg: Any) -> Any:
        calls.append(arg)
        return answer

    return calls, record


# ---------------------------------------------------------------------------
# Activity tracker
# ---------------------------------------------------------------------------


def test_activity_tracker_is_part_of_the_capture_init_script() -> None:
    script = readiness.JS_ACTIVITY_TRACKER

    assert script.startswith(";(() => { /*qa:activity-tracker*/")
    assert script.rstrip().endswith("})();")
    assert readiness.JS_ACTIVITY_TRACKER in profiles.capture_init_script(
        DeviceProfile()
    )
    # Top frame only, never twice, never throws out of the shared init script.
    assert "window.top !== window || window.__qaActivity" in script
    assert "} catch (_) {}\n})();" in script


def test_activity_tracker_observes_mutations_and_root_resizes() -> None:
    script = readiness.JS_ACTIVITY_TRACKER

    # Observes the document node itself: it exists before documentElement.
    assert "observer.observe(document, {" in script
    for option in ("subtree: true", "childList: true", "characterData: true"):
        assert option in script
    assert (
        "attributeFilter: ['src', 'srcset', 'class', 'style', 'hidden', 'open']"
        in script
    )
    assert "new ResizeObserver(" in script
    assert "resizeObserver.observe(root)" in script
    # drain() takes the queued records and leaves lastActivity alone.
    drain = script[script.index("drain() {") : script.index("Object.defineProperty")]
    assert "takeRecords()" in drain
    assert "lastActivity" not in drain
    assert "quietForMs() {" in script


def test_stability_state_reports_quiet_time_and_forced_lazy_render() -> None:
    script = readiness.JS_COLLECT_STABILITY_STATE

    assert "window.__qaActivity" in script
    assert "quietForMs" in script
    assert "'[data-qa-cv-forced],[data-wpr-lazyrender]'" in script
    assert "contentVisibility === 'auto'" in script
    assert "querySelectorAll('*')" not in script


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


async def test_wait_for_fonts_raises_when_page_reports_timeout() -> None:
    page = FakePage(script_results={"wait-fonts": False})

    with pytest.raises(PageStabilizationTimeoutError):
        await readiness.wait_for_fonts(page, 1000)

    assert page.evaluate_calls == [("wait-fonts", 1000)]


async def test_wait_for_fonts_raises_on_a_timeout_with_families() -> None:
    page = FakePage(script_results={"wait-fonts": {"ready": False, "families": []}})

    with pytest.raises(PageStabilizationTimeoutError):
        await readiness.wait_for_fonts(page, 3000)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, readiness.FontsState()),
        (None, readiness.FontsState()),
        ({"ready": True}, readiness.FontsState()),
        (False, readiness.FontsState(ready=False)),
        (
            {"ready": False, "families": ["Brand", "Brand", "", "Icons"]},
            readiness.FontsState(ready=False, families=("Brand", "Icons")),
        ),
        (
            {"ready": False, "families": [f"F{index}" for index in range(30)]},
            readiness.FontsState(
                ready=False, families=tuple(f"F{index}" for index in range(10))
            ),
        ),
    ],
)
def test_fonts_state_from_raw(raw: object, expected: readiness.FontsState) -> None:
    assert readiness.fonts_state_from_raw(raw) == expected


def test_fonts_wait_relies_on_fonts_ready_not_on_status() -> None:
    script = readiness.JS_WAIT_FOR_FONTS

    assert "fonts.ready.then(" in script
    assert "fonts.status" not in script
    assert "face.status !== status" in script


async def test_finalize_fonts_timeout_is_a_warning_and_still_stable() -> None:
    page = FakePage(
        script_results={
            "wait-fonts": {"ready": False, "families": ["Brand Sans", "Icons"]}
        }
    )
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.stable is True
    assert result.unstable_reason is None
    assert result.fonts_ready is False
    assert result.issues == ()
    # It no longer stops at the fonts: decode, stability and blank check run.
    assert page.evaluated[:2] == ["wait-fonts", "decode-images"]
    assert page.evaluated.count("stability-state") == 5
    warning = run.report.warning("fonts_timeout")
    assert warning is not None
    assert warning.level == "warning"
    assert warning.details == {
        "families": ["Brand Sans", "Icons"],
        "timeout_ms": 5000,
        "count": 1,
    }
    assert "Brand Sans, Icons" in warning.message
    assert run.report.meta["readiness"] == {
        "stable": True,
        "fast_path": False,
        "fonts_ready": False,
    }


async def test_finalize_fonts_wait_that_hangs_is_a_timeout_too() -> None:
    run = make_run()
    hung = AsyncMock(side_effect=TimeoutError())

    with patch("app.screenshots.capture.jsutil.evaluate_self_bounded", hung):
        state = await readiness.fonts_state(run.page, 5000)

    assert state == readiness.FontsState(ready=False)


async def test_a_later_verdict_replaces_the_fonts_warning() -> None:
    page = FakePage()
    page.script_results["wait-fonts"] = results_sequence(
        {"ready": False, "families": ["Brand"]}, True
    )
    run = make_run(page=page)

    await readiness.finalize(run, window_ms=8000)
    assert run.report.warning_codes() == ["fonts_timeout"]

    result = await readiness.finalize(run, window_ms=8000)

    assert result.fonts_ready is True
    assert run.report.warnings == []
    assert run.report.meta["readiness"]["fonts_ready"] is True


# ---------------------------------------------------------------------------
# Visible images / backgrounds
# ---------------------------------------------------------------------------


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


async def test_decode_visible_images_hands_out_full_samples() -> None:
    long_url = "https://cdn.example.com/signed.jpg?" + "x" * 400
    page = FakePage(
        script_results={
            "decode-images": {
                **MISSING_IMAGE_STATE,
                "missingSamples": [
                    {"kind": "img", "url": long_url, "pending": False},
                    {"kind": "background", "url": BACKGROUND, "pending": True},
                    {"kind": "img", "url": long_url, "pending": False},
                ],
            }
        }
    )
    samples: list[readiness.AssetSample] = []

    with pytest.raises(PageAssetLoadError) as exc_info:
        await readiness.decode_visible_images(page, 1000, samples=samples)

    assert samples == [
        readiness.AssetSample("img", long_url, False),
        readiness.AssetSample("background", BACKGROUND, True),
    ]
    # The error text keeps its short form.
    assert exc_info.value.assets is not None
    assert len(exc_info.value.assets.missing_samples[0]) < 200


async def test_decode_timeout_keeps_the_partial_verdict() -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 2,
                "timedOut": True,
                "pendingCount": 1,
                "pendingSamples": [{"kind": "img", "url": SLOW, "pending": True}],
            }
        }
    )
    samples: list[readiness.AssetSample] = []

    with pytest.raises(PageAssetLoadError) as exc_info:
        await readiness.decode_visible_images(page, 1000, samples=samples)

    assert str(exc_info.value) == readiness.DECODE_TIMEOUT_MESSAGE
    assert exc_info.value.assets is not None
    assert exc_info.value.assets.visible_image_count == 2
    assert samples == [readiness.AssetSample("img", SLOW, True)]


def test_decode_script_waits_for_images_and_backgrounds_together() -> None:
    script = readiness.JS_DECODE_VISIBLE_IMAGES

    assert "images.map(waitForElementImage)" in script
    assert ".concat(backgrounds.map(waitForBackgroundUrl))" in script
    assert "pendingSamples" in script
    assert "backgroundImage" in script
    assert "visibleBackgroundCount" in script


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
    assert "visible-media" not in page.evaluated


async def test_finalize_bounds_fonts_and_decode_within_the_window() -> None:
    wide = FakePage()
    narrow = FakePage()

    await readiness.finalize(make_run(page=wide), window_ms=25000)
    await readiness.finalize(make_run(page=narrow), window_ms=8000)

    assert ("wait-fonts", 5000) in wide.evaluate_calls
    assert ("decode-images", 12000) in wide.evaluate_calls
    assert ("wait-fonts", 5000) in narrow.evaluate_calls
    assert ("decode-images", 8000) in narrow.evaluate_calls


async def test_finalize_returns_missing_assets_as_issues_and_goes_on() -> None:
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.issues == (
        AssetIssue(
            kind="img",
            url=MISSING_URL,
            category="unknown",
            reason="no network record",
        ),
    )
    # Same error text as before the readiness rework.
    assert run.scratch["asset_error"] == (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg; ignored hidden pending images: 2"
    )
    assert result.assets is not None and result.assets.missing_count == 1
    # No early stop: stability and the blank check still ran.
    assert page.evaluated.count("stability-state") == 5
    assert result.stable is True
    assert result.blank is False
    assert run.report.warnings == []  # broken (not pending): the pipeline's call


async def test_finalize_classifies_missing_assets_by_the_network() -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 4,
                "visibleBackgroundCount": 1,
                "missingVisibleImageCount": 4,
                "missingBackgroundImageCount": 1,
                "missingSamples": [
                    {"kind": "img", "url": GONE, "pending": False},
                    {"kind": "img", "url": BLOCKED, "pending": False},
                    {"kind": "img", "url": RESET, "pending": False},
                    {"kind": "img", "url": SLOW, "pending": True},
                    {"kind": "background", "url": BACKGROUND, "pending": False},
                ],
            }
        }
    )
    net = FakeNet(
        [
            _record(GONE, status=404, content_type="text/html"),
            _record(BLOCKED, status=403, content_type="text/html"),
            _record(RESET, finished=False, failure="net::ERR_CONNECTION_RESET"),
            _record(SLOW, status=200, content_type="image/png", finished=False),
        ]
    )
    run = make_run(page=page, net=net)

    result = await readiness.finalize(run, window_ms=8000)

    assert [(i.kind, i.url, i.category, i.status) for i in result.issues] == [
        ("img", GONE, "missing", 404),
        ("img", BLOCKED, "blocked", 403),
        ("img", RESET, "network", None),
        ("img", SLOW, "pending", 200),
        ("background", BACKGROUND, "unknown", None),
    ]
    assert [issue.reason for issue in result.issues] == [
        "HTTP 404",
        "HTTP 403, text/html",
        "net::ERR_CONNECTION_RESET",
        "still loading",
        "no network record",
    ]
    pending = run.report.warning("assets_pending")
    assert pending is not None
    # Samples in the shape the UI reads; count = assets still loading.
    assert pending.details == {
        "samples": [
            {
                "kind": "img",
                "url": SLOW,
                "status": 200,
                "category": "pending",
                "reason": "still loading",
            }
        ],
        "timeout_ms": 8000,
        "count": 1,
    }


async def test_finalize_decode_timeout_gives_pending_issues_and_a_warning() -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 3,
                "missingVisibleImageCount": 1,
                "missingSamples": [{"kind": "img", "url": GONE, "pending": False}],
                "timedOut": True,
                "pendingCount": 2,
                "pendingSamples": [
                    {"kind": "img", "url": SLOW, "pending": True},
                    {"kind": "background", "url": BACKGROUND, "pending": True},
                ],
            },
            "stability-state": {**READY_STABILITY_STATE, "textLength": 0},
        }
    )
    run = make_run(page=page, net=FakeNet([_record(GONE, status=404)]))

    result = await readiness.finalize(run, window_ms=25000)

    assert [(i.url, i.category) for i in result.issues] == [
        (GONE, "missing"),
        (SLOW, "pending"),
        (BACKGROUND, "pending"),
    ]
    assert run.scratch["asset_error"] == readiness.DECODE_TIMEOUT_MESSAGE
    assert result.assets is not None and result.assets.visible_image_count == 3
    assert result.blank is False  # still loading assets are content
    warning = run.report.warning("assets_pending")
    assert warning is not None and warning.level == "warning"
    assert warning.details["count"] == 2
    assert [sample["url"] for sample in warning.details["samples"]] == [
        SLOW,
        BACKGROUND,
    ]
    assert warning.details["samples"][1]["kind"] == "background"
    assert warning.details["timeout_ms"] == 12000


async def test_finalize_reports_a_decode_timeout_as_pending() -> None:
    page = FakePage(script_results={"decode-images": {"timedOut": True}})
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert run.scratch["asset_error"] == (
        "Timed out waiting for visible image/background assets"
    )
    assert [issue.category for issue in result.issues] == ["pending"]
    assert result.assets is None
    assert result.stable is True
    warning = run.report.warning("assets_pending")
    assert warning is not None
    assert warning.details["count"] == 1
    assert "samples" not in warning.details  # no URL to show


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


async def test_finalize_forgets_the_asset_error_of_an_earlier_verdict() -> None:
    run = make_run()
    run.scratch["asset_error"] = "stale"

    result = await readiness.finalize(run, window_ms=8000)

    assert result.issues == ()
    assert "asset_error" not in run.scratch


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "pending", "expected"),
    [
        (_record(GONE, status=404), None, ("missing", "HTTP 404")),
        (_record(GONE, status=410), None, ("missing", "HTTP 410")),
        (
            _record(BLOCKED, status=403, content_type="text/html; charset=utf-8"),
            None,
            ("blocked", "HTTP 403, text/html"),
        ),
        (
            _record(BLOCKED, status=200, content_type="text/html"),
            None,
            ("blocked", "HTTP 200, text/html"),
        ),
        (_record(BLOCKED, status=429), None, ("blocked", "HTTP 429")),
        (
            _record(BLOCKED, finished=False, failure="net::ERR_BLOCKED_BY_ORB"),
            None,
            ("blocked", "net::ERR_BLOCKED_BY_ORB"),
        ),
        (
            _record(RESET, finished=False, failure="net::ERR_CONNECTION_RESET"),
            None,
            ("network", "net::ERR_CONNECTION_RESET"),
        ),
        (
            _record(RESET, finished=False, failure="net::ERR_TIMED_OUT"),
            None,
            ("network", "net::ERR_TIMED_OUT"),
        ),
        (
            _record(RESET, finished=False, failure="NS_ERROR_NET_RESET"),
            None,
            ("network", "NS_ERROR_NET_RESET"),
        ),
        (
            _record(RESET, finished=False, failure="Could not connect: refused"),
            None,
            ("network", "Could not connect: refused"),
        ),
        (_record(SLOW, finished=False), None, ("pending", "still loading")),
        (_record(SLOW, status=200), True, ("pending", "still loading")),
        (None, True, ("pending", "still loading")),
        (None, None, ("unknown", "no network record")),
        (
            _record(SLOW, status=200, content_type="image/png"),
            False,
            ("unknown", "HTTP 200, image/png"),
        ),
        (_record(SLOW, status=500), None, ("unknown", "HTTP 500")),
        # The page says the element is done: an unfinished record is stale.
        (_record(SLOW, finished=False), False, ("unknown", "not finished")),
        (
            _record(SLOW, finished=False, failure="net::ERR_FAILED"),
            None,
            ("unknown", "net::ERR_FAILED"),
        ),
    ],
)
def test_classify(
    record: ResourceRecord | None, pending: bool | None, expected: tuple[str, str]
) -> None:
    assert readiness.classify(record, pending=pending) == expected


def test_classify_asset_without_a_url_does_not_look_up() -> None:
    class NoLookup(FakeNet):
        def lookup(self, url: str) -> ResourceRecord | None:
            raise AssertionError("no lookup without a url")

    issue = readiness.classify_asset(NoLookup(), "img", "", pending=True)

    assert issue == AssetIssue("img", "", "pending", None, "still loading")


async def test_finalize_classifies_through_the_network_registry() -> None:
    """The production NetworkDiagnostics fed by page events, end to end."""
    page = FakePage(
        script_results={
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 3,
                "missingVisibleImageCount": 3,
                "missingSamples": [
                    {"kind": "img", "url": GONE + "#frag", "pending": False},
                    {"kind": "img", "url": BLOCKED, "pending": False},
                    {"kind": "img", "url": RESET, "pending": False},
                ],
            }
        }
    )
    run = make_run(page=page, attach=False)
    run.net.attach(page)
    responses = [
        FakeResponse(status=404, url=GONE, resource_type="image"),
        FakeResponse(
            status=403,
            url=BLOCKED,
            resource_type="image",
            headers={"Content-Type": "text/html"},
        ),
        FakeResponse(
            status=200,
            url=STYLE,
            resource_type="stylesheet",
            headers={"Content-Type": "text/html; charset=utf-8"},
        ),
    ]
    for response in responses:
        page.emit("response", response)
        page.emit("requestfinished", response.request)
    page.emit(
        "requestfailed",
        FakeRequest(url=RESET, resource_type="image", failure="net::ERR_TIMED_OUT"),
    )
    refused = "https://cdn.example.com/theme.css"
    page.emit(
        "console",
        FakeConsoleMessage(
            f"Did not parse stylesheet at '{refused}' because non CSS MIME types "
            "are not allowed in strict mode."
        ),
    )

    result = await readiness.finalize(run, window_ms=8000)

    assert [(i.kind, i.url, i.category, i.status) for i in result.issues] == [
        ("img", GONE + "#frag", "missing", 404),
        ("img", BLOCKED, "blocked", 403),
        ("img", RESET, "network", None),
        ("stylesheet", STYLE, "blocked", 200),
        ("stylesheet", refused, "blocked", None),
    ]


# ---------------------------------------------------------------------------
# Stylesheets and fonts
# ---------------------------------------------------------------------------


async def test_stylesheet_issues_from_network_console_and_dom() -> None:
    html_css = "https://example.com/waf.css"
    missing_css = "https://example.com/missing.css"
    refused_css = "https://cdn.example.com/refused.css"
    unapplied_css = "https://example.com/unapplied.css"
    loading_css = "https://example.com/loading.css"
    unknown_css = "https://example.com/unknown.css"
    recovered_css = "https://example.com/recovered.css"
    net = FakeNet(
        [
            _record(html_css, "stylesheet", status=200, content_type="text/html"),
            _record(missing_css, "stylesheet", status=404),
            _record(unapplied_css, "stylesheet", status=200, content_type="text/css"),
            _record(loading_css, "stylesheet", finished=False),
            _record(recovered_css, "stylesheet", status=200, content_type="text/css"),
        ],
        problems=[
            _record(html_css, "stylesheet", status=200, content_type="text/html"),
            _record(missing_css, "stylesheet", status=404),
            # A failed first request; the latest one loaded fine.
            _record(recovered_css, "stylesheet", status=503),
            _record(GONE, "image", status=404),
        ],
        refused=[html_css, refused_css],
    )
    page = FakePage(
        script_results={
            "broken-stylesheets": [
                missing_css,
                unapplied_css,
                loading_css,
                unknown_css,
            ]
        }
    )
    run = make_run(page=page, net=net)

    issues = await readiness.stylesheet_issues(run)

    assert [(i.url, i.category, i.status, i.reason) for i in issues] == [
        (html_css, "blocked", 200, "HTTP 200, text/html"),
        (missing_css, "missing", 404, "HTTP 404"),
        (refused_css, "blocked", None, "refused by the browser"),
        (
            unapplied_css,
            "unknown",
            200,
            "stylesheet not applied (HTTP 200, text/css)",
        ),
    ]
    assert {issue.kind for issue in issues} == {"stylesheet"}


async def test_finalize_adds_stylesheet_and_font_issues() -> None:
    net = FakeNet(
        [
            _record(STYLE, "stylesheet", status=403, content_type="text/html"),
            _record(FONT, "font", finished=False, failure="net::ERR_CONNECTION_RESET"),
        ],
        problems=[
            _record(STYLE, "stylesheet", status=403, content_type="text/html"),
            _record(FONT, "font", finished=False, failure="net::ERR_CONNECTION_RESET"),
        ],
    )
    run = make_run(net=net)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.issues == (
        AssetIssue("stylesheet", STYLE, "blocked", 403, "HTTP 403, text/html"),
        AssetIssue("font", FONT, "network", None, "net::ERR_CONNECTION_RESET"),
    )
    assert run.scratch["asset_error"] == (
        "Page assets did not load: 1 stylesheet, 1 font; samples: stylesheet: "
        f"{STYLE} (HTTP 403, text/html); font: {FONT} (net::ERR_CONNECTION_RESET)"
    )
    assert run.report.warnings == []


async def test_finalize_appends_stylesheet_issues_to_the_decode_error_text() -> None:
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})
    net = FakeNet(
        [_record(STYLE, "stylesheet", status=404)],
        problems=[_record(STYLE, "stylesheet", status=404)],
    )
    run = make_run(page=page, net=net)

    result = await readiness.finalize(run, window_ms=8000)

    assert [issue.kind for issue in result.issues] == ["img", "stylesheet"]
    assert run.scratch["asset_error"] == (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg; ignored hidden pending images: 2; "
        f"also did not load: 1 stylesheet; samples: stylesheet: {STYLE} (HTTP 404)"
    )


# ---------------------------------------------------------------------------
# Blank check
# ---------------------------------------------------------------------------


async def test_finalize_flags_a_blank_page() -> None:
    page = FakePage(
        script_results={"stability-state": {**READY_STABILITY_STATE, "textLength": 0}}
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is True
    assert result.stable is True
    assert "visible-media" in page.evaluated


async def test_finalize_counts_svg_canvas_video_and_iframes_as_content() -> None:
    page = FakePage(
        script_results={
            "stability-state": {**READY_STABILITY_STATE, "textLength": 0},
            "visible-media": 2,
        }
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is False
    for tag in ("svg", "canvas", "video", "iframe"):
        assert tag in readiness.JS_VISIBLE_MEDIA


async def test_finalize_is_not_blank_when_the_media_check_hangs() -> None:
    page = FakePage(
        script_results={
            "stability-state": {**READY_STABILITY_STATE, "textLength": 0},
            "visible-media": EvaluateTimeout("visible-media did not finish"),
        }
    )

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is False


async def test_finalize_skips_the_media_check_for_pages_with_text() -> None:
    page = FakePage()

    result = await readiness.finalize(make_run(page=page), window_ms=8000)

    assert result.blank is False
    assert "visible-media" not in page.evaluated


# ---------------------------------------------------------------------------
# DOM stability and the activity fast path
# ---------------------------------------------------------------------------


async def test_wait_for_dom_stability_needs_three_identical_ready_samples() -> None:
    page = FakePage()

    fast = await readiness.wait_for_dom_stability(page, 8000)

    assert fast is False
    assert page.evaluated == ["stability-state"] * 4
    assert page.waits == [500, 500, 500]


async def test_fast_path_returns_after_one_sample_when_quiet_and_idle() -> None:
    page = FakePage(
        script_results={
            "stability-state": {**READY_STABILITY_STATE, "quietForMs": 1500}
        }
    )

    fast = await readiness.wait_for_dom_stability(page, 8000, tracker=InflightTracker())

    assert fast is True
    assert page.evaluated == ["stability-state"]
    assert page.waits == []


async def test_old_path_still_needs_three_samples_when_not_quiet_enough() -> None:
    page = FakePage(
        script_results={"stability-state": {**READY_STABILITY_STATE, "quietForMs": 900}}
    )

    fast = await readiness.wait_for_dom_stability(page, 8000, tracker=InflightTracker())

    assert fast is False
    assert page.evaluated == ["stability-state"] * 4
    assert page.waits == [500, 500, 500]


async def test_fast_path_needs_an_idle_tracker() -> None:
    quiet = {**READY_STABILITY_STATE, "quietForMs": 60000}
    busy = FakePage(script_results={"stability-state": quiet})
    untracked = FakePage(script_results={"stability-state": quiet})

    busy_fast = await readiness.wait_for_dom_stability(
        busy, 8000, tracker=BusyTracker()
    )
    untracked_fast = await readiness.wait_for_dom_stability(untracked, 8000)

    assert busy_fast is False
    assert untracked_fast is False
    assert busy.evaluated == untracked.evaluated == ["stability-state"] * 4


async def test_fast_path_needs_a_ready_page() -> None:
    page = FakePage(
        script_results={
            "stability-state": {
                **READY_STABILITY_STATE,
                "pendingVisibleImages": 1,
                "quietForMs": 60000,
            }
        }
    )
    clock = iter([0.0, 0.0, 0.5, 1.0, 9.0])

    with (
        patch("app.screenshots.capture.clock.now", side_effect=lambda: next(clock)),
        pytest.raises(PageStabilizationTimeoutError),
    ):
        await readiness.wait_for_dom_stability(page, 8000, tracker=InflightTracker())

    assert page.evaluated == ["stability-state"] * 3


async def test_fast_path_is_checked_on_every_sample() -> None:
    page = FakePage()
    page.script_results["stability-state"] = results_sequence(
        {**READY_STABILITY_STATE, "scrollHeight": 100, "quietForMs": 200},
        {**READY_STABILITY_STATE, "scrollHeight": 200, "quietForMs": 700},
        {**READY_STABILITY_STATE, "scrollHeight": 300, "quietForMs": 1600},
    )

    fast = await readiness.wait_for_dom_stability(page, 8000, tracker=InflightTracker())

    assert fast is True
    assert page.evaluated == ["stability-state"] * 3
    assert page.waits == [500, 500]


async def test_a_page_that_never_goes_quiet_is_judged_by_the_old_rule() -> None:
    # A countdown: text changes every second at the same length.
    page = FakePage(
        script_results={"stability-state": {**READY_STABILITY_STATE, "quietForMs": 400}}
    )
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.stable is True
    assert result.fast_path is False


async def test_finalize_takes_the_fast_path_and_records_it() -> None:
    page = FakePage(
        script_results={
            "stability-state": {**READY_STABILITY_STATE, "quietForMs": 4000}
        }
    )
    run = make_run(page=page)

    result = await readiness.finalize(run, window_ms=8000)

    assert result.stable is True
    assert result.fast_path is True
    assert page.waits == []
    assert run.report.meta["readiness"] == {
        "stable": True,
        "fast_path": True,
        "fonts_ready": True,
    }


async def test_finalize_passes_the_run_tracker_to_the_stability_wait() -> None:
    stability = AsyncMock(return_value=True)
    run = make_run()

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        result = await readiness.finalize(run, window_ms=8000)

    assert stability.await_args.kwargs["tracker"] is run.tracker
    assert result.fast_path is True


async def test_finalize_reports_dom_timeout_as_unstable() -> None:
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))
    run = make_run()

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        result = await readiness.finalize(run, window_ms=8000)

    assert result.stable is False
    assert result.unstable_reason == "dom"
    assert result.fonts_ready is True
    assert result.assets is not None
    assert result.blank is False
    assert stability.await_args.args[1] == 8000
    assert run.report.meta["readiness"]["stable"] is False


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


async def test_stability_state_timeout_counts_as_no_data() -> None:
    page = FakePage(
        script_results={"stability-state": EvaluateTimeout("stability-state hung")}
    )

    state = await readiness.collect_stability_state(page)

    assert state["readyState"] == "unknown"
    assert page.evaluate_calls == [("stability-state", None)]


# ---------------------------------------------------------------------------
# retry_in_place
# ---------------------------------------------------------------------------


async def test_retry_in_place_leaves_missing_and_font_issues_alone() -> None:
    issues = (
        AssetIssue("img", GONE, "missing", 404, "HTTP 404"),
        AssetIssue("stylesheet", STYLE, "missing", 410, "HTTP 410"),
        AssetIssue("font", FONT, "network", None, "net::ERR_CONNECTION_RESET"),
    )
    page = FakePage()

    still = await readiness.retry_in_place(make_run(page=page), list(issues))

    assert still == issues
    assert page.evaluated == []
    assert page.waits == []


async def test_retry_in_place_re_requests_and_returns_what_is_still_broken() -> None:
    missing = AssetIssue("img", GONE, "missing", 404, "HTTP 404")
    blocked = AssetIssue("img", BLOCKED, "blocked", 403, "HTTP 403, text/html")
    reset = AssetIssue("img", RESET, "network", None, "net::ERR_CONNECTION_RESET")
    retry_calls, retry_assets = _recorder({"images": 2, "stylesheets": 0})
    page = FakePage(
        script_results={
            "retry-assets": retry_assets,
            "retry-status": results_sequence({"pending": 2}, {"pending": 0}),
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 3,
                "missingVisibleImageCount": 2,
                "missingSamples": [
                    {"kind": "img", "url": GONE, "pending": False},
                    {"kind": "img", "url": RESET, "pending": False},
                ],
            },
        }
    )
    net = FakeNet(
        [
            _record(GONE, status=404),
            _record(BLOCKED, status=200, content_type="image/png"),
            _record(RESET, status=403, content_type="text/html"),
        ]
    )
    run = make_run(page=page, net=net)

    still = await readiness.retry_in_place(run, (missing, blocked, reset))

    # The 404 one is not asked for again; the recovered one is gone; the
    # still broken one is classified by its latest request.
    assert retry_calls == [{"images": [BLOCKED, RESET], "stylesheets": []}]
    assert still == (
        missing,
        AssetIssue("img", RESET, "blocked", 403, "HTTP 403, text/html"),
    )
    assert ("decode-images", cfg.RETRY_DECODE_MS) in page.evaluate_calls
    assert page.waits == [cfg.RETRY_POLL_MS]
    assert run.scratch["asset_error"] == (
        "Page assets did not load: 2 img; samples: img: "
        f"{GONE} (HTTP 404); img: {RESET} (HTTP 403, text/html)"
    )


async def test_retry_in_place_recovers_everything() -> None:
    blocked = AssetIssue("img", BLOCKED, "blocked", 403, "HTTP 403, text/html")
    page = FakePage(script_results={"retry-assets": {"images": 1, "stylesheets": 0}})
    run = make_run(page=page)
    run.scratch["asset_error"] = "Visible page assets did not load: 1 visible img"

    assert await readiness.retry_in_place(run, [blocked]) == ()
    assert "asset_error" not in run.scratch


async def test_retry_in_place_reloads_stylesheets_by_the_latest_request() -> None:
    recovered = "https://example.com/recovered.css"
    blocked_again = "https://example.com/still-html.css"
    retry_calls, retry_assets = _recorder({"images": 0, "stylesheets": 2})
    page = FakePage(script_results={"retry-assets": retry_assets})
    net = FakeNet(
        [
            _record(recovered, "stylesheet", status=200, content_type="text/css"),
            _record(blocked_again, "stylesheet", status=200, content_type="text/html"),
        ]
    )
    issues = (
        AssetIssue("stylesheet", recovered, "blocked", 200, "HTTP 200, text/html"),
        AssetIssue("stylesheet", blocked_again, "network", None, "net::ERR_TIMED_OUT"),
    )

    still = await readiness.retry_in_place(make_run(page=page, net=net), issues)

    assert retry_calls == [{"images": [], "stylesheets": [recovered, blocked_again]}]
    assert still == (
        AssetIssue("stylesheet", blocked_again, "blocked", 200, "HTTP 200, text/html"),
    )
    assert "decode-images" not in page.evaluated


async def test_retry_in_place_falls_back_to_the_link_state_without_records() -> None:
    good = "https://example.com/good.css"
    bad = "https://example.com/bad.css"
    page = FakePage(
        script_results={
            "retry-assets": {"images": 0, "stylesheets": 2},
            "stylesheet-state": {good: True, bad: False},
        }
    )
    issues = (
        AssetIssue("stylesheet", good, "blocked", None, "refused by the browser"),
        AssetIssue("stylesheet", bad, "blocked", None, "refused by the browser"),
    )

    still = await readiness.retry_in_place(make_run(page=page, net=FakeNet()), issues)

    assert still == issues[1:]
    assert ("stylesheet-state", [good, bad]) in page.evaluate_calls


async def test_retry_in_place_waits_for_backgrounds_and_pending_images() -> None:
    background = AssetIssue("background", BACKGROUND, "unknown", None, "no record")
    slow = AssetIssue("img", SLOW, "pending", None, "still loading")
    page = FakePage(
        script_results={
            "decode-images": {
                **EMPTY_ASSET_STATE,
                "visibleImageCount": 1,
                "missingVisibleImageCount": 1,
                "missingSamples": [{"kind": "img", "url": SLOW, "pending": True}],
            }
        }
    )
    run = make_run(page=page)
    run.report.warn(
        "assets_pending",
        "2 visible image(s) still loading after 8 s",
        samples=[{"url": SLOW}, {"url": BACKGROUND}],
        timeout_ms=8000,
    )

    still = await readiness.retry_in_place(run, (background, slow))

    # Neither is re-requested: the background is no element, the image is
    # still on its way. The background loaded meanwhile.
    assert "retry-assets" not in page.evaluated
    assert "retry-status" not in page.evaluated
    assert still == (slow,)
    warning = run.report.warning("assets_pending")
    assert warning is not None
    assert warning.details == {
        "samples": [
            {
                "kind": "img",
                "url": SLOW,
                "status": None,
                "category": "pending",
                "reason": "still loading",
            }
        ],
        "timeout_ms": 8000,
        "count": 1,
    }


async def test_retry_in_place_drops_the_pending_warning_once_loaded() -> None:
    slow = AssetIssue("img", SLOW, "pending", None, "still loading")
    run = make_run()
    run.report.warn("assets_pending", "1 visible image(s) still loading")

    assert await readiness.retry_in_place(run, (slow,)) == ()
    assert run.report.warning("assets_pending") is None


async def test_retry_in_place_keeps_issues_when_the_recheck_cannot_tell() -> None:
    blocked = AssetIssue("img", BLOCKED, "blocked", 403, "HTTP 403")
    unnamed = AssetIssue("img", "", "pending", None, "decode timed out")
    page = FakePage(script_results={"decode-images": {"timedOut": True}})

    still = await readiness.retry_in_place(make_run(page=page), (blocked, unnamed))

    assert still == (blocked, unnamed)


async def test_retry_wait_gives_up_when_nothing_is_on_the_wire() -> None:
    blocked = AssetIssue("img", BLOCKED, "blocked", 403, "HTTP 403")
    page = FakePage(
        script_results={
            "retry-assets": {"images": 1, "stylesheets": 0},
            "retry-status": {"pending": 1},
        }
    )

    await readiness.retry_in_place(make_run(page=page), (blocked,))

    # Idle for RETRY_IDLE_MS (one poll more when the real clock adds a hair).
    assert cfg.RETRY_IDLE_MS <= sum(page.waits) <= cfg.RETRY_IDLE_MS + cfg.RETRY_POLL_MS


async def test_retry_wait_is_capped() -> None:
    blocked = AssetIssue("img", BLOCKED, "blocked", 403, "HTTP 403")
    page = FakePage(
        script_results={
            "retry-assets": {"images": 1, "stylesheets": 0},
            "retry-status": {"pending": 1},
        }
    )

    await readiness.retry_in_place(
        make_run(page=page, tracker=BusyTracker()), (blocked,)
    )

    assert sum(page.waits) == cfg.RETRY_WAIT_MS
    assert set(page.waits) == {cfg.RETRY_POLL_MS}


def test_retry_script_re_requests_the_same_url() -> None:
    script = readiness.JS_RETRY_ASSETS

    # Remove + set back the same value (every engine fetches again, see the
    # script's comment) — never a cache-busting change of the URL.
    assert "el.removeAttribute(name);\n        el.setAttribute(name, value);" in script
    assert "reassign(source, 'srcset')" in script
    assert "reassign(img, 'srcset')" in script
    assert "reassign(img, 'src')" in script
    assert "reassign(link, 'href')" in script
    assert "Date.now" not in script
    assert "window.__qaActivity.drain()" in script
