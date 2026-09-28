# Reproducing the ARGO-vs-jev VIA A/B bench on Windows

This is a self-contained runbook for a fresh Claude Code instance (or a
human) on Windows to reproduce the ARGO `desktopweb` vs. ARGO+jev A/B
measurement documented in `docs/via-argo-bench-design.md` and
`docs/via-vs-argo.md`. That original run was done on macOS; this document
exists because Windows is also argo-pc's *real* target platform, so a
Windows run is a stronger parity check, not just a repeat.

Read this whole file before running anything. If a step's outcome doesn't
match its "expect" line, stop and report the actual output — do not
substitute recorded/documented figures for a failed live measurement (see
`AGENTS.md` in this repo: "Verify actual final outcomes independently").

## 0. What you're reproducing, in one paragraph

Two arms answer the exact same Korean voice-style hint
(`"네이버에서 서울 날씨 검색해줘"`) through the *same* VIA entry point (a real
`via-core` Evaluator + Router + capability-card roster copied verbatim from
argo-pc's `via_ask.rs::build_orchestrator`). The only difference between
arms is what executes the `desktopweb` lane: Arm A calls real ARGO
(`tinicli`'s desktopweb sub-agent, headless via a bench-only CLI flag,
non-default feature); Arm B calls jev-ultrafast's `jev-via` CLI. Both drive
the same dedicated Chrome profile over CDP. Success is verified
independently by reading the resulting page over CDP — never by trusting
either agent's own "done" claim.

Nothing in ARGO's default build or default behavior changes. The bench
hooks are gated behind a `bench-hooks` Cargo feature that is off unless
explicitly enabled.

## 1. Repos and branches

| Repo | Remote | Branch | What's there |
|---|---|---|---|
| ARGO | `github.ecodesamsung.com/AGENTIC/ARGO` (Enterprise) | `via-fastweb-bench` | `tinicli::bench` headless desktopweb runner (feature `bench-hooks`), `argo-pc/via-bench` (independent crate: real via-core Evaluator/Router + 8-lane capability-card roster) |
| jev-ultrafast | `github.com/ddung2c/jev-ultrafast` (fork) | `feat/via-argo-ab-bench` | `scripts/bench_via_argo.py`, `scripts/via_bench_lib.py`, `scripts/render_via_compare.py`, `jev_ultrafast/via.py` (`jev-via` CLI), `docs/via-argo-bench-design.md` |

Clone both at those branches. jev-ultrafast is a **fork** — clone
`ddung2c/jev-ultrafast`, not `browser-use/jev-ultrafast` upstream, so pushes
(if any) land in the right place. Do not push to `browser-use/jev-ultrafast`
directly; do not commit or push anything at all unless the user explicitly
asks.

```powershell
git clone --branch via-fastweb-bench https://github.ecodesamsung.com/AGENTIC/ARGO.git
git clone --branch feat/via-argo-ab-bench https://github.com/ddung2c/jev-ultrafast.git
```

ARGO's Enterprise remote needs `github.ecodesamsung.com` auth (VPN/SSO as
usual for that host) — this is separate from your github.com credentials.

## 2. Prerequisites

Install, in this order, and confirm the version command works before moving
on:

| Tool | Check | Notes |
|---|---|---|
| Rust (via rustup) | `cargo --version` | Use the toolchain pinned in `ARGO/rust-toolchain.toml` (currently `1.94`). `rustup show` inside the ARGO checkout installs it. |
| Python 3.12+ | `python --version` | |
| `uv` | `uv --version` | `pip install uv` or the official Windows installer. jev-ultrafast is a `uv`-managed project. |
| Google Chrome | — | Default install path is auto-detected; see §4 if yours is elsewhere. |
| `ffmpeg` | `ffmpeg -version` | Only needed if you render the comparison video (§7). A static Windows build (gyan.dev or similar) on `PATH` is fine. |
| Visual Studio Build Tools (C++) | — | Needed for some Rust native deps (objectbox, etc.) on MSVC targets. Install the "Desktop development with C++" workload if `cargo build` fails on a linker error. |

LLM credentials: you need an OpenAI-compatible API key for **both** roles —
the VIA Evaluator and ARGO's `desktopweb` sub-agent share one set of
credentials in this bench (see `docs/via-argo-bench-design.md` §1). The
original run used DeepSeek (`deepseek-chat`, `https://api.deepseek.com`).
Whatever model you use, it must be recorded in your results — do not run
with an unrecorded/default model, since ARGO's numbers are model-sensitive.

jev-ultrafast also needs its own `.env` (copy `.env.example` →
`.env`, fill in `TYPESAFE_API_KEY` and `TEXT_MODEL_API_KEY`). Never commit
`.env`; it's gitignored.

## 3. Build

### 3.1 ARGO

From the ARGO checkout root:

```powershell
cargo build -p argo-cli --release --features bench-hooks
```

This builds `target\release\argo.exe` with the bench-only
`--desktopweb-bench` flag compiled in (feature-gated, off in a normal
`cargo build -p argo-cli --release`). If this fails on a native-dependency
linker error (objectbox is the likely culprit — it was the one macOS hit,
see `artifacts/via_bench/phase0.md` in jev-ultrafast for that writeup),
capture the full error and report it rather than working around it by
disabling default features silently — the goal is a build that matches
argo-pc's own desktopweb code path as closely as possible.

Then build the independent VIA bench harness:

```powershell
cd argo-pc\via-bench
cargo build --release
```

This is its own Cargo workspace (not a member of ARGO's root workspace), so
it must be built from inside `argo-pc\via-bench`. It depends only on
`via-core`/`via-engine`/`via-orchestrator`/`via-agents` (the isolated `via/`
workspace) — never on `tinicore`/`argo-*` crates directly. It calls the
ARGO arm as a **child process** (the `argo.exe` built above), so both arms
are symmetric (both are subprocess executors from `via-bench`'s point of
view).

Expect: `target\release\via-bench.exe` exists in `argo-pc\via-bench`.

### 3.2 jev-ultrafast

From the jev-ultrafast checkout root:

```powershell
uv sync
uv run pytest -q
uv run ruff check .
```

Expect: all tests pass, ruff reports no errors. If either fails on Windows
in a way that looks platform-specific (path separators, encoding), that's
new information worth reporting — the scripts were made cross-platform for
this runbook, but hadn't been run on real Windows yet as of this writing.

## 4. Windows-specific gotchas

These are things known (from the macOS run) or anticipated to differ on
Windows. Read before running Phase 0.

- **Chrome path.** `scripts/bench_via_argo.py`'s `default_chrome_path()`
  checks `%ProgramFiles%\Google\Chrome\Application\chrome.exe` and the
  `(x86)`/`%LocalAppData%` variants. If your Chrome is somewhere else, pass
  `--chrome-bin "C:\path\to\chrome.exe"` explicitly.
- **Native library search path.** On macOS, ARGO's built `argo` binary
  needed `DYLD_LIBRARY_PATH` pointed at objectbox's build output directory
  because the rpath didn't propagate to the final bin link (see
  `artifacts/via_bench/phase0.md`). The Windows equivalent, if you hit the
  same class of problem, is prepending that directory to `PATH` (Windows
  resolves DLL search via `PATH`, not an rpath-like mechanism) —
  `scripts/bench_via_argo.py`'s `objectbox_env_var()` already returns
  `"PATH"` on Windows and the caller prepends rather than replaces it. In
  practice, Windows DLL loading conventions often make this unnecessary
  (MSVC typically places dependent DLLs next to the `.exe` or on `PATH`
  already) — treat this as a "if G3-equivalent build/run fails with a
  missing-DLL error" fallback, not a required step.
- **Screen recording.** `scripts/bench_via_argo.py` uses `gdigrab` (ffmpeg's
  Windows desktop-capture input) instead of macOS's `avfoundation`. No
  device-index selection is needed on Windows (`--screen-index` is ignored
  outside macOS) — `gdigrab` with `-i desktop` captures the whole desktop.
  No special OS permission prompt is expected (unlike macOS Screen
  Recording permission), but Windows may still show a one-time "Chrome
  wants to record your screen" style prompt for *other* apps — not
  applicable here since ffmpeg's gdigrab doesn't go through that API.
- **Fonts, if rendering the comparison video (§7).** `render_via_compare.py`
  now looks for Malgun Gothic (`%WINDIR%\Fonts\malgun.ttf` /
  `malgunbd.ttf`) for Korean text and Consolas
  (`%WINDIR%\Fonts\consola.ttf`) for the monospace HUD text. Both ship with
  Windows by default; if missing, the script exits with a clear error
  naming which one and where it looked.
- **Path quoting.** All example commands below use PowerShell. If a path
  has spaces (e.g. `Program Files`), keep it quoted exactly as shown.
- **Line endings / `git`.** Nothing in this repro depends on line endings,
  but if `git` on your machine auto-converts LF→CRLF on checkout, the
  Rust/Python files still work fine either way — noted only in case a diff
  looks noisier than expected.

## 5. Phase 0 — feasibility gate (do this before anything else)

Mirrors `docs/via-argo-bench-design.md` §2, adapted for Windows. Record
each gate's command, a summary of its output, and elapsed time in a new
`artifacts/via_bench/phase0-windows.md` in the jev-ultrafast checkout (don't
overwrite the macOS `phase0.md` — it's a record of that run). If any gate
fails, **stop and report the exact error** rather than guessing around it
or substituting the macOS-recorded numbers.

| Gate | Do | Passes when |
|---|---|---|
| G1 Rust | `cargo --version` inside ARGO checkout (rustup auto-installs the pinned toolchain from `rust-toolchain.toml`) | Prints a `1.94.x` rustc version |
| G2 Build | §3.1 above | `argo.exe` and `via-bench.exe` both exist |
| G3 VIA turn | From `ARGO\via`: `cargo run --example run_one_turn -p via-orchestrator -- "네이버에서 서울 날씨 검색해줘"` (with your LLM env vars set — see §1 of the design doc for the var names: `OPENAI_API_KEY`/`AZURE_OPENAI_API_KEY`, `OPENAI_ENDPOINT`, `OPENAI_MODEL`, `VIA_LLM_API_STYLE=openai` for a non-Azure OpenAI-compatible endpoint) | Prints `[dispatched]` |
| G4 ARGO desktopweb (headless) | Launch a dedicated Chrome (§6.1), then: `$env:ARGO_BROWSER_ATTACH="http://127.0.0.1:9333"; $env:ARGO_BROWSER_NO_AUTOLAUNCH="1"; .\target\release\argo.exe --desktopweb-bench "네이버에서 서울 날씨 검색해줘"` | Exit code 0, Chrome shows Naver weather results |
| G5 jev-via | `$env:BU_NAME="viabench"; uv run --env-file .env jev-via -- "네이버에서 서울 날씨 검색해줘"` from the jev-ultrafast checkout, pointed at the same Chrome via `BU_CDP_WS` | Exit 0, new tab in the same Chrome |
| G6 screen recording | `ffmpeg -f gdigrab -framerate 30 -i desktop -t 5 -y test.mp4` | `test.mp4` plays and shows your desktop |

## 6. Running the A/B bench

### 6.1 Dedicated Chrome profile

Both arms share one dedicated, disposable Chrome profile — never your
everyday Chrome — so neither arm gets a login/cookie advantage and your own
browsing isn't disturbed. `scripts/bench_via_argo.py` launches this for you
per run; you don't need to do it by hand except for the Phase 0 gates
above, where you can use:

```powershell
& $env:ProgramFiles"\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9333 --user-data-dir="$env:USERPROFILE\.argo-browser-bench" `
  --no-first-run --no-default-browser-check `
  --window-position=0,40 --window-size=1280,860 about:blank
```

Port `9333`, not `9222` — `9222` is a common default that your regular
Chrome or other tools may already be listening on, which silently causes a
second instance to bind in a way that's invisible to naive
`127.0.0.1`-based probing (this is exactly what happened on macOS; see
`bench_via_argo.py`'s `CDP_PORT` comment). `9333` is also ARGO's own
documented fallback CDP probe port, so ARGO finds it without extra config.

### 6.2 Run it

From the jev-ultrafast checkout:

```powershell
uv run python scripts/bench_via_argo.py `
  --argo-bin "..\ARGO\target\release\argo.exe" `
  --via-bench-bin "..\ARGO\argo-pc\via-bench\target\release\via-bench.exe" `
  --uv-bin uv `
  --runs 3 `
  --llm-api-key <your key> `
  --llm-endpoint https://api.deepseek.com `
  --llm-model deepseek-chat
```

Adjust `--llm-endpoint`/`--llm-model` to whatever provider you're actually
using — they're passed through to both the VIA Evaluator and ARGO's
desktopweb arm, matching the design doc's requirement that both share one
LLM configuration. `--chrome-bin` and `--screen-index` have platform-aware
defaults (§4) and rarely need overriding on Windows.

This runs one untimed warmup per arm, then 3 measured runs per arm,
interleaved A/B/A/B/A/B (see `docs/via-argo-bench-design.md` §5.4 for why:
it spreads network/cache bias evenly rather than confounding it with arm
order). Output lands in
`artifacts\via_bench\<timestamp>\` — a `run.json` per run plus a
`summary.json` with medians, min/max, and independently-verified success
counts. A run screen-records to `screen.mp4` in its own directory (skip
recording by not having `ffmpeg` on `PATH`, if you only want the numbers).

**Do not shorten the process to look at intermediate DONE claims and skip
independent verification** — `bench_via_argo.py` already does the
verification step (reads the resulting Naver page over CDP after each run,
via `verify_naver_weather()` in `scripts/via_bench_lib.py`), but if you
build any variant of this by hand, replicate that check rather than trusting
either agent's own success claim. This project's `AGENTS.md` states the
principle directly: "A DONE choice is not proof of success."

### 6.3 Reading the result

```powershell
Get-Content artifacts\via_bench\<timestamp>\summary.json | ConvertFrom-Json
```

Compare against the macOS baseline in `docs/via-vs-argo.md` (median 69.6s
ARGO vs. 11.0s jev, both 3/3 verified) — a Windows run is expected to differ
in absolute numbers (different machine, possibly different LLM latency to
your chosen endpoint) but should show the same *qualitative* shape: ARGO's
desktopweb lane pays for step-by-step DSL generation with a large
completion-gate verification turn on top, while jev's selection-based
`TypeSafe` approach collapses that into fewer, smaller model calls. If your
Windows numbers show the opposite pattern, that's a real, reportable
finding — not something to adjust to match expectations.

## 7. Optional: render the side-by-side comparison video

```powershell
uv run python scripts/render_via_compare.py artifacts\via_bench\<timestamp>
```

Produces `docs\via-vs-argo.mp4`/`.gif`/`.png` (representative — median —
run from each arm, synchronized to the same T0, original speed, no edited
cuts — see `docs/via-argo-bench-design.md` §6 for the exact rendering
contract, including why the video must not be sped up or trimmed during a
slow arm's wait). Requires the fonts named in §4. If you don't need video,
skip this section entirely — the numeric result in `summary.json` and
`docs/via-vs-argo.md`-style tables are the primary artifact.

## 8. What this does *not* cover

- **Wiring jev into the real argo-pc Windows app.** This bench's `via-bench`
  crate is a standalone stand-in for argo-pc's VIA host — it reimplements
  the Evaluator/Router/capability-card wiring from `via_ask.rs` but is not
  argo-pc itself, and does not touch `argo-pc/rust-backend/src/via_ask.rs`.
  Registering jev as `desktopweb`'s real executor inside argo-pc (with a
  fallback to ARGO's own desktopweb on jev failure) is a designed-but-
  unimplemented follow-up — see `docs/plans/FASTWEB_JEV_FUSION.md` §5.3/§6
  in the ARGO repo, and `docs/argo-vs-jev-fusion-architecture.html` in this
  repo for the architecture diagram of that follow-up.
- **Voice input.** Both arms are driven by a stub `VoiceEngine` that injects
  a text hint directly (matching how argo-pc's own PTT path also collapses
  to `submit_hint(app, hint)` after STT — see design doc §1). No
  microphone/STT is exercised here.
- **Committing or pushing anything.** Do that only if the user explicitly
  asks, and to the fork (`ddung2c/jev-ultrafast`) for jev-ultrafast changes,
  never directly to `browser-use/jev-ultrafast` upstream.
