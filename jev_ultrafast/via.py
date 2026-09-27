"""A VIA-compatible CLI entry point: `program [args] -- <goal>`.

Stdout lines are progress; the last line is the spoken result. See
`docs/via-fastweb-design.md` for the contract this implements
(`via/via-agents/src/cli.rs` in ARGO: one stdout line per Progress, the
accumulated output is the Result, a non-zero exit is Failed).
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from .agent import Agent
from .demo import start_url as _normalize_url

# Deciding the starting page is not policy: jev has no navigate operation, so
# something has to open the first tab. Everything after that is jev's choice.
SITES = {
    "네이버": "https://www.naver.com",
    "naver": "https://www.naver.com",
    "구글": "https://www.google.com",
    "google": "https://www.google.com",
    "쿠팡": "https://www.coupang.com",
    "coupang": "https://www.coupang.com",
    "다음": "https://www.daum.net",
    "daum": "https://www.daum.net",
    "유튜브": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "위키": "https://en.wikipedia.org",
    "wikipedia": "https://en.wikipedia.org",
}
DEFAULT_URL = "https://www.google.com"


def start_url(goal, explicit=None):
    """Explicit URL wins; otherwise the first site named in the goal; otherwise Google."""
    if explicit:
        return _normalize_url(explicit)
    lowered = goal.lower()
    for alias, url in SITES.items():
        if alias.lower() in lowered:
            return url
    return DEFAULT_URL


def step_line(entry):
    label = entry["action"].replace("\n", " ").strip()[:40]
    line = f"step {entry['step']:02d}  {entry['operation']}  {label}"
    if entry.get("text"):
        line += f'  "{entry["text"]}"'
    line += f"  ·  decision {entry['latency_ms']} ms"
    if entry.get("text_latency_ms"):
        line += f"  ·  text {entry['text_latency_ms']} ms"
    line += f"  ·  p {entry['probability']:.2f}"
    return line


def _sum_numeric_usage(records):
    totals = {}
    for record in records:
        for key, value in (record.get("usage") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals


def summarize(state):
    """One run's numbers, for the trace file and for reporting against ARGO's recorded figures."""
    decisions = state["decisions"]
    text_calls = state["text_calls"]
    latencies = [d["latency_ms"] for d in decisions]
    page = state.get("page") or {}
    return {
        "status": state["status"],
        "total_ms": state["elapsed_ms"],
        "actions": len(state["history"]),
        "typesafe_calls": len(decisions),
        "text_calls": len(text_calls),
        "decision_ms_median": round(statistics.median(latencies)) if latencies else None,
        "decision_ms_max": max(latencies) if latencies else None,
        "text_ms_total": sum(c["latency_ms"] for c in text_calls),
        "tokens": {"typesafe": _sum_numeric_usage(decisions), "text": _sum_numeric_usage(text_calls)},
        "final_url": page.get("url"),
        "final_title": page.get("title"),
    }


def exit_code(status):
    return {"done": 0, "blocked": 2}.get(status, 3)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jev-via",
        description="Run one goal as a VIA-compatible CLI agent: program [args] -- <goal>.",
    )
    parser.add_argument("--start-url", default=None, help="Override the page jev starts from.")
    parser.add_argument(
        "--trace-dir", default=None, help="Where to write trace.json (default artifacts/via_runs/<ts>)."
    )
    parser.add_argument("--quiet", action="store_true", help="Print only the final RESULT line.")
    parser.add_argument("--keep-open", action="store_true", help="Do not close the browser tab on exit.")
    parser.add_argument("goal", nargs="+", help="The goal, as the words after --.")
    args = parser.parse_args(argv)

    goal = " ".join(args.goal).strip()
    if not goal:
        print("RESULT error · Supply a goal after --", flush=True)
        return 3
    try:
        url = start_url(goal, args.start_url)
    except ValueError as error:
        print(f"RESULT error · {error}", flush=True)
        return 3

    trace_dir = Path(args.trace_dir) if args.trace_dir else Path("artifacts/via_runs") / time.strftime("%Y%m%d-%H%M%S")

    agent = None
    status, message, summary = "error", "", {}
    seen = 0
    try:
        agent = Agent(url, goal)
        for state in agent.run():
            history = state["history"]
            if not args.quiet:
                for entry in history[seen:]:
                    print(step_line(entry), flush=True)
            seen = len(history)
        status = state["status"]
    except (ValueError, RuntimeError) as error:
        message = str(error)
    finally:
        if agent is not None:
            snapshot = None
            try:
                snapshot = agent.snapshot()
                summary = summarize(snapshot)
            except Exception:
                snapshot = None
            if not args.keep_open:
                agent.close()
            if snapshot is not None:
                trace_dir.mkdir(parents=True, exist_ok=True)
                page = dict(snapshot.get("page") or {})
                page.pop("screenshot", None)
                snapshot["page"] = page
                snapshot["summary"] = summary
                snapshot["start_url"] = url
                snapshot["goal"] = goal
                (trace_dir / "trace.json").write_text(json.dumps(snapshot, indent=2, default=str))

    if status == "done":
        result = (
            f"RESULT done · {summary.get('total_ms', 0) / 1000:.1f}s · "
            f"{summary.get('actions', 0)} actions · {summary.get('final_title') or ''}"
        )
    elif status == "blocked":
        history = agent.state["history"] if agent else []
        last_action = history[-1]["action"] if history else "no supported action"
        result = f"RESULT blocked · {last_action}"
    else:
        result = f"RESULT error · {message or 'unknown failure'}"
    print(result, flush=True)
    return exit_code(status)


if __name__ == "__main__":
    raise SystemExit(main())
