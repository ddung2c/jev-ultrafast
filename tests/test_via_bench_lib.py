"""Offline contracts for the VIA A/B bench's pure helpers. No paid APIs, no process spawns."""

import json

from scripts.via_bench_lib import (
    clean_argo_steps,
    clean_jev_steps,
    load_events,
    markdown_table,
    stages,
    summarize_runs,
    verify_naver_weather,
)


def test_verify_naver_weather_passes_on_a_search_results_page():
    url = "https://search.naver.com/search.naver?where=nexearch&query=%EC%84%9C%EC%9A%B8+%EB%82%A0%EC%94%A8"
    text = "서울 날씨\n27.1°\n맑음"
    result = verify_naver_weather(url, text)
    assert result["passed"]
    assert result["checks"] == {"host": True, "location": True, "weather_visible": True}


def test_verify_naver_weather_passes_on_the_dedicated_weather_app():
    # ARGO's desktopweb sometimes clicks through past the search results into
    # weather.naver.com, whose page shows a district ("중구 을지로1가"), never
    # the literal string "서울" -- accepted on host alone (see via_bench_lib).
    url = "https://weather.naver.com/today/09140104"
    text = "날씨\n중구 을지로1가\n26.8°\n맑음"
    result = verify_naver_weather(url, text)
    assert result["passed"]
    assert result["checks"] == {"host": True, "location": True, "weather_visible": True}


def test_verify_naver_weather_rejects_wrong_host_or_missing_weather():
    assert not verify_naver_weather("https://www.google.com/search?q=weather", "27.1°")["passed"]
    assert not verify_naver_weather(
        "https://search.naver.com/search.naver?query=%EC%84%9C%EC%9A%B8", "no results here"
    )["passed"]


def test_load_events_missing_file_is_empty_not_an_error(tmp_path):
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_parses_jsonl(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text('{"kind":"submit","t_ms":0}\n{"kind":"result","t_ms":100}\n')
    events = load_events(p)
    assert [e["kind"] for e in events] == ["submit", "result"]


def _event(kind, t_ms, **payload):
    return {"kind": kind, "t_ms": t_ms, "payload": payload}


def _argo_turn(reasoning, tool, t0, t_tool=None, t_result=None, t_step=None):
    """One ARGO terminal turn as tinicore/via-bench actually emit it: a
    reasoning line, an ANSI-colored [tool] line, a blank line, an ANSI-colored
    [call_...] JSON result line, a blank line, and via-bench's own step-count
    line -- exactly the shape observed live, 2026-09-27."""
    t_tool = t0 if t_tool is None else t_tool
    t_result = t0 + 1 if t_result is None else t_result
    t_step = t_result + 1 if t_step is None else t_step
    return [
        _event("progress", t0, text=reasoning),
        _event("progress", t_tool, text=f"\x1b[2m  \x1b[38;2;132;100;206m[tool]\x1b[0m {tool}\x1b[0m"),
        _event("progress", t_tool, text=""),
        _event(
            "progress",
            t_result,
            text='\x1b[2m  [call_00_abc123] \x1b[38;2;80;220;120mok\x1b[0m {"url":"https://x","snapshot":[]}\x1b[0m',
        ),
        _event("progress", t_result, text=""),
        _event("progress", t_step, text="step 01  tool_call  tool_call"),
    ]


def test_clean_argo_steps_extracts_reasoning_and_tool_skipping_noise():
    events = _argo_turn("Naver를 열겠습니다.", "desktopweb_open", t0=100)
    steps = clean_argo_steps(events)
    assert steps == [{"t_ms": 100, "text": "Naver를 열겠습니다.", "tool": "desktopweb_open"}]


def test_clean_argo_steps_joins_a_reasoning_block_split_across_events():
    events = [
        _event("progress", 100, text="첫 줄."),
        _event("progress", 150, text="둘째 줄."),
        _event("progress", 150, text="\x1b[2m  \x1b[38;2;132;100;206m[tool]\x1b[0m desktopweb_exec\x1b[0m"),
    ]
    steps = clean_argo_steps(events)
    assert steps == [{"t_ms": 100, "text": "첫 줄. 둘째 줄.", "tool": "desktopweb_exec"}]


def test_clean_argo_steps_flushes_a_trailing_turn_with_no_further_tool_call():
    events = _argo_turn("첫 턴.", "desktopweb_exec", t0=100) + [
        _event("progress", 500, text="작업이 끝났습니다."),
        _event("progress", 501, text="[TASK_COMPLETE]"),
    ]
    steps = clean_argo_steps(events)
    assert len(steps) == 2
    assert steps[1] == {"t_ms": 500, "text": "작업이 끝났습니다. [TASK_COMPLETE]", "tool": None}


def test_clean_jev_steps_carries_step_lines_through_unchanged():
    events = [
        _event("submit", 0, hint="x"),
        _event("progress", 300, text='step 01  TYPE_TEXT  검색어  "서울 날씨"  ·  decision 271 ms  ·  p 1.00'),
        _event("progress", 900, text="step 02  CLICK  서울 날씨 검색  ·  decision 261 ms  ·  p 0.91"),
        _event("progress", 1000, text=""),  # blank lines are dropped
    ]
    steps = clean_jev_steps(events)
    assert len(steps) == 2
    assert steps[0]["t_ms"] == 300
    assert "decision 271 ms" in steps[0]["text"]


def test_stages_computes_evaluator_executor_and_total():
    events = [
        _event("submit", 0),
        _event("intent_set", 400, thread_ids=["desktopweb-0"]),
        _event("progress", 900, text="step 1"),
        _event("progress", 1800, text="step 2"),
        _event("result", 3800, text="done"),
    ]
    s = stages(events)
    assert s["evaluator_ms"] == 400
    assert s["executor_ms"] == 3400
    assert s["first_action_ms"] == 900
    assert s["total_ms"] == 3800
    assert s["progress_count"] == 2
    assert s["thread_ids"] == ["desktopweb-0"]


def test_stages_handles_a_clarify_with_no_dispatch():
    events = [_event("submit", 0), _event("clarify", 250, text="which one?")]
    s = stages(events)
    assert s["evaluator_ms"] is None  # no intent_set at all
    assert s["total_ms"] is None
    assert s["executor_ms"] is None


def test_summarize_runs_picks_the_median_closest_successful_run():
    runs = [
        {"arm": "argo", "passed": True, "stages": {"total_ms": 10000}, "run_dir": "a1"},
        {"arm": "argo", "passed": True, "stages": {"total_ms": 40000}, "run_dir": "a2"},
        {"arm": "argo", "passed": False, "stages": {"total_ms": None}, "run_dir": "a3"},
        {"arm": "jev", "passed": True, "stages": {"total_ms": 4000}, "run_dir": "j1"},
        {"arm": "jev", "passed": True, "stages": {"total_ms": 5000}, "run_dir": "j2"},
        {"arm": "jev", "passed": True, "stages": {"total_ms": 6000}, "run_dir": "j3"},
    ]
    summary = summarize_runs(runs)
    assert summary["argo"]["attempted"] == 3
    assert summary["argo"]["succeeded"] == 2
    assert summary["argo"]["total_ms_median"] == 25000
    assert summary["jev"]["succeeded"] == 3
    assert summary["jev"]["representative_run_dir"] == "j2"


def test_markdown_table_renders_dash_for_missing_data():
    summary = {"argo": {"attempted": 0, "succeeded": 0}, "jev": {"attempted": 0, "succeeded": 0}}
    table = markdown_table(summary, "azure_openai/gpt-5.4-mini", "deepseek-flash")
    assert "—" in table
    assert "deepseek-flash" in table


def test_markdown_table_round_trips_through_json_safe_values():
    # summarize_runs' output must be directly json.dumps-able for summary.json.
    runs = [{"arm": "argo", "passed": True, "stages": {"total_ms": 7000}, "run_dir": "a1"}]
    summary = summarize_runs(runs)
    json.dumps(summary)  # raises if anything non-serializable slipped in
