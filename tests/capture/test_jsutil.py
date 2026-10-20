import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from playwright.async_api import Error as PlaywrightError

from app.screenshots.capture import jsutil, prepare, readiness, snapshot
from app.screenshots.capture.errors import EvaluateTimeout
from tests.capture.fakes import FakePage, results_sequence

MARKED = "() => { /*qa:probe*/ return 1; }"
CONTEXT_DESTROYED = (
    "Page.evaluate: Execution context was destroyed, most likely because of a "
    "navigation"
)


class _RecordingPage:
    def __init__(self, result: Any = 7) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.result = result

    async def evaluate(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.result


class _HangingPage:
    def __init__(self, gate: "asyncio.Future[Any]") -> None:
        self.gate = gate
        self.calls: list[tuple[Any, ...]] = []

    async def evaluate(self, *args: Any) -> Any:
        self.calls.append(args)
        return await self.gate


def _production_scripts() -> dict[str, str]:
    scripts = {}
    for module in (prepare, readiness, snapshot):
        for name, value in vars(module).items():
            if name.startswith("JS_") and isinstance(value, str) and value:
                scripts[f"{module.__name__}.{name}"] = value
    return scripts


@pytest.mark.parametrize(("name", "script"), sorted(_production_scripts().items()))
def test_every_page_script_carries_a_marker_first_in_its_body(
    name: str, script: str
) -> None:
    # A comment before `function` would break Playwright's function detection,
    # so the marker is the first token inside the body.
    body = script[script.index("{") + 1 :]
    assert body.startswith(" /*qa:"), name
    assert jsutil.script_marker(script) is not None


def test_script_marker() -> None:
    assert jsutil.script_marker(MARKED) == "probe"
    assert jsutil.script_marker("() => 1") is None


def test_is_context_destroyed() -> None:
    assert jsutil.is_context_destroyed(PlaywrightError(CONTEXT_DESTROYED))
    assert not jsutil.is_context_destroyed(PlaywrightError("Target closed"))


async def test_evaluate_passes_only_given_arguments() -> None:
    page = _RecordingPage()

    assert await jsutil.evaluate(page, MARKED) == 7
    await jsutil.evaluate(page, MARKED, 0)
    await jsutil.evaluate(page, MARKED, {"y": 1})

    assert page.calls == [(MARKED,), (MARKED, 0), (MARKED, {"y": 1})]


async def test_evaluate_with_timeout_returns_a_timely_result() -> None:
    page = _RecordingPage(result={"ok": True})

    assert await jsutil.evaluate(page, MARKED, 5, timeout_ms=1000) == {"ok": True}
    assert page.calls == [(MARKED, 5)]


async def test_evaluate_with_timeout_raises_evaluate_timeout() -> None:
    gate = asyncio.get_running_loop().create_future()

    with pytest.raises(EvaluateTimeout, match="probe") as exc_info:
        await jsutil.evaluate(_HangingPage(gate), MARKED, timeout_ms=10)

    assert not isinstance(exc_info.value, TimeoutError)
    gate.set_result(None)


async def test_evaluate_is_bounded_by_default() -> None:
    gate = asyncio.get_running_loop().create_future()

    with (
        patch("app.screenshots.capture.config.EVALUATE_TIMEOUT_MS", 10),
        pytest.raises(EvaluateTimeout, match="within 10 ms"),
    ):
        await jsutil.evaluate(_HangingPage(gate), MARKED)

    gate.set_result(None)


async def test_evaluate_with_timeout_consumes_late_failure() -> None:
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    gate = loop.create_future()

    try:
        with pytest.raises(EvaluateTimeout):
            await jsutil.evaluate(_HangingPage(gate), MARKED, timeout_ms=10)

        # The context closes under the abandoned evaluate.
        gate.set_exception(RuntimeError("Target page, context or browser closed"))
        for _ in range(3):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []


async def test_evaluate_reruns_once_after_a_late_redirect() -> None:
    page = FakePage(
        script_results={
            "probe": results_sequence(PlaywrightError(CONTEXT_DESTROYED), 42)
        }
    )

    assert await jsutil.evaluate(page, MARKED, 3) == 42

    assert page.evaluate_calls == [("probe", 3), ("probe", 3)]
    assert page.load_state_calls == [("domcontentloaded", {"timeout": 5000})]
    # The new document is awaited between the two runs.
    assert page.events == [
        ("evaluate", "probe"),
        ("load_state", "domcontentloaded"),
        ("evaluate", "probe"),
    ]


async def test_evaluate_reruns_even_when_the_load_state_wait_fails() -> None:
    page = FakePage(
        script_results={
            "probe": results_sequence(PlaywrightError(CONTEXT_DESTROYED), 1)
        },
        load_state_error=PlaywrightError("Timeout 10000ms exceeded"),
    )

    assert await jsutil.evaluate(page, MARKED) == 1
    assert page.evaluated == ["probe", "probe"]


async def test_evaluate_reruns_only_once() -> None:
    page = FakePage(script_results={"probe": PlaywrightError(CONTEXT_DESTROYED)})

    with pytest.raises(PlaywrightError, match="Execution context was destroyed"):
        await jsutil.evaluate(page, MARKED)

    assert page.evaluated == ["probe", "probe"]


async def test_evaluate_does_not_rerun_other_errors() -> None:
    page = FakePage(script_results={"probe": PlaywrightError("ReferenceError: x")})

    with pytest.raises(PlaywrightError, match="ReferenceError"):
        await jsutil.evaluate(page, MARKED)

    assert page.evaluated == ["probe"]
    assert page.load_state_calls == []


async def test_evaluate_bounds_the_wait_for_the_new_document() -> None:
    class _StuckLoadState(FakePage):
        async def wait_for_load_state(self, state: str = "load", **kwargs: Any):
            await super().wait_for_load_state(state, **kwargs)
            await asyncio.sleep(3600)

    page = _StuckLoadState(
        script_results={
            "probe": results_sequence(PlaywrightError(CONTEXT_DESTROYED), 5)
        }
    )

    with (
        patch("app.screenshots.capture.config.CONTEXT_DESTROYED_WAIT_MS", 10),
        patch("app.screenshots.capture.config.JS_TIMEOUT_GRACE_MS", 10),
    ):
        assert await asyncio.wait_for(jsutil.evaluate(page, MARKED), 5) == 5


async def test_evaluate_self_bounded_passes_the_timeout_to_the_script() -> None:
    page = _RecordingPage(result=True)

    assert await jsutil.evaluate_self_bounded(page, MARKED, 1234) is True
    assert page.calls == [(MARKED, 1234)]


async def test_evaluate_self_bounded_reruns_once_after_a_late_redirect() -> None:
    page = FakePage(
        script_results={
            "probe": results_sequence(PlaywrightError(CONTEXT_DESTROYED), True)
        }
    )

    assert await jsutil.evaluate_self_bounded(page, MARKED, 500) is True
    assert page.evaluate_calls == [("probe", 500), ("probe", 500)]
    assert [state for state, _ in page.load_state_calls] == ["domcontentloaded"]


async def test_evaluate_self_bounded_consumes_late_failure() -> None:
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    gate = loop.create_future()

    try:
        with (
            patch("app.screenshots.capture.config.JS_TIMEOUT_GRACE_MS", 0),
            pytest.raises(TimeoutError),
        ):
            # Its callers (readiness) translate the safety net's TimeoutError.
            await jsutil.evaluate_self_bounded(_HangingPage(gate), MARKED, 10)

        # The context closes under the abandoned evaluate.
        gate.set_exception(RuntimeError("Target page, context or browser closed"))
        for _ in range(3):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []
