"""Snapshot stage: the full-page screenshot itself."""

from typing import Any

from app.screenshots.capture.types import CaptureRun, SnapshotResult

SCREENSHOT_STYLE = """
*, *::before, *::after {
    animation-duration: 0.001s !important;
    animation-delay: 0s !important;
    transition-duration: 0.001s !important;
    transition-delay: 0s !important;
}

[data-aos],
[data-scroll],
[data-animate],
.wow,
.animate__animated,
.aos-init:not(.aos-animate),
.animate-on-scroll,
.fade-in:not(.visible),
.fade-up:not(.visible),
.slide-up:not(.visible),
.slide-in:not(.visible),
.reveal:not(.revealed),
.scroll-animate:not(.animated),
.lazy-animate,
.is-hidden-scroll,
.before-animate,
.gsap-hidden {
    opacity: 1 !important;
    visibility: visible !important;
    transform: none !important;
    clip-path: none !important;
    pointer-events: auto !important;
}
"""


def screenshot_kwargs(run: CaptureRun) -> dict[str, Any]:
    config = run.config
    kwargs: dict[str, Any] = {
        "full_page": True,
        "type": config.fmt,
        "timeout": config.timeout,
        "animations": "disabled",
        "caret": "hide",
        "style": SCREENSHOT_STYLE,
    }
    if config.fmt == "jpeg":
        kwargs["quality"] = config.quality
    return kwargs


async def take_snapshot(run: CaptureRun) -> SnapshotResult:
    """Full-page screenshot of ``run.page`` in the configured format."""
    data = await run.page.screenshot(**screenshot_kwargs(run))
    return SnapshotResult(data=data)
