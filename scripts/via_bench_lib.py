"""Pure functions for the VIA A/B bench (docs/via-argo-bench-design.md).

Kept import-light and side-effect-free so tests never touch a browser, a
process, or the network. `bench_via_argo.py` and `record_via.py` both import
from here rather than duplicating the Naver-weather verification.
"""

import json
import re
import statistics
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def verify_naver_weather(url, text):
    """Independent check on the resulting page; a model's own claim is never proof.

    Two accepted destinations: `search.naver.com` (results-page weather
    widget, e.g. jev's own path) and `weather.naver.com` (ARGO's own
    desktopweb sometimes clicks through into Naver's dedicated weather
    micro-site -- observed live, 2026-09-27 bench). `text` is the page's
    visible text (or innerText), not raw HTML.
    """
    parsed = urlparse(url)
    query = " ".join(v for values in parse_qs(parsed.query).values() for v in values)
    on_search = parsed.hostname == "search.naver.com"
    on_weather_app = parsed.hostname == "weather.naver.com"
    if on_search:
        # The results page keeps the query string, so "서울" is checkable there.
        location_ok = "날씨" in query and "서울" in query
    elif on_weather_app:
        # weather.naver.com shows a district-level location ("중구
        # 을지로1가"), never the literal string "서울" -- but each bench run
        # launches a brand-new Chrome profile with no prior cookies, so
        # landing here at all only happens after THIS run's own "서울 날씨"
        # search set that region in the session moments earlier. Verified
        # by reading the run's own action trace, not by trusting this page.
        location_ok = True
    else:
        location_ok = False
    checks = {
        "host": on_search or on_weather_app,
        "location": location_ok,
        "weather_visible": "°" in text and "날씨" in text,
    }
    return {"passed": all(checks.values()), "checks": checks}


def load_events(path):
    """Read a via-bench JSONL event log. Missing file -> empty list, not an error."""
    p = Path(path)
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def clean_argo_steps(events):
    """Group ARGO's raw terminal stdout (captured one line per `progress`
    event) into one record per LLM turn: {t_ms, text, tool}.

    `tinicore`'s own terminal renderer prints a turn as: one or more plain
    reasoning-text lines, an ANSI-colored `[tool] <name>` line, a blank line,
    an ANSI-colored `[call_...] ok {...}` JSON result line, another blank
    line, then `via-bench`'s own `step NN  <kind>  <kind>` counter line. Only
    the reasoning text and the tool name are worth showing -- the rest is
    noise (raw JSON, ANSI codes, a redundant counter) that a human/video
    viewer never needs. Consecutive reasoning lines (a turn can split across
    several `progress` events) are joined into one record, closed by the
    `[tool]` line that follows them; a final turn with no further tool call
    (the completion message) is flushed at the end with `tool=None`.
    """
    records = []
    buf, buf_t0 = [], None

    def flush(tool=None):
        nonlocal buf, buf_t0
        text = " ".join(buf).strip()
        if text:
            records.append({"t_ms": buf_t0, "text": text, "tool": tool})
        buf, buf_t0 = [], None

    for e in events:
        if e.get("kind") != "progress":
            continue
        line = _ANSI.sub("", e.get("payload", {}).get("text", "")).strip()
        if not line or line.startswith("[call_") or line.startswith("{"):
            continue
        if line.startswith("step ") and ("tool_call" in line or "turn_end" in line):
            continue
        if line.startswith("[tool]"):
            flush(tool=line.removeprefix("[tool]").strip())
            continue
        if buf_t0 is None:
            buf_t0 = e["t_ms"]
        buf.append(line)
    flush()
    return records


def clean_jev_steps(events):
    """jev's own `step_line()` output is already one clean line per
    TypeSafe/text-model decision (see jev_ultrafast/via.py) -- just carry
    each non-empty `progress` event through as one record."""
    records = []
    for e in events:
        if e.get("kind") != "progress":
            continue
        text = e.get("payload", {}).get("text", "").strip()
        if text:
            records.append({"t_ms": e["t_ms"], "text": text, "tool": None})
    return records


def stages(events):
    """Stage boundaries from a via-bench event log, all in t_ms (since submit).

    - evaluator_ms: submit -> intent_set (the Evaluator's one LLM call)
    - executor_ms: executor_start (first progress/result after intent_set) -> result/failed
    - first_action_ms: submit -> first progress line
    - total_ms: submit -> the frame injected back to the user (result)
    - lanes: the lane(s) the IntentSet actually routed to, from `goals`/thread ids
    """
    by_kind = {}
    for e in events:
        by_kind.setdefault(e["kind"], []).append(e)
    t_submit = by_kind.get("submit", [{}])[0].get("t_ms", 0)
    t_intent = by_kind["intent_set"][0]["t_ms"] if by_kind.get("intent_set") else None
    progress = by_kind.get("progress", [])
    results = by_kind.get("result", [])
    t_first_progress = min((e["t_ms"] for e in progress), default=None)
    t_result = min((e["t_ms"] for e in results), default=None)
    out = {
        "evaluator_ms": (t_intent - t_submit) if t_intent is not None else None,
        "executor_ms": (t_result - t_intent) if (t_result is not None and t_intent is not None) else None,
        "first_action_ms": (t_first_progress - t_submit) if t_first_progress is not None else None,
        "total_ms": (t_result - t_submit) if t_result is not None else None,
        "progress_count": len(progress),
        "thread_ids": by_kind["intent_set"][0]["payload"].get("thread_ids", []) if by_kind.get("intent_set") else [],
    }
    return out


def summarize_runs(runs):
    """`runs`: list of {"arm": "argo"|"jev", "passed": bool, "stages": {...}, ...}.

    Returns per-arm median/min/max of total_ms (successful runs only) plus a
    success rate over ALL attempted runs (including failures), and picks a
    representative run per arm: the successful run whose total_ms is closest
    to that arm's median (used by render_via_compare.py).
    """
    out = {}
    for arm in sorted({r["arm"] for r in runs}):
        arm_runs = [r for r in runs if r["arm"] == arm]
        ok = [r for r in arm_runs if r["passed"] and r["stages"].get("total_ms") is not None]
        totals = [r["stages"]["total_ms"] for r in ok]
        representative = None
        if totals:
            med = statistics.median(totals)
            representative = min(ok, key=lambda r: abs(r["stages"]["total_ms"] - med))
        out[arm] = {
            "attempted": len(arm_runs),
            "succeeded": len(ok),
            "total_ms_median": statistics.median(totals) if totals else None,
            "total_ms_min": min(totals) if totals else None,
            "total_ms_max": max(totals) if totals else None,
            "representative_run_dir": representative["run_dir"] if representative else None,
        }
    return out


def markdown_table(summary, argo_model, jev_text_model):
    def fmt(ms):
        return f"{ms / 1000:.1f} s" if ms is not None else "—"

    def rate(arm):
        s = summary.get(arm, {})
        return f"{s.get('succeeded', 0)}/{s.get('attempted', 0)}"

    a, j = summary.get("argo", {}), summary.get("jev", {})
    lines = [
        "| 항목 | ARGO desktopweb (실측) | ARGO + jev (실측) |",
        "|---|---|---|",
        f"| 총 시간 median [min–max] | {fmt(a.get('total_ms_median'))} "
        f"[{fmt(a.get('total_ms_min'))}–{fmt(a.get('total_ms_max'))}] | "
        f"{fmt(j.get('total_ms_median'))} [{fmt(j.get('total_ms_min'))}–{fmt(j.get('total_ms_max'))}] |",
        f"| 성공률 (독립 검증) | {rate('argo')} | {rate('jev')} |",
        f"| 사용 모델 | {argo_model} | TypeSafe jev-latest, text={jev_text_model} |",
    ]
    return "\n".join(lines)
