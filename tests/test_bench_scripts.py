"""Unit tests of the capture bench scripts (scripts/capture_bench.py,
scripts/bench_compare.py): case / viewport / --set parsing, host pacing, the
summary math, the runner on a fake pipeline and the before/after comparison.
No browser, no network."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bench_compare
from scripts import capture_bench as bench

ROOT = Path(__file__).resolve().parents[1]


def _png(width: int, height: int) -> bytes:
    """Just enough of a PNG for image_dimensions: signature + IHDR."""
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\0\0\0"
    return b"\x89PNG\r\n\x1a\n" + len(ihdr).to_bytes(4, "big") + b"IHDR" + ihdr


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def test_scripts_import_without_playwright_or_the_app() -> None:
    code = (
        "import sys, scripts.capture_bench, scripts.bench_compare\n"
        "heavy = sorted(m for m in sys.modules "
        "if m.split('.')[0] in ('playwright', 'app'))\n"
        "print(heavy)\n"
        "sys.exit(1 if heavy else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Cases: TSV, URL lists, viewports
# ---------------------------------------------------------------------------


def test_parse_cases_tsv_reads_columns_skips_comments_and_applies_defaults() -> None:
    text = (
        "# id\turl\tbrowser\tWxH\tdevice\twait_until\ttimeout_ms\n"
        "\n"
        "id\turl\tbrowser\n"
        "full\thttps://a.test/x\tchrome\t1920x1080\tdesktop\tnetworkidle\t100000\n"
        "  # indented comment\n"
        "alias\thttps://b.test/\tchromium\t390x844\n"
        "auto\thttps://c.test/\twebkit\t834x1112\tauto\n"
        "short\thttps://d.test/\n"
    )

    cases = bench.parse_cases_tsv(text, wait_until="domcontentloaded", timeout_ms=5000)

    assert [case.id for case in cases] == ["full", "alias", "auto", "short"]
    full, alias, auto, short = cases
    assert full == bench.Case(
        id="full",
        url="https://a.test/x",
        browser="chrome",
        width=1920,
        height=1080,
        device="desktop",
        wait_until="networkidle",
        timeout_ms=100000,
    )
    assert (alias.browser, alias.width, alias.height, alias.device) == (
        "chrome",
        390,
        844,
        None,
    )
    assert (alias.wait_until, alias.timeout_ms) == ("domcontentloaded", 5000)
    assert (auto.browser, auto.device) == ("webkit", None)
    assert (short.browser, short.viewport) == (
        "chrome",
        {"width": 1920, "height": 1080},
    )


def test_reference_cases_parse() -> None:
    text = (ROOT / "scripts/bench/reference.tsv").read_text(encoding="utf-8")

    cases = bench.parse_cases_tsv(text)

    assert len(cases) == 13
    by_id = {case.id: case for case in cases}
    assert by_id["ta-form-1920-webkit"].browser == "webkit"
    assert by_id["ta-form-390-chrome"].device == "mobile"
    assert by_id["ta-form-390-chrome"].wait_until == "networkidle"
    assert by_id["zempleo-390-chrome"].timeout_ms == 60000
    assert by_id["perimeter-1920-chrome"].host == "perimeter.net"
    assert by_id["ta-wh-1920-chrome"].host == "taservices.com"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("x\thttps://a.test/\tsafari", "unknown browser"),
        ("x\thttps://a.test/\tchrome\t1920by1080", "invalid viewport"),
        ("x\thttps://a.test/\tchrome\t0x1080", "invalid viewport"),
        ("x\tftp://a.test/", "invalid URL"),
        ("x y\thttps://a.test/", "invalid case id"),
        ("../x\thttps://a.test/", "invalid case id"),
        ("x\thttps://a.test/\tchrome\t1920x1080\twatch", "unknown device"),
        ("x\thttps://a.test/\tchrome\t1920x1080\tdesktop\tidle", "unknown wait_until"),
        ("x\thttps://a.test/\tchrome\t1920x1080\tdesktop\tload\t-5", "invalid timeout"),
        ("x\thttps://a.test/\tchrome\t1x1\tdesktop\tload\t1\textra", "at most 7"),
        ("x https://a.test/ chrome", "tab-separated"),
    ],
)
def test_parse_cases_tsv_rejects_bad_rows(line: str, message: str) -> None:
    with pytest.raises(bench.CaseError, match=message) as excinfo:
        bench.parse_cases_tsv(f"# header\nok\thttps://ok.test/\n{line}\n")
    assert str(excinfo.value).startswith("line 3:")


def test_parse_cases_tsv_rejects_duplicate_ids() -> None:
    text = "a\thttps://a.test/\nb\thttps://b.test/\na\thttps://c.test/\n"
    with pytest.raises(bench.CaseError, match="duplicate case ids: a"):
        bench.parse_cases_tsv(text)


def test_parse_viewport_specs() -> None:
    specs = bench.parse_viewport_specs(
        "1920x1080:desktop, 390X844:Mobile,834x1112,1024x768:auto,390x844:mobile"
    )

    assert specs == [
        (1920, 1080, "desktop"),
        (390, 844, "mobile"),
        (834, 1112, None),
        (1024, 768, None),
    ]


@pytest.mark.parametrize("spec", ["", " , ", "1920", "1920x", "0x10", "390x844:phone"])
def test_parse_viewport_specs_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(bench.CaseError):
        bench.parse_viewport_specs(spec)


def test_parse_browsers_applies_aliases_and_dedupes() -> None:
    assert bench.parse_browsers("chromium, firefox,chrome") == ["chrome", "firefox"]
    with pytest.raises(bench.CaseError):
        bench.parse_browsers(" , ")


def test_parse_url_list_skips_comments_blanks_and_duplicates() -> None:
    text = "# sweep\nhttps://a.test/\n\n  https://b.test/x  \nhttps://a.test/\n"

    assert bench.parse_url_list(text) == ["https://a.test/", "https://b.test/x"]
    with pytest.raises(bench.CaseError, match="line 2"):
        bench.parse_url_list("https://a.test/\nnot a url\n")


def test_url_slug() -> None:
    assert bench.url_slug("https://www.Waterstep.org/") == "waterstep.org"
    assert (
        bench.url_slug("https://waterstep.org/get-involved/donate/")
        == "waterstep.org-get-involved-donate"
    )
    with_query = bench.url_slug("https://a.test/p?x=1")
    assert (
        with_query.startswith("a.test-p-") and len(with_query) == len("a.test-p-") + 6
    )
    long_slug = bench.url_slug("https://a.test/" + "segment/" * 20)
    assert len(long_slug) <= 60 + 7
    assert long_slug != bench.url_slug("https://a.test/" + "segment/" * 21)


def test_cases_from_urls_builds_unique_ids() -> None:
    urls = ["https://waterstep.org/", "https://perimeter.net/who-we-help/"]
    viewports = bench.parse_viewport_specs("1920x1080:desktop,390x844:mobile")

    cases = bench.cases_from_urls(
        urls, ["chrome", "firefox"], viewports, wait_until="load", timeout_ms=30000
    )

    assert [case.id for case in cases] == [
        "waterstep.org-1920x1080-chrome",
        "waterstep.org-390x844-chrome",
        "waterstep.org-1920x1080-firefox",
        "waterstep.org-390x844-firefox",
        "perimeter.net-who-we-help-1920x1080-chrome",
        "perimeter.net-who-we-help-390x844-chrome",
        "perimeter.net-who-we-help-1920x1080-firefox",
        "perimeter.net-who-we-help-390x844-firefox",
    ]
    assert cases[1].device == "mobile"
    for case in cases:
        bench.validate_case_id(case.id)

    same_size = bench.cases_from_urls(
        ["https://a.test/"],
        ["chrome"],
        bench.parse_viewport_specs("1024x768:desktop,1024x768:tablet"),
    )
    assert [case.id for case in same_size] == [
        "a.test-1024x768-chrome-desktop",
        "a.test-1024x768-chrome-tablet",
    ]


def test_dedupe_ids_numbers_repeats() -> None:
    cases = [bench.Case(id="a", url="https://a.test/") for _ in range(3)]

    assert [case.id for case in bench.dedupe_ids(cases)] == ["a", "a-2", "a-3"]


def test_filter_cases_and_expand_jobs() -> None:
    cases = [
        bench.Case(id="ta-form-1920-chrome", url="https://a.test/"),
        bench.Case(id="ta-wh-1920-chrome", url="https://a.test/"),
        bench.Case(id="ta-form-1920-webkit", url="https://a.test/", browser="webkit"),
    ]

    assert [c.id for c in bench.filter_cases(cases, only="ta-form-*")] == [
        "ta-form-1920-chrome",
        "ta-form-1920-webkit",
    ]
    assert [
        c.id for c in bench.filter_cases(cases, only="ta-*", browsers=["webkit"])
    ] == ["ta-form-1920-webkit"]

    jobs = bench.expand_jobs(cases[:2], repeat=2)
    assert [(job.case.id, job.repeat) for job in jobs] == [
        ("ta-form-1920-chrome", 1),
        ("ta-wh-1920-chrome", 1),
        ("ta-form-1920-chrome", 2),
        ("ta-wh-1920-chrome", 2),
    ]
    assert jobs[2].image_stem == "ta-form-1920-chrome-r2"
    assert bench.expand_jobs(cases[:1])[0].image_stem == "ta-form-1920-chrome"


def test_read_results_keeps_the_last_record_per_case_and_resume_reruns_skips(
    tmp_path: Path,
) -> None:
    lines = [
        {"id": "a", "repeat": 1, "ok": False, "skipped": True},
        {"id": "b", "repeat": 1, "ok": False, "error_class": "BlankPageError"},
        {"id": "a", "repeat": 2, "ok": True},
        {"id": "a", "repeat": 1, "ok": True},
    ]
    (tmp_path / bench.RESULTS_FILE).write_text(
        "".join(json.dumps(line) + "\n" for line in lines)
    )

    records = bench.read_results(tmp_path)

    assert [(r["id"], r["repeat"], r["ok"]) for r in records] == [
        ("a", 1, True),
        ("b", 1, False),
        ("a", 2, True),
    ]
    cases = [bench.Case(id=i, url=f"https://{i}.test/") for i in ("a", "b", "c")]
    jobs = bench.expand_jobs(cases, repeat=2)
    stale = [{"id": "a", "repeat": 1, "skipped": True}, {"id": "b", "repeat": 1}]
    assert [job.key for job in bench.pending_jobs(jobs, stale)] == [
        ("a", 1),
        ("c", 1),
        ("a", 2),
        ("b", 2),
        ("c", 2),
    ]


# ---------------------------------------------------------------------------
# --set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("FORCE_PASS2=true", ("FORCE_PASS2", True)),
        ("RETRY_COUNT=0", ("RETRY_COUNT", 0)),
        ("RATIO = 0.5", ("RATIO", 0.5)),
        ('TYPES=["image","font"]', ("TYPES", ["image", "font"])),
        ("FLAG=False", ("FLAG", False)),
        ("VALUE=None", ("VALUE", None)),
        ("NAME=hello world", ("NAME", "hello world")),
        ('NAME="1"', ("NAME", "1")),
        ("NAME=", ("NAME", "")),
        ("URL=a=b", ("URL", "a=b")),
    ],
)
def test_parse_set_option(option: str, expected: tuple[str, object]) -> None:
    assert bench.parse_set_option(option) == expected


@pytest.mark.parametrize("option", ["NOVALUE", "=1", "1X=2", "A-B=1"])
def test_parse_set_option_rejects_bad_names(option: str) -> None:
    with pytest.raises(bench.CaseError):
        bench.parse_set_option(option)


def test_apply_overrides_sets_and_coerces_attributes() -> None:
    module = SimpleNamespace(
        RETRY_COUNT=1, RATIO=1.5, TYPES=frozenset({"image"}), FLAG=False
    )

    notes = bench.apply_overrides(
        module,
        {
            "RETRY_COUNT": 0,
            "RATIO": 2,
            "TYPES": ["image", "font"],
            "NEW_GATE": True,
            "FLAG": "yes",
        },
    )

    assert module.RETRY_COUNT == 0
    assert module.RATIO == 2.0 and isinstance(module.RATIO, float)
    assert module.TYPES == frozenset({"image", "font"})
    assert module.NEW_GATE is True
    assert module.FLAG == "yes"
    assert any(note.startswith("NEW_GATE: not defined") for note in notes)
    assert any(note.startswith("FLAG: bool replaced by str") for note in notes)
    assert len(notes) == 2


# ---------------------------------------------------------------------------
# Host pacing
# ---------------------------------------------------------------------------


def _job(case_id: str, url: str) -> bench.Job:
    return bench.Job(bench.Case(id=case_id, url=url))


def test_host_key_ignores_www_and_case() -> None:
    assert bench.host_key("https://WWW.Zempleo.com/a?b") == "zempleo.com"
    assert bench.host_key("https://everspandev.wpenginepowered.com/x") == (
        "everspandev.wpenginepowered.com"
    )


def test_scheduler_skips_cases_past_the_host_limit() -> None:
    jobs = [
        _job("ta-1", "https://www.taservices.com/a"),
        _job("ta-2", "https://www.taservices.com/b"),
        _job("ta-3", "https://taservices.com/c"),
        _job("other", "https://other.test/"),
    ]
    scheduler = bench.Scheduler(jobs, bench.HostGate(max_loads=2))

    picked = [scheduler.pick(0.0) for _ in range(4)]

    assert [(action, job.case.id) for action, job in picked] == [
        ("run", "ta-1"),
        ("run", "ta-2"),
        ("skip", "ta-3"),
        ("run", "other"),
    ]
    assert scheduler.pick(0.0) is None


def test_host_gate_counts_retries_and_reloads_as_loads() -> None:
    gate = bench.HostGate(max_loads=3)
    gate.start("a.test")
    gate.finish("a.test", 1.0, extra_loads=2)

    assert gate.loads["a.test"] == 3
    assert gate.over_limit("a.test")
    assert not gate.over_limit("b.test")


def test_scheduler_spaces_loads_of_one_host_and_interleaves_others() -> None:
    jobs = [
        _job("a-1", "https://a.test/1"),
        _job("a-2", "https://a.test/2"),
        _job("b-1", "https://b.test/1"),
    ]
    scheduler = bench.Scheduler(jobs, bench.HostGate(delay_s=20))

    action, first = scheduler.pick(0.0)
    assert (action, first.case.id) == ("run", "a-1")
    action, second = scheduler.pick(0.0)
    assert (action, second.case.id) == ("run", "b-1")  # a.test is busy
    assert scheduler.pick(0.0) == float("inf")  # a-2 waits for a-1 to finish

    scheduler.gate.finish("a.test", 5.0)
    assert scheduler.pick(10.0) == 25.0  # 20 s after a-1 finished
    action, third = scheduler.pick(25.0)
    assert (action, third.case.id) == ("run", "a-2")
    assert scheduler.pick(25.0) is None


def test_scheduler_without_delay_runs_one_host_concurrently() -> None:
    jobs = [_job("a-1", "https://a.test/1"), _job("a-2", "https://a.test/2")]
    scheduler = bench.Scheduler(jobs, bench.HostGate())

    assert [scheduler.pick(0.0)[1].case.id for _ in range(2)] == ["a-1", "a-2"]


async def test_scheduler_next_waits_out_the_host_delay() -> None:
    jobs = [_job("a-1", "https://a.test/1"), _job("a-2", "https://a.test/2")]
    scheduler = bench.Scheduler(jobs, bench.HostGate(delay_s=0.05))
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    async def worker() -> None:
        while (picked := await scheduler.next()) is not None:
            _action, job = picked
            starts[job.case.id] = time.monotonic()
            ends[job.case.id] = time.monotonic()
            await scheduler.done(job)

    await asyncio.wait_for(asyncio.gather(worker(), worker()), timeout=5)

    assert starts["a-2"] - ends["a-1"] >= 0.045


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def test_warning_entries_and_meta_subset_of_a_capture_report() -> None:
    from app.screenshots.capture.report import CaptureReport

    report = CaptureReport()
    report.warn("dom_unstable", "unstable", reason="dom")
    report.warn("overlays_hidden", "hidden", samples=["x" * 200, "b", "c", "d"])
    report.warn("overlays_hidden", "hidden again")
    report.set_meta("navigation", http_status=200, final_url="https://a.test/")
    report.set_meta("readiness", fast_path=True)
    report.set_meta("timings_extra", value=1)
    report.timings_ms.update(total=1234, goto=100)

    entries = bench.warning_entries(report)

    assert entries[0] == {
        "code": "dom_unstable",
        "level": "warning",
        "count": 1,
        "details": {"reason": "dom"},
    }
    assert entries[1]["code"] == "overlays_hidden"
    assert entries[1]["level"] == "info"
    assert entries[1]["count"] == 2
    samples = entries[1]["details"]["samples"]
    assert len(samples) == 3 and len(samples[0]) == bench.DETAIL_MAX_CHARS
    assert bench.meta_subset(report) == {
        "navigation": {"http_status": 200, "final_url": "https://a.test/"},
        "readiness": {"fast_path": True},
    }
    assert bench.timings_of(report) == {"total": 1234, "goto": 100}


def test_warning_entries_tolerate_dicts_and_missing_fields() -> None:
    report = SimpleNamespace(
        warnings=[
            {"code": "http_error", "level": "warning", "details": {"count": 2}},
            {"level": "info"},
            SimpleNamespace(code="redirected", level=None, details=None),
        ]
    )

    assert bench.warning_entries(report) == [
        {"code": "http_error", "level": "warning", "count": 2},
        {"code": "redirected", "level": "warning", "count": 1},
    ]
    assert bench.warning_entries(SimpleNamespace()) == []
    assert bench.meta_subset(SimpleNamespace()) == {}
    assert bench.timings_of(None) == {}


def test_error_class_name_marks_playwright_errors() -> None:
    playwright_timeout = type("TimeoutError", (Exception,), {})
    playwright_timeout.__module__ = "playwright._impl._errors"

    assert bench.error_class_name(None) is None
    assert bench.error_class_name(ValueError("x")) == "ValueError"
    assert bench.error_class_name(playwright_timeout()) == "playwright.TimeoutError"


def test_jsonable_and_clip() -> None:
    value = {"a": float("nan"), "b": {1, 2}, "c": ("x" * 400,), "d": object}

    result = bench.jsonable(value)

    assert result["a"] is None
    assert result["b"] == [1, 2]
    assert len(result["c"][0]) == bench.META_MAX_CHARS
    assert result["c"][0].endswith("...")
    assert isinstance(result["d"], str)
    json.dumps(result)


def test_call_accepting_drops_unknown_keywords() -> None:
    def old(browser, *, url):
        return (browser, url)

    def new(browser, *, url, **extra):
        return (browser, url, extra)

    assert bench.call_accepting(old, "b", url="u", browser_provider=1) == ("b", "u")
    assert bench.call_accepting(new, "b", url="u", browser_provider=1) == (
        "b",
        "u",
        {"browser_provider": 1},
    )


def test_extra_loads_counts_retries_and_reloads() -> None:
    record = {
        "attempts": 2,
        "warnings": [{"code": "page_reloaded", "level": "info", "count": 1}],
    }

    assert bench.reload_count(record) == 1
    assert bench.extra_loads(record) == 2
    assert bench.reload_count({"meta": {"navigation": {"reloads": 2}}}) == 2
    assert bench.extra_loads({}) == 0


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([], 50, None),
        ([None, True, float("nan")], 50, None),
        ([5], 90, 5.0),
        ([4, 1, 3, 2], 50, 2.5),
        ([1, 2, 3, 4], 90, pytest.approx(3.7)),
        ([1, 2, 3, 4], 0, 1.0),
        ([1, 2, 3, 4], 100, 4.0),
    ],
)
def test_percentile(values: list, q: float, expected: object) -> None:
    assert bench.percentile(values, q) == expected


def test_stats_rounds_and_counts() -> None:
    assert bench.stats([1000, 2000, 3001]) == {
        "n": 3,
        "p50": 2000,
        "p90": 2801,
        "max": 3001,
    }
    assert bench.stats([1.23456, 2.0], digits=3)["p50"] == 1.617
    assert bench.stats([]) == {"n": 0, "p50": None, "p90": None, "max": None}


def _record(case_id: str, **values: object) -> dict:
    record = {
        "id": case_id,
        "repeat": 1,
        "url": f"https://{case_id}.test/",
        "browser": "chrome",
        "device": "desktop",
        "viewport": {"width": 1920, "height": 1080},
        "ok": True,
        "skipped": False,
        "error_class": None,
        "error": None,
        "attempts": 1,
        "duration": 1.0,
        "wall_s": 1.5,
        "timings_ms": {},
        "warnings": [],
        "meta": {},
    }
    record.update(values)
    return record


def _warning(code: str, level: str = "info", count: int = 1) -> dict:
    return {"code": code, "level": level, "count": count}


SUMMARY_RECORDS = [
    _record(
        "fast",
        timings_ms={"total": 1000, "goto": 100, "snapshot": 50},
        warnings=[_warning("overlays_hidden")],
        meta={"readiness": {"fast_path": True}},
        image_width=1920,
        image_height=4000,
    ),
    _record(
        "unstable",
        timings_ms={"total": 3000, "goto": 300},
        captured_with_unstable_dom=True,
        warnings=[_warning("dom_unstable", "warning")],
        image_width=3518,
        image_height=5000,
    ),
    _record(
        "mobile-ok",
        device="mobile",
        viewport={"width": 390, "height": 844},
        attempts=2,
        timings_ms={"total": 2000, "goto": 200, "reload": 900},
        warnings=[_warning("page_reloaded"), _warning("screenshot_retried")],
        meta={"readiness": {"stable": True}},
        image_width=390,
        image_height=8000,
    ),
    _record(
        "mobile-failed",
        device="mobile",
        viewport={"width": 390, "height": 844},
        ok=False,
        error_class="PageAssetLoadError",
        error="Visible page assets did not load",
        phase="readiness",
        wall_s=50.0,
        timings_ms={"total": 40000},
    ),
    _record(
        "mobile-skipped",
        device="mobile",
        ok=False,
        skipped=True,
        error_class="Skipped",
        error="host limit",
        wall_s=0,
    ),
    _record(
        "firefox-failed",
        browser="firefox",
        ok=False,
        error_class="playwright.TimeoutError",
        error="Timeout 30000ms exceeded",
        wall_s=31.0,
    ),
]


def test_summarize_counts_and_percentiles() -> None:
    summary = bench.summarize(SUMMARY_RECORDS)

    assert list(summary["groups"]) == [
        "chrome/desktop",
        "chrome/mobile",
        "firefox/desktop",
    ]
    overall = summary["overall"]
    assert (overall["cases"], overall["ok"], overall["failed"], overall["skipped"]) == (
        6,
        3,
        2,
        1,
    )
    assert overall["failed_by_class"] == {
        "PageAssetLoadError": 1,
        "playwright.TimeoutError": 1,
    }
    assert overall["total_ms"] == {"n": 3, "p50": 2000, "p90": 2800, "max": 3000}
    assert list(overall["phases_ms"]) == ["goto", "reload", "snapshot"]
    assert overall["phases_ms"]["goto"]["p50"] == 200
    assert overall["wall_s"]["n"] == 5  # every case that ran, skipped excluded
    assert overall["unstable"] == 1
    assert overall["fast_path"] == {"count": 1, "of": 2, "known": 1, "rate": 0.5}
    assert overall["warnings"] == {
        "dom_unstable": 1,
        "overlays_hidden": 1,
        "page_reloaded": 1,
        "screenshot_retried": 1,
    }
    assert (overall["reloads"], overall["screenshot_retries"], overall["retried"]) == (
        1,
        1,
        1,
    )
    assert overall["width_eq_viewport"] == {"count": 2, "of": 3, "rate": 0.6667}

    desktop = summary["groups"]["chrome/desktop"]
    assert desktop["total_ms"]["p50"] == 2000
    assert desktop["fast_path"] == {"count": 1, "of": 1, "known": 1, "rate": 1.0}
    mobile = summary["groups"]["chrome/mobile"]
    assert (mobile["ok"], mobile["failed"], mobile["skipped"]) == (1, 1, 1)
    assert mobile["fast_path"] == {"count": 0, "of": 1, "known": 0, "rate": 0.0}

    assert [f["id"] for f in summary["failures"]] == [
        "mobile-failed",
        "firefox-failed",
    ]
    assert summary["failures"][0]["phase"] == "readiness"
    assert summary["skipped"] == [
        {"id": "mobile-skipped", "repeat": 1, "reason": "host limit"}
    ]


def test_summarize_tolerates_sparse_records() -> None:
    summary = bench.summarize([{"id": "x", "ok": True}, {"id": "y"}])

    assert summary["overall"]["ok"] == 1
    assert summary["overall"]["failed"] == 1
    assert summary["overall"]["total_ms"]["n"] == 0
    assert list(summary["groups"]) == ["?/?"]


def test_format_summary_mentions_groups_failures_and_skips() -> None:
    summary = {
        "run": {"label": "base-ref", "elapsed_s": 125, "overrides": {"X": 1}},
        **bench.summarize(SUMMARY_RECORDS),
    }

    text = bench.format_summary(summary)

    assert text.splitlines()[0] == (
        "capture bench base-ref: 6 cases, ok 3, failed 2, skipped 1, elapsed 2m05s"
    )
    assert 'overrides: {"X": 1}' in text
    assert "chrome/mobile" in text and "firefox/desktop" in text
    assert "FAIL mobile-failed [chrome/mobile] PageAssetLoadError" in text
    assert "skip mobile-skipped: host limit" in text
    assert "warnings (cases): dom_unstable 1" in text


# ---------------------------------------------------------------------------
# The runner, on a fake pipeline
# ---------------------------------------------------------------------------


class _FakeBrowser:
    version = "149.0.1.2"

    def __init__(self) -> None:
        self.closed = False

    def is_connected(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True


async def test_bench_runs_cases_on_a_fake_pipeline(tmp_path: Path) -> None:
    from app.screenshots.capture.errors import PageAssetLoadError
    from app.screenshots.capture.report import CaptureReport
    from app.screenshots.capture.types import CaptureOutcome, CaptureResult

    urls = tmp_path / "urls.txt"
    urls.write_text("https://ok.test/\nhttps://fail.test/\n")
    out = tmp_path / "run"
    args = bench.build_parser().parse_args(
        [
            "--urls",
            str(urls),
            "--viewports",
            "1920x1080:desktop,390x844",
            "--out",
            str(out),
            "--concurrency",
            "2",
            "--fmt",
            "png",
        ]
    )
    jobs = bench.expand_jobs(bench.load_cases(args))
    (out / bench.IMAGE_DIR).mkdir(parents=True)
    calls: list[dict] = []

    async def fake_capture(*, browser, url, viewport, config, profile, **kwargs):
        calls.append(
            {"url": url, "viewport": viewport, "config": config, "profile": profile}
        )
        assert callable(kwargs.get("browser_provider"))
        report = CaptureReport()
        report.timings_ms.update(total=1500, goto=200)
        if "fail" in url:
            report.current_phase = "readiness"
            return CaptureResult(
                outcome=None,
                error=PageAssetLoadError("assets did not load"),
                report=report,
                attempts=2,
                duration=3.0,
            )
        report.warn("overlays_hidden", "hidden", samples=["#cookie"])
        outcome = CaptureOutcome(
            data=_png(viewport["width"], 3000), duration=1.2, report=report
        )
        return CaptureResult(
            outcome=outcome, error=None, report=report, attempts=1, duration=1.2
        )

    runner = bench.Bench(args, jobs)
    runner.pipeline = SimpleNamespace(capture_with_retries=fake_capture)
    launched: list[str] = []

    async def fake_launch(_pw, name):
        launched.append(name)
        return _FakeBrowser()

    pool = bench.BrowserPool(None, fake_launch)
    await runner.run(pool)

    assert launched == ["chrome"]
    records = {r["id"]: r for r in bench.read_results(out)}
    assert sorted(records) == [
        "fail.test-1920x1080-chrome",
        "fail.test-390x844-chrome",
        "ok.test-1920x1080-chrome",
        "ok.test-390x844-chrome",
    ]
    ok = records["ok.test-390x844-chrome"]
    assert ok["ok"] is True
    assert ok["device"] == "mobile"
    assert ok["browser_version"] == "149.0.1.2"
    assert ok["profile"]["type"] == "mobile"
    assert ok["image"] == "img/ok.test-390x844-chrome.png"
    assert (ok["image_width"], ok["image_height"]) == (390, 3000)
    assert (out / ok["image"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert ok["timings_ms"] == {"total": 1500, "goto": 200}
    assert ok["warnings"][0]["code"] == "overlays_hidden"
    failed = records["fail.test-1920x1080-chrome"]
    assert failed["ok"] is False
    assert failed["error_class"] == "PageAssetLoadError"
    assert failed["phase"] == "readiness"
    assert failed["attempts"] == 2
    assert "image" not in failed
    config = calls[0]["config"]
    assert (config.fmt, config.wait_until, config.timeout) == ("png", "load", 30000)


def test_main_refuses_to_mix_runs_and_summarizes(tmp_path: Path, capsys) -> None:
    out = tmp_path / "run"
    out.mkdir()
    (out / bench.RESULTS_FILE).write_text(
        "".join(json.dumps(record) + "\n" for record in SUMMARY_RECORDS) + "{broken\n"
    )
    urls = tmp_path / "urls.txt"
    urls.write_text("https://a.test/\n")

    with pytest.raises(SystemExit) as excinfo:
        bench.main(["--urls", str(urls), "--out", str(out)])
    assert excinfo.value.code == 2
    assert "already exists" in capsys.readouterr().err

    assert bench.main(["--out", str(out), "--summarize"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("capture bench run: 6 cases, ok 3, failed 2")
    summary = json.loads((out / bench.SUMMARY_FILE).read_text())
    assert summary["overall"]["cases"] == 6


def test_main_rejects_bad_arguments(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("https://a.test/\n")
    base = ["--urls", str(urls), "--out", str(tmp_path / "run")]

    for extra in (
        ["--endpoint", "ws://browser:3001"],
        ["--endpoint", "http://browser"],
        ["--concurrency", "0"],
        ["--viewports", "big"],
        ["--set", "1=2"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            bench.main(base + extra)
        assert excinfo.value.code == 2, extra
    with pytest.raises(SystemExit):
        bench.main(["--out", str(tmp_path / "run")])


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


def _write_run(directory: Path, records: list[dict], run: dict | None = None) -> None:
    (directory / "img").mkdir(parents=True)
    for record in records:
        if record.get("image"):
            (directory / record["image"]).write_bytes(_png(10, 10))
    (directory / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )
    if run is not None:
        (directory / "summary.json").write_text(json.dumps({"run": run}))


def _ok(case_id: str, height: int, total: int, **values: object) -> dict:
    return _record(
        case_id,
        image=f"img/{case_id}.jpg",
        image_width=values.pop("image_width", 1920),
        image_height=height,
        bytes=1000,
        timings_ms={"total": total, "goto": total // 10},
        duration=total / 1000,
        **values,
    )


@pytest.fixture
def compare_runs(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "bench" / "before"
    after = tmp_path / "bench" / "after"
    _write_run(
        before,
        [
            _ok("grows", 1000, 1000),
            _ok("cropped", 2000, 1000, image_width=3518),
            _record("fixed", ok=False, error_class="PageAssetLoadError", error="x"),
            _ok("gone", 1000, 1000),
        ],
        run={"label": "base", "overrides": {}},
    )
    _write_run(
        after,
        [
            _ok("grows", 1200, 1500, warnings=[_warning("overlays_hidden")]),
            _ok("cropped", 2001, 900, warnings=[_warning("overflow_cropped")]),
            _ok("fixed", 3000, 2000),
            _ok("new", 1000, 1000),
        ],
        run={"label": "after", "overrides": {"FORCE_PASS2": True}},
    )
    return before, after


def test_case_flags() -> None:
    before = _ok("a", 1000, 1000)
    after = _ok("a", 1200, 1500, warnings=[_warning("overlays_hidden")])

    assert bench_compare.case_flags(before, after) == [
        "height +20%",
        "slower x1.5 (no reload)",
        "+overlays_hidden",
    ]
    assert bench_compare.case_flags(None, after) == ["only in after"]
    assert bench_compare.case_flags(before, None) == ["only in before"]
    failed = _record("a", ok=False, error_class="BlankPageError")
    assert bench_compare.case_flags(before, failed) == ["ok -> failed"]
    other = _record("a", ok=False, error_class="PageAssetLoadError")
    assert bench_compare.case_flags(failed, other) == [
        "BlankPageError -> PageAssetLoadError"
    ]
    wider = _ok("a", 1000, 1000, image_width=5785)
    narrower = _ok("a", 1000, 1000, image_width=2878)
    assert bench_compare.case_flags(wider, narrower) == ["width 5785 -> 2878"]
    reloaded = _ok("a", 1000, 2000, warnings=[_warning("page_reloaded")])
    assert "slower x2.0 (reloaded)" in bench_compare.case_flags(before, reloaded)


def test_metric_rows_show_deltas_and_tones() -> None:
    before = bench.summarize([_ok("a", 100, 1000), _record("b", ok=False)])
    after = bench.summarize([_ok("a", 100, 2000), _ok("b", 100, 2000)])

    rows = {
        row.label: row
        for row in bench_compare.metric_rows(before["overall"], after["overall"])
    }

    assert (rows["ok"].before, rows["ok"].after, rows["ok"].delta) == ("1", "2", "+1")
    assert rows["ok"].tone == "better"
    assert rows["failed"].tone == "better"
    assert rows["failed: Unknown"].delta == "-1"
    assert rows["total p50 (s)"].delta == "+1.00 (+100%)"
    assert rows["total p50 (s)"].tone == "worse"
    assert rows["total p50 (s)"].pct == "+100%"
    assert rows["width = viewport"].before == "100% (1/1)"
    assert rows["width = viewport"].delta == "+0 pp"
    assert rows["goto p50 (ms)"].after == "200"
    missing = bench_compare.metric_rows(None, after["overall"])
    assert missing[0].before == "-" and missing[0].delta == ""


def test_compare_writes_html_and_text_summary(
    compare_runs: tuple[Path, Path], tmp_path: Path, capsys
) -> None:
    before, after = compare_runs
    html_path = tmp_path / "reports" / "compare.html"

    assert bench_compare.main([str(before), str(after), "--out", str(html_path)]) == 0

    text = capsys.readouterr().out
    assert "before=base (4 cases) after=after (4 cases): 3 matched" in text
    assert "1 only before, 1 only after" in text
    assert "status changes: 1\n  fixed: failed -> ok" in text
    assert "height changes > 10%: 1" in text
    assert "grows: 1000 -> 1200 px (+20%) [+overlays_hidden]" in text
    assert "heights within 2 px: 1/2 ok pairs (others: grows +200 px)" in text
    assert (
        "chrome/desktop: ok 3 -> 4; failed 1 -> 0; total p50 (s) 1.00 -> 1.25 (+25%)"
        in text
    )
    assert "slower > 1.3x: 1\n  grows: 1.0 -> 1.5 s (x1.5, NO page_reloaded)" in text
    assert "width changes: 1\n  cropped: 3518 -> 1920 px (viewport 1920)" in text
    assert "cropped: +overflow_cropped" in text
    assert f"html: {html_path}" in text

    page = html_path.read_text()
    assert page.startswith("<!doctype html>")
    assert 'src="../bench/before/img/grows.jpg"' in page
    assert 'href="../bench/after/img/grows.jpg"' in page
    assert "only in before" in page and "only in after" in page
    assert "FORCE_PASS2" in page
    assert "width 3518 -&gt; 1920" in page
    assert "<script src" not in page and "<link" not in page  # self-contained


def test_compare_reports_a_missing_run(tmp_path: Path, capsys) -> None:
    code = bench_compare.main(
        [str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(tmp_path / "x.html")]
    )

    assert code == 2
    assert "results.jsonl does not exist" in capsys.readouterr().err


def test_rel_link_is_relative_to_the_html_directory(tmp_path: Path) -> None:
    target = tmp_path / "data" / "bench" / "run a" / "img" / "x.jpg"

    assert (
        bench_compare.rel_link(target, tmp_path / "data" / "bench")
        == "run%20a/img/x.jpg"
    )
    assert (
        bench_compare.rel_link(target, tmp_path / "reports")
        == "../data/bench/run%20a/img/x.jpg"
    )
