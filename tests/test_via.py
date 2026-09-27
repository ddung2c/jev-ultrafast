"""Offline contracts for the VIA CLI entry point. No paid APIs."""

import json

import pytest

from jev_ultrafast import via


def test_start_url_matches_a_site_named_in_the_goal():
    assert via.start_url("네이버에서 서울 날씨 검색") == "https://www.naver.com"
    assert via.start_url("search Google for cats") == "https://www.google.com"
    assert via.start_url("Find a book on this site") == via.DEFAULT_URL


def test_start_url_explicit_argument_wins_and_is_validated():
    assert via.start_url("anything", "en.wikipedia.org") == "https://en.wikipedia.org"
    with pytest.raises(ValueError, match="http"):
        via.start_url("anything", "javascript:alert(1)")


def test_step_line_includes_text_and_timing():
    entry = {
        "step": 2,
        "operation": "TYPE_TEXT",
        "action": "검색어를 입력해 주세요.",
        "text": "서울 날씨",
        "latency_ms": 312,
        "text_latency_ms": 841,
        "probability": 0.9723,
    }
    line = via.step_line(entry)
    assert line.startswith("step 02  TYPE_TEXT  검색어를 입력해 주세요.")
    assert '"서울 날씨"' in line
    assert "decision 312 ms" in line
    assert "text 841 ms" in line
    assert "p 0.97" in line


def test_step_line_omits_text_fields_for_a_click():
    entry = {
        "step": 1,
        "operation": "CLICK",
        "action": "Search\nbutton",
        "text": None,
        "latency_ms": 48,
        "text_latency_ms": 0,
        "probability": 0.91,
    }
    line = via.step_line(entry)
    assert '"' not in line
    assert "text " not in line.split("·")[-2]  # no stray text-latency segment
    assert "Search button" in line  # newline collapsed to a space


def test_summarize_counts_stale_decisions_and_sums_only_numeric_usage():
    state = {
        "status": "done",
        "elapsed_ms": 4500,
        "history": [{"step": 1}, {"step": 2}],
        "decisions": [
            {"latency_ms": 100, "usage": {"input_tokens": 50, "note": "n/a"}},
            {"latency_ms": 200, "usage": {"input_tokens": 30}},
            {"latency_ms": 150, "usage": {}},  # a stale decision, still counted as a call
        ],
        "text_calls": [{"latency_ms": 400, "usage": {"total_tokens": 20}}],
        "page": {"url": "https://www.naver.com/search?query=weather", "title": "weather - Naver"},
    }
    summary = via.summarize(state)
    assert summary["typesafe_calls"] == 3
    assert summary["text_calls"] == 1
    assert summary["decision_ms_median"] == 150
    assert summary["decision_ms_max"] == 200
    assert summary["text_ms_total"] == 400
    assert summary["tokens"]["typesafe"] == {"input_tokens": 80}
    assert summary["tokens"]["text"] == {"total_tokens": 20}
    assert summary["final_url"] == "https://www.naver.com/search?query=weather"


def test_summarize_handles_no_decisions():
    state = {
        "status": "blocked",
        "elapsed_ms": 0,
        "history": [],
        "decisions": [],
        "text_calls": [],
        "page": {"url": "https://example.test", "title": "Example"},
    }
    summary = via.summarize(state)
    assert summary["decision_ms_median"] is None
    assert summary["decision_ms_max"] is None
    assert summary["tokens"] == {"typesafe": {}, "text": {}}


@pytest.mark.parametrize("status,code", [("done", 0), ("blocked", 2), ("error", 3), ("anything-else", 3)])
def test_exit_code_mapping(status, code):
    assert via.exit_code(status) == code


class FakeAgent:
    """Stands in for jev_ultrafast.agent.Agent: takes states, returns them via run()."""

    instances = []

    def __init__(self, url, goal, states=None, raise_during=None):
        self.url = url
        self.goal = goal
        self._states = states or []
        self._raise_during = raise_during
        self.closed = False
        self.state = self._states[-1] if self._states else {}
        FakeAgent.instances.append(self)

    def run(self):
        for state in self._states:
            self.state = state
            yield state
        if self._raise_during:
            raise self._raise_during

    def snapshot(self):
        return dict(self.state)

    def close(self):
        self.closed = True


def _state(step, status, **overrides):
    base = {
        "status": status,
        "elapsed_ms": step * 500,
        "history": [
            {
                "step": i + 1,
                "operation": "CLICK",
                "action": f"action {i + 1}",
                "text": None,
                "latency_ms": 50,
                "text_latency_ms": 0,
                "probability": 0.9,
            }
            for i in range(step)
        ],
        "decisions": [{"latency_ms": 50, "usage": {}} for _ in range(step)],
        "text_calls": [],
        "page": {"url": "https://example.test/done", "title": "Done Page", "screenshot": "base64stuff"},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_fake_agent():
    FakeAgent.instances.clear()
    yield
    FakeAgent.instances.clear()


def _run_main(monkeypatch, tmp_path, states=None, raise_during=None, extra_args=()):
    def factory(url, goal):
        return FakeAgent(url, goal, states=states, raise_during=raise_during)

    monkeypatch.setattr(via, "Agent", factory)
    argv = ["--trace-dir", str(tmp_path / "trace"), *extra_args, "--", "네이버에서 서울 날씨 검색"]
    code = via.main(argv)
    return code


def test_main_done_run_exits_zero_and_prints_result(monkeypatch, tmp_path, capsys):
    states = [_state(1, "ready"), _state(2, "done")]
    code = _run_main(monkeypatch, tmp_path, states=states)
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert out[-1].startswith("RESULT done ·")
    assert "Done Page" in out[-1]
    assert FakeAgent.instances[0].closed


def test_main_blocked_run_exits_two(monkeypatch, tmp_path, capsys):
    states = [_state(1, "blocked")]
    code = _run_main(monkeypatch, tmp_path, states=states)
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 2
    assert out[-1].startswith("RESULT blocked ·")


def test_main_agent_error_exits_three(monkeypatch, tmp_path, capsys):
    code = _run_main(monkeypatch, tmp_path, states=[_state(1, "ready")], raise_during=RuntimeError("model unavailable"))
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 3
    assert out[-1] == "RESULT error · model unavailable"


def test_main_writes_trace_without_screenshot(monkeypatch, tmp_path):
    states = [_state(1, "done")]
    _run_main(monkeypatch, tmp_path, states=states)
    trace = json.loads((tmp_path / "trace" / "trace.json").read_text())
    assert "screenshot" not in trace["page"]
    assert trace["summary"]["status"] == "done"
    assert trace["goal"] == "네이버에서 서울 날씨 검색"


def test_main_quiet_suppresses_step_lines(monkeypatch, tmp_path, capsys):
    states = [_state(2, "done")]
    _run_main(monkeypatch, tmp_path, states=states, extra_args=["--quiet"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("RESULT done ·")


def test_main_keep_open_does_not_close_the_agent(monkeypatch, tmp_path):
    states = [_state(1, "done")]
    _run_main(monkeypatch, tmp_path, states=states, extra_args=["--keep-open"])
    assert FakeAgent.instances[0].closed is False


def test_main_rejects_an_empty_goal():
    assert via.main(["--", "   "]) == 3
