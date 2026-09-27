"""VIA A/B bench runner: real ARGO desktopweb vs jev-ultrafast, same VIA start point.

See docs/via-argo-bench-design.md §5. Drives a dedicated Chrome profile over
CDP, launches `via-bench --executor <arm>` (the real via-core Evaluator +
Router; only the desktopweb lane's executor differs between arms), records
the screen with ffmpeg, and verifies the result independently -- never on
the model's own claim.

Usage:
  uv run python scripts/bench_via_argo.py --argo-bin ~/workspaces/ARGO/target/release/argo \
      --via-bench-bin ~/workspaces/ARGO/target/debug/via-bench --runs 3
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.via_bench_lib import stages, summarize_runs, verify_naver_weather  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOAL = "네이버에서 서울 날씨 검색해줘"
# NOT 9222: the user's regular, long-lived Chrome already listens there on
# this machine (see docs/via-argo-bench-design.md phase0.md), so a second
# Chrome on 9222 silently falls back to an IPv6-only bind that
# 127.0.0.1-based probing never reaches. 9333 is ARGO's own documented
# fallback probe port (tinicli/src/adapters/desktop_browser_bridge.rs), so
# ARGO would find this Chrome unprompted even without ARGO_BROWSER_ATTACH.
CDP_PORT = 9333
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def cdp_get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as r:
        return json.loads(r.read())


def cdp_post(path):
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}{path}", method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def cdp_eval(target_id, expression):
    """One `Runtime.evaluate` over the page's dedicated debugger websocket."""
    from websockets.sync.client import connect  # lazy: only needed here, not at collection time for tests

    info = cdp_get("/json/list")
    target = next(t for t in info if t["id"] == target_id)
    with connect(target["webSocketDebuggerUrl"], open_timeout=10) as ws:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression}}))
        result = json.loads(ws.recv(timeout=10))
    return result.get("result", {}).get("result", {}).get("value")


def launch_chrome(profile_dir):
    profile_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            CHROME,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-position=0,40",
            "--window-size=1280,860",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            cdp_get("/json/version")
            return proc
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("Chrome did not open a CDP port in time")


def reset_tabs():
    """Close every page target, leave one about:blank -- same starting point every run."""
    for t in cdp_get("/json/list"):
        if t.get("type") == "page":
            cdp_post(f"/json/close/{t['id']}")
    time.sleep(0.3)
    # PUT, not GET: recent Chrome (153+) rejects a GET to /json/new as an
    # "unsafe HTTP verb" (observed live, 2026-09-27).
    req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/new?about:blank", method="PUT")
    new_tab = json.loads(urllib.request.urlopen(req, timeout=5).read())
    return new_tab["id"]


def start_screen_recording(out_path, screen_index):
    return subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "avfoundation",
            "-framerate",
            "30",
            "-capture_cursor",
            "1",
            "-i",
            f"{screen_index}:none",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def objectbox_lib_dir(argo_bin):
    """macOS-only workaround: the `argo` binary's objectbox rpath does not
    reach the final bin link (see docs/via-argo-bench-design.md phase0.md
    G3), so `dyld` cannot find libobjectbox.dylib unless DYLD_LIBRARY_PATH
    points at it directly. Discovered by glob, not a hardcoded hash, since
    Cargo's build-script output directory name changes across rebuilds.
    """
    repo_root = Path(argo_bin).resolve().parents[2]  # .../target/release/argo -> repo root
    matches = glob.glob(str(repo_root / "target" / "release" / "build" / "tinicore-*" / "out" / "objectbox-*" / "lib"))
    return matches[0] if matches else None


def stop_screen_recording(proc):
    try:
        proc.communicate(input=b"q", timeout=10)
    except Exception:
        proc.kill()


def find_result_tab():
    """The page target on search.naver.com or weather.naver.com, if any
    (either arm may open several tabs; ARGO's desktopweb sometimes clicks
    through into the dedicated weather micro-site -- see verify_naver_weather
    in via_bench_lib.py for why that host is also accepted)."""
    for t in cdp_get("/json/list"):
        url = t.get("url") or ""
        if t.get("type") == "page" and ("search.naver.com" in url or "weather.naver.com" in url):
            return t
    return None


class ForegroundWatcher:
    """Polls /json/list and activates any new, non-blank page tab.

    Both arms may open tabs in the background (jev's Agent always does --
    `Target.createTarget(..., background=True)` -- and ARGO can too), so
    without this the recorded video shows an idle about:blank window the
    whole run (observed live, 2026-09-27 smoke test). Applied identically to
    both arms; see docs/via-argo-bench-design.md §5.2.
    """

    def __init__(self, interval_s=0.1):
        self.interval_s = interval_s
        self._stop = threading.Event()
        # A tab is created blank and navigated a moment later (both arms'
        # browsers do this) -- so this tracks "already activated", not
        # "already seen", and every not-yet-activated page tab is
        # re-checked on every poll until its URL leaves about:blank.
        self._activated = set()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            try:
                for t in cdp_get("/json/list"):
                    if t.get("type") != "page" or t["id"] in self._activated:
                        continue
                    if (t.get("url") or "about:blank") != "about:blank":
                        cdp_post(f"/json/activate/{t['id']}")
                        self._activated.add(t["id"])
            except Exception:
                pass
            self._stop.wait(self.interval_s)


def run_one(arm, args, run_dir):
    run_dir.mkdir(parents=True, exist_ok=False)
    profile_dir = run_dir / "chrome-profile"
    chrome = launch_chrome(profile_dir)
    try:
        reset_tabs()
        time.sleep(1.0)
        recording = start_screen_recording(run_dir / "screen.mp4", args.screen_index)
        time.sleep(0.3)
        watcher = ForegroundWatcher()
        watcher.start()

        env = {**os.environ}
        # VIA's own Evaluator (via-bench's AzureOpenAiClient::from_env) reads
        # these every run, regardless of arm. ARGO_PROVIDER only matters on
        # the argo arm, but is harmless to set either way.
        env["VIA_LLM_API_STYLE"] = "openai"
        env["OPENAI_API_KEY"] = args.llm_api_key
        env["OPENAI_ENDPOINT"] = args.llm_endpoint
        env["OPENAI_MODEL"] = args.llm_model
        env["ARGO_PROVIDER"] = "openai"
        if arm == "argo":
            env["ARGO_BROWSER_ATTACH"] = f"http://127.0.0.1:{CDP_PORT}"
            env["ARGO_BROWSER_NO_AUTOLAUNCH"] = "1"
            lib_dir = objectbox_lib_dir(args.argo_bin)
            if lib_dir:
                env["DYLD_LIBRARY_PATH"] = lib_dir
        else:
            env["BU_NAME"] = "viabench"
            env["BU_CDP_WS"] = cdp_get("/json/version")["webSocketDebuggerUrl"]

        cmd = [
            args.via_bench_bin,
            "--executor",
            arm,
            "--hint",
            GOAL,
            "--events",
            str(run_dir / "events.jsonl"),
            "--argo-bin",
            args.argo_bin,
            "--jev-dir",
            str(ROOT),
            "--uv-bin",
            args.uv_bin,
            "--timeout-s",
            str(args.timeout_s),
        ]
        log_path = run_dir / "via-bench.log"
        with open(log_path, "w") as log:
            proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout_s + 30)
        exit_code = proc.returncode

        time.sleep(1.5)
        watcher.stop()
        stop_screen_recording(recording)

        result_tab = find_result_tab()
        if result_tab:
            text = cdp_eval(result_tab["id"], "document.body.innerText") or ""
            verification = verify_naver_weather(result_tab["url"], text)
        else:
            verification = {"passed": False, "checks": {"host": False, "location": False, "weather_visible": False}}

        from scripts.via_bench_lib import load_events

        run_stages = stages(load_events(run_dir / "events.jsonl"))
        record = {
            "arm": arm,
            "exit_code": exit_code,
            "passed": verification["passed"] and exit_code == 0,
            "verification": verification,
            "stages": run_stages,
            "run_dir": str(run_dir),
            "goal": GOAL,
            "llm_model": args.llm_model,
            "llm_endpoint": args.llm_endpoint,
        }
        (run_dir / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
        return record
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except Exception:
            chrome.kill()


def _default_deepseek_key():
    """Read jev-ultrafast's own .env so the ARGO arm and the VIA Evaluator can
    share credentials with jev's TYPE_TEXT helper by default -- override with
    --llm-api-key for a different provider on either role."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text().splitlines():
        if line.startswith("TEXT_MODEL_API_KEY="):
            return line.split("=", 1)[1].strip()
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--via-bench-bin", required=True)
    parser.add_argument("--argo-bin", required=True)
    parser.add_argument("--uv-bin", default=str(Path.home() / "Library/Python/3.12/bin/uv"))
    parser.add_argument("--runs", type=int, default=3, help="measured runs per arm (a warmup run precedes each arm)")
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--screen-index", default="1", help="avfoundation screen device index (see --list-devices)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument(
        "--llm-api-key",
        default=_default_deepseek_key(),
        help="Used for BOTH the VIA Evaluator and the ARGO desktopweb arm (OpenAI-compatible).",
    )
    parser.add_argument("--llm-endpoint", default="https://api.deepseek.com")
    parser.add_argument("--llm-model", default="deepseek-chat")
    args = parser.parse_args()
    if not args.llm_api_key:
        raise SystemExit("No LLM API key: pass --llm-api-key or set TEXT_MODEL_API_KEY in jev-ultrafast/.env")

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg not found")
    if not Path(args.via_bench_bin).exists():
        raise SystemExit(f"via-bench binary not found: {args.via_bench_bin}")
    if not Path(args.argo_bin).exists():
        raise SystemExit(f"argo binary not found: {args.argo_bin}")

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "artifacts" / "via_bench" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    # A B A B A B ... interleaved to spread network/cache bias evenly, with one
    # untimed warmup per arm first (design doc §5.4).
    for arm in ("argo", "jev"):
        print(f"--- warmup: {arm} ---", flush=True)
        run_one(arm, args, out_dir / f"{arm}-warmup")
    for n in range(1, args.runs + 1):
        for arm in ("argo", "jev"):
            print(f"--- {arm} run {n}/{args.runs} ---", flush=True)
            record = run_one(arm, args, out_dir / f"{arm}-{n}")
            print(json.dumps(record, indent=2, ensure_ascii=False), flush=True)
            runs.append(record)

    summary = summarize_runs(runs)
    (out_dir / "summary.json").write_text(json.dumps({"runs": runs, "summary": summary}, indent=2, ensure_ascii=False))
    print("=== summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWritten to {out_dir}")


if __name__ == "__main__":
    main()
