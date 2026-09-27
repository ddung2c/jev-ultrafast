"""A measured live run with continuous CDP screencast, for the VIA fastweb PoC demo.

Usage: record_via.py OUTPUT_DIR [--start-url URL] -- <goal ...>
"""

import argparse
import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

from browser_harness.helpers import drain_events

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev_ultrafast import Agent, via  # noqa: E402
from scripts.via_bench_lib import verify_naver_weather as _verify_naver_weather  # noqa: E402

DEFAULT_GOAL = "네이버에서 서울 날씨를 검색하고 날씨 결과가 보이면 멈춰."


def verify_naver_weather(page):
    """Independent check on the resulting page; a DONE choice is not proof of success.

    Thin adapter over the shared `via_bench_lib` check (also used by the ARGO
    A/B bench), which takes (url, text) rather than jev's `page` dict.
    """
    return _verify_naver_weather(page["url"], page["text"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-url", default=None)
    parser.add_argument("goal", nargs="*", default=None)
    args = parser.parse_args()
    goal = " ".join(args.goal).strip() if args.goal else DEFAULT_GOAL

    folder = args.output
    folder.mkdir(parents=True, exist_ok=False)
    source_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__).resolve().parents[1] / "jev_ultrafast").iterdir()
        if p.suffix in {".py", ".js"}
    }
    url = via.start_url(goal, args.start_url)
    agent = Agent(url, goal)
    (folder / "frames").mkdir(exist_ok=True)
    (folder / "frames" / "000000.jpg").write_bytes(
        base64.b64decode(agent.browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"])
    )
    frames = folder / "screencast"
    frames.mkdir(exist_ok=True)
    stop = threading.Event()
    epoch = time.time()
    errors = []

    def capture():
        try:
            while not stop.is_set():
                for event in drain_events():
                    if event["method"] != "Page.screencastFrame" or event.get("session_id") != agent.browser.session:
                        continue
                    p = event["params"]
                    timestamp = max(0, round((p["metadata"]["timestamp"] - epoch) * 1000))
                    (frames / f"{timestamp:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                    agent.browser.call("Page.screencastFrameAck", sessionId=p["sessionId"])
                stop.wait(0.015)
        except Exception as e:
            errors.append(str(e))

    agent.browser.call(
        "Page.startScreencast", format="jpeg", quality=80, maxWidth=1120, maxHeight=780, everyNthFrame=2
    )
    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    # The first prediction starts the run timer; this anchors video timestamps to it.
    epoch = time.time()
    seen = 0
    try:
        for state in agent.run():
            history = state["history"]
            for entry in history[seen:]:
                print(via.step_line(entry), flush=True)
            seen = len(history)
    finally:
        time.sleep(0.08)  # Drain the last frame, outside the reported agent time.
        stop.set()
        worker.join(timeout=3)
        agent.browser.call("Page.stopScreencast")
        state = agent.snapshot()
        state["final_page"] = agent.browser.observe(screenshot=False)
        state["verification"] = verify_naver_weather(state["final_page"])
        state["summary"] = via.summarize(state)
        state["source_hashes"] = source_hashes
        state["recording_errors"] = errors
        state["start_url"] = url
        state["goal"] = goal
        page = dict(state.get("page") or {})
        page.pop("screenshot", None)
        state["page"] = page
        (folder / "state.json").write_text(json.dumps(state, indent=2, default=str))
        (folder / "session.json").write_text(
            json.dumps({"target": agent.browser.target, "session": agent.browser.session})
        )
        agent.close()
    print(json.dumps(state["verification"], indent=2))
    print("Screencast frames", len(list(frames.glob("*.jpg"))), "errors", errors)
    if not state["verification"]["passed"]:
        raise SystemExit("Final-page verification failed")


if __name__ == "__main__":
    main()
