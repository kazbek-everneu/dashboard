import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from app.screenshots.capture import jsutil, prepare, readiness
from app.screenshots.capture.errors import EvaluateTimeout

MARKED = "() => { /*qa:probe*/ return 1; }"


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
    for module in (prepare, readiness):
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


async def test_evaluate_without_timeout_passes_only_given_arguments() -> None:
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


async def test_evaluate_self_bounded_passes_the_timeout_to_the_script() -> None:
    page = _RecordingPage(result=True)

    assert await jsutil.evaluate_self_bounded(page, MARKED, 1234) is True
    assert page.calls == [(MARKED, 1234)]


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
            await jsutil.evaluate_self_bounded(_HangingPage(gate), MARKED, 10)

        # The context closes under the abandoned evaluate.
        gate.set_exception(RuntimeError("Target page, context or browser closed"))
        for _ in range(3):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []
