# VIA 실측 A/B 벤치 — ARGO desktopweb vs ARGO+jev (macOS) 구현 설계서

> 구현자(Sonnet)용 문서다. 이 문서만 보고 구현할 수 있도록 확인된 사실과 파일 위치를 적었다.
> 먼저 읽을 것: `jev-ultrafast/AGENTS.md`, `jev-ultrafast/docs/via-fastweb-design.md`(이전 PoC), `ARGO/docs/plans/FASTWEB_JEV_FUSION.md`.
> 이 문서는 이전 PoC 영상(`docs/via-demo.mp4`, ARGO **기록값**과 비교)을 **대체**한다.

## 0. 목표

사용자 요구 (원문 요지):
- 문서 기록값이 아니라 **ARGO와 jev의 실측값**이 한 영상에서 대비되어야 한다.
- **argo-pc VIA로 요청하는 시작 지점이 같아야** 한다. ARGO를 실제로 실행해 같은 명령을 했을 때 목표에 도달하기까지의 실측을 대조해야 한다.
- 우선 **Mac**에서 한다. argo-pc 본체는 Windows 전용이므로 "간단 구현으로 대체"한다.

### 실험 설계 (A/B)

두 arm의 **유일한 차이는 desktopweb lane의 실행기**다.

| | Arm A: ARGO 현재 | Arm B: ARGO + jev 융합 |
|---|---|---|
| 시작 지점 | 같은 문장 → VIA `DelegationSignal{hint}` (argo-pc `submit_hint`와 같은 형태) | 동일 |
| ③ Evaluator | VIA 실제 `Evaluator` + argo-pc와 **동일한 capability card 전체** | 동일 |
| ④ Router | VIA 실제 `Router`, lane id `desktopweb` | 동일 |
| ⑤ 실행기 | tinicore `desktopweb` 서브에이전트 (argo-pc `run_sub_agent_task`와 같은 경로, 재시도 3회 포함) | `jev-via` (TypeSafe 선택 + 텍스트 LLM) |
| 브라우저 | 전용 Chrome 1개 (CDP 9222) | 동일 Chrome |
| 측정 | T0 = hint 제출, 종료 = lane `Result`/`Failed` + 독립 검증 | 동일 |

과제 문장(고정): `네이버에서 서울 날씨 검색해줘`

### 산출물

| # | 산출물 | 위치 |
|---|---|---|
| 1 | ARGO desktopweb 헤드리스 실행기 | `ARGO/tinicli/src/bench.rs` (feature `bench-hooks`) + `ARGO/argo-cli/src/bin/argo_desktopweb.rs` |
| 2 | VIA 벤치 하네스 (Mac용 argo-pc 대체) | `ARGO/argo-pc/via-bench/` (독립 crate) |
| 3 | 벤치 러너 (녹화, 실행, 검증, 반복) | `jev-ultrafast/scripts/bench_via_argo.py` + `scripts/via_bench_lib.py` |
| 4 | 비교 영상 렌더러 | `jev-ultrafast/scripts/render_via_compare.py` |
| 5 | 결과물 | `jev-ultrafast/docs/via-vs-argo.mp4`, `.gif`, `via-vs-argo.png` |
| 6 | 문서 갱신 | `ARGO/docs/plans/FASTWEB_JEV_FUSION.md` §2를 실측표로 교체, `jev-ultrafast/docs/argo-fusion.md`에 결과 링크 |
| 7 | 오프라인 테스트 | `jev-ultrafast/tests/test_via_bench.py`, `via-bench` 단위 테스트 |

**하지 않는 것:**
- jev 에이전트 루프(`agent.py`, `model.py`, `browser.py`, `snapshot.js`, `questions.py`) 수정
- argo-pc 앱 본체(`argo-pc/rust-backend`) 수정
- commit/push (사용자가 요청할 때만)

## 1. 환경 사실 (확인됨, 2026-09-26)

- **ARGO 저장소:** `~/workspaces/ARGO`, 브랜치 `via-dev-demo-mode`(origin 추적). **sparse-checkout(cone)** 상태이며, 현재 받은 디렉토리는 `git sparse-checkout list`로 확인한다.
- **Rust:** 이 Mac에는 `cargo`가 **없다**. `rustup`을 설치해야 한다. 툴체인은 `ARGO/rust-toolchain.toml`을 따르고, via workspace는 `rust-version = "1.94"`다.
- **argo-pc는 Windows 전용:** `via/VIA_ARGO_PC_GUIDE.md`에 "Windows desktop app"이라고 되어 있고 WebView2, UIA를 쓴다. 그래서 argo-pc 본체 대신 아래 구성으로 같은 경로를 재현한다.
  - `via_ask_submit`(텍스트 입력)은 `submit_hint(app, hint)`의 얇은 래퍼다(`argo-pc/rust-backend/src/via_ask.rs:8094`). 음성(PTT)도 STT 후 같은 `submit_hint`로 들어온다(`via_ask.rs:4540`). → **공통 시작점은 `submit_hint`**다.
  - `submit_hint`는 `DelegationSignal{hint: 전체 텍스트, continuity: Unsure}`를 만들어 `Orchestrator::handle_turn`에 넘긴다.
- **VIA 오케스트레이터는 GUI 없이 동작한다:** `via/via-orchestrator/examples/run_one_turn.rs`가 실제 `Evaluator`, `Router`, `Orchestrator`와 stub `VoiceEngine`으로 한 턴을 돈다. via workspace는 `argo-*`/`tinicore` 의존이 금지된 독립 workspace라 크로스플랫폼이다.
- **VIA LLM 설정** (`via/via-orchestrator/src/azure_openai.rs`):
  - 키: `AZURE_OPENAI_API_KEY` 또는 `OPENAI_API_KEY`
  - 그 외: `OPENAI_ENDPOINT`, `OPENAI_MODEL`
  - `VIA_LLM_API_STYLE=openai`로 두면 OpenAI 호환 엔드포인트를 쓴다(기본값은 azure). → Azure 키가 없으면 jev `.env`의 DeepSeek 키로 대체 가능하다. 엔드포인트 경로 조합(`/v1` 포함 여부)은 `chat_url` 함수와 테스트를 읽고 맞춘다.
- **argo-pc의 desktopweb lane 등록:** `via_ask.rs::build_orchestrator` 약 L6697–7070.
  - card prose 문자열은 L7000–7035 부근이다.
  - handler는 `SubAgentDirectHandler{kind:"desktopweb", needs_web_slot:true, prepend_existing_window_hint:true, ..}`이다.
  - `.declaring(&[Capability::Resume])`가 붙어 있다.
- **argo-pc의 서브에이전트 실행:** `argo-pc/rust-backend/src/bridge.rs::run_sub_agent_task` L11434–11720.
  - `AgentLoopConfig`(provider/model/api_key/base_url, `ExecutionContext::cron()`)를 만든다.
  - `headless_run_ctx`, `approval_handler = BoundedAutoApproveHandler`, `exclusive_lease_scope`를 설정한다.
  - `prepend_existing_window_hint`면 goal 앞에 안내문(L11560 부근)을 붙인다.
  - `MAX_SUB_AGENT_RETRIES = 3` 루프를 돌며 `sub_agents.execute(...)`를 호출한다. PARTIAL/FAILED면 이전 결과를 피드백해서 재시도한다.
- **tinicli:** 크로스플랫폼 CLI 라이브러리다.
  - `context_builder::build_context`는 **`pub(crate)`**다(`tinicli/src/context_builder.rs:512`). 외부 crate에서 직접 부를 수 없다 → §3의 `bench` 모듈이 필요하다.
  - `desktop-tools` feature에서 `DesktopWebSubAgentKind`와 `desktopweb_*` 도구를 등록한다(`tinicli/src/agent_registry.rs:2598` 부근).
  - 단발 프롬프트 모드가 있다(`tinicli/src/cli.rs`의 "Optional single-shot prompt").
  - LLM은 `-p/--provider`, `-m/--model`, `--base-url`, `ARGO_API_KEY`, `~/.argo/config.toml`로 지정한다.
- **ARGO 브라우저 브리지** (`tinicli/src/adapters/desktop_browser_bridge.rs`):
  - `ARGO_BROWSER_ATTACH`가 최우선이고, 없으면 9222/9223/9333을 probe한다.
  - `ARGO_BROWSER_NO_AUTOLAUNCH=1`이면 Chrome을 자동으로 띄우지 않는다.
- **jev → 같은 Chrome:**
  - browser-harness는 `BU_CDP_WS`를 지원한다(`browser_harness/admin.py:353`).
  - daemon은 `BU_NAME`마다 하나다. 기본 `default` daemon이 이미 사용자 Chrome에 붙어 있으면 `BU_CDP_WS`가 무시된다. → **반드시 `BU_NAME=viabench`처럼 전용 이름을 쓴다.**
- **root cargo workspace:** 멤버 37개가 모두 있어야 로드된다(현재 sparse-checkout에는 일부만 있다). 멤버 소스 전체는 약 75 MB다. `via`는 root에서 exclude되어 있다.
- **도구:** `ffmpeg` 8.1(`/usr/local/bin/ffmpeg`), 한글 폰트 `/System/Library/Fonts/AppleSDGothicNeo.ttc`, `~/Library/Python/3.12/bin/uv`
- **jev `.env`:** `TYPESAFE_API_KEY`, `TEXT_MODEL_API_KEY`(DeepSeek `deepseek-flash`, base `https://api.deepseek.com`)

## 2. Phase 0 — 실행 가능성 게이트 (반드시 먼저, 하나라도 실패하면 멈추고 보고)

각 게이트의 결과(명령, 출력 요약, 소요 시간)를 `jev-ultrafast/artifacts/via_bench/phase0.md`에 기록한다.

| 게이트 | 할 일 | 통과 기준 |
|---|---|---|
| G1 Rust | `rustup` 설치 (`curl https://sh.rustup.rs -sSf \| sh -s -- -y`). 이후 `ARGO`에서 `rustup show`로 `rust-toolchain.toml` 툴체인을 설치 | `cargo --version` 동작 |
| G2 소스 | `git sparse-checkout add` root workspace 멤버 전부 + `argo-cli` (멤버 목록: `git show HEAD:Cargo.toml`의 `members`). 존재하지 않는 경로(예: `android`)는 그대로 두고 G3에서 확인 | `cargo metadata --no-deps` 성공 |
| G3 ARGO 빌드 | `cargo build -p argo-cli --release` (기본 feature, `desktop-tools` 포함). 네이티브 의존(objectbox 등) 실패 시 `--no-default-features --features desktop-tools` 재시도 | `target/release/argo` 생성, `argo --doctor` 실행됨 |
| G4 VIA 턴 | `via/`에서 `cargo run --example run_one_turn -p via-orchestrator -- "네이버에서 서울 날씨 검색해줘"`. LLM 환경변수는 §1 참고 | `[dispatched]`가 출력됨 (IntentSet 생성) |
| G5 ARGO desktopweb 동작 | 전용 Chrome(§5.1)을 띄우고 `ARGO_BROWSER_ATTACH=http://127.0.0.1:9222 ARGO_BROWSER_NO_AUTOLAUNCH=1 argo "<단발 프롬프트: desktopweb 서브에이전트로 네이버에서 서울 날씨 검색>"` | Chrome에서 네이버 검색 결과가 열림 |
| G6 jev 동작 | `BU_NAME=viabench BU_CDP_WS=<9222 ws> uv run --env-file .env jev-via -- "네이버에서 서울 날씨 검색해줘"` | exit 0, 같은 Chrome에 탭 생성 |
| G7 화면 녹화 | `ffmpeg -f avfoundation -list_devices true -i ""`로 화면 index를 확인한 뒤 5초 녹화. 터미널 앱에 **화면 기록 권한**이 필요하다(시스템 설정 → 개인정보 보호 → 화면 기록) | mp4에 Chrome 창이 보임 |

G3 또는 G5가 실패하면 **추측으로 우회하지 말고** 에러 전문과 함께 사용자에게 보고한다. 기록값으로 대체하는 것은 사용자가 거부한 방안이다.

**ARGO desktopweb에 쓸 LLM:**
- 사용자가 argo-pc에서 평소 쓰는 모델(`~/.argo/config.toml`, `~/.argo/.env`)이 있으면 그것을 쓴다.
- 없으면 Phase 0 보고에서 사용자에게 묻는다. 모델에 따라 ARGO 수치가 크게 달라지므로 임의로 정하지 않는다.
- 어떤 모델을 썼는지는 모든 결과표와 영상에 표기한다.

## 3. 작업 1 — ARGO desktopweb 헤드리스 실행기 (`tinicli::bench`)

**목적:** argo-pc `run_sub_agent_task("desktopweb", goal, …)`를 GUI와 Tauri 없이 **같은 로직**으로 실행한다.

### 3.1 `tinicli/Cargo.toml`
- `[features]`에 `bench-hooks = ["desktop-tools"]`를 추가한다.
- `tinicli/src/lib.rs`에 `#[cfg(feature = "bench-hooks")] pub mod bench;`를 추가한다.

### 3.2 `tinicli/src/bench.rs`
```rust
pub struct DesktopWebRun { pub goal: String, pub prepend_existing_window_hint: bool }
pub struct DesktopWebOutcome { pub final_text: String, pub attempts: u32, pub llm_calls: Option<u64>, pub usage: serde_json::Value }
pub async fn run_desktopweb(cli: &crate::cli::Cli /* 또는 config 경로+LLM override */, run: DesktopWebRun,
                            on_event: impl Fn(serde_json::Value) + Send + Sync + 'static)
    -> Result<DesktopWebOutcome, String>
```
- **컨텍스트:** 단발 프롬프트 경로가 쓰는 것과 같은 방식으로 `context_builder::build_context`를 호출해 `ctx`를 얻는다. `ctx.sub_agent_registry`에 `desktopweb`이 있어야 한다.
- **`AgentLoopConfig`:** `bridge.rs::run_sub_agent_task`(L11470 부근)를 **필드 단위로 그대로** 옮긴다. `execution_context: ExecutionContext::cron()`을 쓰고, provider/model/key/base_url은 tinicli의 LLM 해석 결과를 쓴다.
- **승인:** argo-pc는 `BoundedAutoApproveHandler`(턴당 8 calls / 40 DSL / 4000 chars)를 쓴다. tinicli에 같은 타입이 있으면 재사용하고, 없으면 `desktopweb_*`는 `ApprovalMode::Never`라 승인 핸들러가 불필요할 수 있다. 실제로 확인하고 결정한 내용을 주석으로 남긴다.
- **goal 앞 안내문:** `prepend_existing_window_hint`가 true면 argo-pc L11560 부근의 안내문을 **문자열 그대로** 앞에 붙인다(`{kind}` = `desktopweb`).
- **재시도:** `MAX_SUB_AGENT_RETRIES = 3` 루프와 이전 결과 피드백 문구를 argo-pc(L11660 부근 이후)에서 그대로 옮긴다. 재시도 판단 함수(`partial_is_an_evidence_hedge` 등)는 tinicore의 공개 함수를 쓴다.
- **이벤트:** 각 시도의 시작과 끝, 서브에이전트의 스텝 이벤트를 `on_event`로 JSON으로 내보낸다. `HarnessEventSink` 구현체를 하나 만들어 `event_sink`로 넘긴다(argo-pc의 `StepEventSink` 참고).
  - LLM 호출 수와 usage를 이벤트에서 셀 수 있으면 채운다. 셀 수 없으면 `None`으로 두고 결과표에 "—"로 표시한다. 추정하지 않는다.
- **출처 주석:** 파일 맨 위에 "argo-pc bridge.rs run_sub_agent_task @ eba08d81e3 를 옮긴 것, 차이점 목록"을 적는다. 차이점은 반드시 나열한다(예: web-slot lease 없음 → 단일 Chrome, kill switch 없음).

### 3.3 `argo-cli/src/bin/argo_desktopweb.rs`
- 등록: `argo-cli/Cargo.toml`의 `[[bin]] name = "argo-desktopweb"`, `required-features = ["bench-hooks"]`. feature 정의는 `bench-hooks = ["tinicli/bench-hooks"]`.
- CLI: `argo-desktopweb [tinicli 공통 LLM 플래그] -- <goal>`
- stdout 규약: jev-via와 같은 VIA `CliAgent` 규약을 따른다.
  - 진행 이벤트마다 한 줄: `step NN  <tool>  <요약>`
  - 마지막 줄: `RESULT done|partial|error · <final_text 첫 120자>`
- 추가로 `--events <path>`가 주어지면 JSONL 이벤트를 파일에 쓴다(하네스가 읽는다).
- exit code: `[RESULT:SUCCESS]` → 0, PARTIAL → 2, 실패/에러 → 3

## 4. 작업 2 — VIA 벤치 하네스 `argo-pc/via-bench/` (Mac용 argo-pc 대체)

**독립 crate**로 만든다. `argo-pc/via-adapter/Cargo.toml`처럼 자체 `[workspace]`를 선언한다.

```toml
[package] name = "via-bench"  edition = "2021"  publish = false
[workspace]
[dependencies]
via-core = { path = "../../via/via-core" }
via-engine = { path = "../../via/via-engine" }
via-orchestrator = { path = "../../via/via-orchestrator" }
via-agents = { path = "../../via/via-agents" }
tokio (rt-multi-thread, macros, process, io-util, time), futures, serde_json, dotenvy, clap
```
tinicore와 argo-* 의존은 **넣지 않는다**. ARGO 실행기는 §3의 바이너리를 **프로세스로** 부른다. 그래야 두 arm이 대칭이 된다(둘 다 자식 프로세스).

### 4.1 CLI
```
via-bench --executor argo|jev --hint "<문장>" --events <jsonl> [--argo-bin PATH] [--jev-dir PATH] [--timeout-s 180]
```

### 4.2 구성 (run_one_turn.rs를 기반으로)
1. `dotenvy::dotenv()`와 `AzureOpenAiClient::from_env()`로 Evaluator LLM을 준비한다. 두 arm이 **같은 설정**을 쓴다.
2. `ContextEngine`, `Router`, `Orchestrator`는 `run_one_turn.rs`와 같이 만든다.
3. **Vocabulary:** argo-pc가 `Evaluator::new(llm, <vocabulary>)`에 넘기는 값을 `via_ask.rs`에서 찾아 **그대로 복사**한다.
4. **Roster:** argo-pc `build_orchestrator`에 등록되는 lane을 **전부 같은 id, 같은 `CapabilityCard`(classes, prose 문자열 그대로)**로 등록한다(answer, files, windows, desktop, desktopweb, tools, cancel, correction. `terminal`은 feature-gated라 제외).
   - card 문자열은 `src/cards.rs`에 복사하고, 맨 위에 출처(`via_ask.rs` 커밋, 줄 번호)를 적는다.
   - **desktopweb 이외 lane**의 handler는 즉시 `AgentOutput::Failed("bench: lane not exercised")`를 반환하는 stub이다. 이런 lane으로 라우팅되면 그 run은 `invalid_route`로 기록하고 결과에서 제외한다. 몇 번 발생했는지는 보고한다.
   - desktopweb은 `.declaring(&[Capability::Resume])`까지 동일하게 선언한다.
5. **desktopweb handler** (`DirectHandler` 구현, 두 arm 공통 구조):
   - `--executor argo`면 `argo-desktopweb -- <goal>`를 실행한다. env: `ARGO_BROWSER_ATTACH=http://127.0.0.1:9222`, `ARGO_BROWSER_NO_AUTOLAUNCH=1`
   - `--executor jev`면 `uv run --directory <jev-dir> --env-file .env jev-via --quiet -- <goal>`를 실행한다. env: `BU_NAME=viabench`, `BU_CDP_WS=<ws>`. ws는 `http://127.0.0.1:9222/json/version`의 `webSocketDebuggerUrl`에서 얻는다.
   - stdout 각 줄 → `AgentOutput::Progress`, 마지막 `RESULT` 줄 → `Result`, exit≠0 → `Failed`. `via-agents/src/cli.rs`의 규약과 같다.
   - goal은 Evaluator가 만든 `intent.goal`을 **그대로** 넘긴다. 두 arm이 같은 형태의 입력을 받는다.
6. **VoiceEngine stub:** `run_one_turn.rs`의 `PrintlnVoiceEngine`처럼 만들되, `inject`된 frame(receipt/result)을 이벤트로 기록한다.
7. **시작 신호:** `DelegationSignal{ hint, continuity: Continuity::Unsure, references: vec![], clarified: false }`. argo-pc `submit_hint`와 같다. argo-pc가 hint 앞에 붙이는 `[브라우저]` 블록은 첫 턴에 슬롯이 비어 있어 빈 값이므로 생략한다. 이 사실을 주석으로 남긴다.

### 4.3 이벤트 (JSONL, `--events` 파일)
T0는 `handle_turn` 호출 직전의 단조 시계이고, 모든 이벤트는 `t_ms`(T0 기준)와 `wall_ms`(epoch)를 갖는다.

| event | 필드 | 발생 시점 |
|---|---|---|
| `submit` | hint, executor, evaluator_model | T0 |
| `intent_set` | lanes[], goal, clarify | `handle_turn`이 `Dispatched`/`Clarify`를 반환한 직후 |
| `receipt` | text | VoiceEngine inject(receipt) |
| `executor_start` | pid, cmd | 자식 프로세스 spawn |
| `progress` | line | stdout 한 줄 |
| `result` / `failed` | text, exit_code | 자식 종료 |
| `inject_result` | text | VoiceEngine inject(result) (사용자에게 말해질 시점) |
| `end` | total_ms | drain 완료 |

**종료 시간의 정의:** `inject_result.t_ms`, 곧 "사용자에게 결과가 전달되는 시점"이다. 실행기 자체 시간(`result.t_ms - executor_start.t_ms`)도 따로 보고한다.

### 4.4 테스트
- `cards.rs`의 lane id 목록이 기대한 8개인지 확인한다.
- stdout → AgentOutput 매핑: `echo`나 `sh -c` 가짜 실행기로 exit 0/2/3을 확인한다.
- 이벤트 직렬화에 `t_ms`가 단조 증가하는지 확인한다.
- LLM을 호출하지 않는다.

## 5. 작업 3 — 벤치 러너 `jev-ultrafast/scripts/bench_via_argo.py`

### 5.1 전용 Chrome
```
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 --user-data-dir=$HOME/.argo-browser-bench \
  --no-first-run --no-default-browser-check \
  --window-position=0,40 --window-size=1280,860 about:blank
```
- 사용자의 평소 Chrome과 **분리된 프로필**을 쓴다. 로그인이나 쿠키 차이가 한쪽에만 유리하게 작용하지 않도록 두 arm이 같은 프로필을 쓴다.
- run마다 초기화한다. CDP `/json/list`에서 page target을 모두 닫고 `about:blank` 탭 하나만 남긴다(`/json/new?about:blank`, `/json/close/<id>`).

### 5.2 탭 전면화 워처 (두 arm 공통)
- 두 실행기 모두 새 탭을 열 수 있다. jev는 `background=True`로 연다.
- 영상에 보이도록 러너가 100 ms마다 `/json/list`를 polling해 **새로 생긴 page target을 `/json/activate/<id>`**로 전면화한다.
- 두 arm에 똑같이 적용한다. 이 개입은 결과 문서에 명시한다.

### 5.3 한 run의 절차
1. Chrome을 초기화하고 1초 대기한다(안정화).
2. `ffmpeg -f avfoundation -framerate 30 -capture_cursor 1 -i "<screen>:none" -c:v libx264 -preset ultrafast -pix_fmt yuv420p screen.mp4` 녹화를 시작한다. 첫 프레임이 나온 wall time을 기록한다(stderr의 첫 `frame=` 또는 시작 직후 `time.time()`). 오차 ±100 ms를 문서에 명시한다.
3. `via-bench --executor <arm> --hint "네이버에서 서울 날씨 검색해줘" --events events.jsonl`을 실행하고 timeout 180 s를 건다.
4. 종료 후 1.5 초 더 녹화하고 ffmpeg를 정상 종료한다(stdin에 `q`).
5. **독립 검증:** CDP로 page target 중 URL host가 `search.naver.com`인 것을 찾고, `Runtime.evaluate("document.body.innerText")`로 텍스트를 읽는다. `verify_naver_weather(url, text)` 기준은 `scripts/record_via.py`와 **같다**. 이 함수는 `via_bench_lib.py`로 옮기고 `record_via.py`가 import하게 바꾼다.
6. `run.json`에 events 요약, 검증 결과, ffmpeg 시작 wall time, 화면 crop 정보, arm, 모델 이름을 저장한다.

### 5.4 반복과 순서
- arm마다 **워밍업 1회(버림) + 측정 3회**. 순서는 `A B A B A B`로 교대한다(네트워크와 캐시 편향 완화).
- 저장 위치: `artifacts/via_bench/<YYYYmmdd-HHMMSS>/<arm>-<n>/`, 전체 요약은 `summary.json`.
- 검증 실패나 `invalid_route` run도 **삭제하지 않고** 요약에 포함한다(성공률 표기).

### 5.5 `scripts/via_bench_lib.py` (테스트 대상 순수 함수)
- `verify_naver_weather(url, text) -> dict`
- `load_events(path) -> list[dict]`
- `stages(events) -> dict`: `evaluator_ms`(intent_set), `executor_ms`(result − executor_start), `first_action_ms`(첫 progress), `total_ms`(inject_result), `llm_calls`
- `summarize_runs(runs) -> dict`: arm별 median/min/max, 성공률, 대표 run(= total_ms가 median인 성공 run)
- `markdown_table(summary) -> str`

## 6. 작업 4 — 비교 영상 `scripts/render_via_compare.py`

- 입력: `bench` 결과 폴더. 각 arm의 **대표 run**(median 성공 run) 1개씩을 쓴다.
- **캔버스:** 1920×1080, 30 fps, **원래 속도**. 길이 = max(두 arm total_ms) + 1.5 s hold.
- **상단 공통 바 (같은 시작점을 강조):**
  - `🎤 "네이버에서 서울 날씨 검색해줘"  →  VIA (같은 Evaluator · 같은 Router · 같은 lane)`
  - 그 아래: `왼쪽: ARGO desktopweb (현재)   |   오른쪽: ARGO + jev (융합)`
- **좌우 패널:** 각 run의 `screen.mp4`에서 Chrome 창 영역을 crop한다.
  - Retina는 2×이므로 실제 녹화 해상도 대비 비율로 계산한다.
  - 프레임 시각은 `t = 영상시각 − (T0_wall − ffmpeg_start_wall)`로 T0에 맞춘다. 좌우 모두 **같은 T0에서 동시에 시작**한다.
- **패널별 오버레이:**
  - 큰 타이머 `00.00 s`
  - 단계 타임라인 막대: `Evaluator` → `실행기` → `결과 전달`. 이벤트 시각에 맞춰 칸이 채워진다.
  - 최근 progress 3줄
  - 누적 LLM 호출 수(알 수 있으면)
- **먼저 끝난 쪽:** 마지막 프레임에서 멈추고 `✓ 완료 X.X s`(검증 통과 표시) 배너를 띄운다. 다른 쪽은 계속 재생한다.
- **하단 요약 (영상 끝 1.5 s 동안):**
  - 3회 median: `ARGO X.X s · 융합 Y.Y s`, 성공률 `a/3 · b/3`
  - 모델 표기: Evaluator 모델, ARGO desktopweb 모델, jev(TypeSafe + 텍스트 모델)
- **캡션 (항상 표시):** `같은 Mac · 같은 Chrome 프로필 · 같은 네트워크 · 순차 실행(A/B 교대) · 원 속도`
- **출력:**
  - `docs/via-vs-argo.mp4`, `docs/via-vs-argo.gif`: ffmpeg 인자는 `render_demo.py`와 같게 하되, GIF는 폭 1280
  - `docs/via-vs-argo.png`: 결과표 이미지
  - stdout: 마크다운 표
- 폰트: 한글은 `AppleSDGothicNeo.ttc`, 숫자는 `Menlo.ttc`. **텍스트가 캔버스 밖으로 나가지 않는지** 프레임을 추출해 직접 확인한다(이전 PoC에서 오른쪽 끝 숫자가 잘린 사례가 있다).

### 결과표 형식
| 항목 | ARGO desktopweb (실측) | ARGO + jev (실측) |
|---|---|---|
| 총 시간 (T0→결과 전달), median [min–max] | | |
| Evaluator (③) | | |
| 실행기 (⑤) | | |
| 첫 브라우저 액션까지 | | |
| LLM 호출 수 (실행기) | | |
| 성공률 (독립 검증) | a/3 | b/3 |
| 사용 모델 | Evaluator=…, desktopweb=… | Evaluator=…, TypeSafe jev-latest, text=… |

## 7. 작업 5 — 문서 갱신
- **`ARGO/docs/plans/FASTWEB_JEV_FUSION.md`:** §2 "PoC 실측값"을 이번 **A/B 실측표**로 교체한다. 기존 "기록값 비교" 표는 "참고: 과거 기록값"으로 아래에 내린다.
  - 방법 절을 추가한다: via-bench 구성, 두 arm의 차이가 실행기뿐이라는 점, argo-pc 대비 차이점(§8).
  - 상태 줄을 "A/B 실측 완료(macOS 대체 하네스)"로 바꾼다.
- **`jev-ultrafast/docs/argo-fusion.md`:** 맨 위에 결과 링크(영상, 표)를 추가한다.
- **`jev-ultrafast/README.md`는 수정하지 않는다** (사용자 요청 없음).

## 8. argo-pc 대비 차이점 (문서와 영상에 반드시 명시)

| argo-pc (Windows) | via-bench (Mac) | 영향 |
|---|---|---|
| Tauri 앱, via-ask 입력창 → `submit_hint` | CLI가 같은 형태의 `DelegationSignal` 생성 | 입력 UI 지연 제외 (두 arm 동일) |
| `WebSlotPool` 3 slot, 스레드별 lease | Chrome 1개 (9222) | 동시 요청 없음. 단일 요청이라 영향 적음 |
| desktopweb 실행이 앱 프로세스 내부 | 자식 프로세스 `argo-desktopweb` | 프로세스 기동 비용이 추가됨. 기동 시간을 이벤트로 따로 기록 |
| 음성 엔진(Realtime/Qwen) | stub (텍스트) | 음성 구간 제외 (두 arm 동일) |
| screen/pointer 컨텍스트 | 없음 | 첫 턴 네이버 검색에는 불필요 |

## 9. 완료 기준
- [ ] Phase 0 게이트 G1–G7 결과가 `phase0.md`에 기록되어 있다.
- [ ] `cargo test -p via-bench`(via-bench 디렉토리), jev `ruff`/`pytest`/`node --check`/`uv build`가 모두 통과한다.
- [ ] A/B 각 3회(+워밍업)를 실행했고 `summary.json`이 있다. 실패 run도 포함되어 있다.
- [ ] 두 arm의 `intent_set.lanes`가 모두 `["desktopweb"]`이다(아니면 invalid로 표기).
- [ ] `docs/via-vs-argo.mp4`를 프레임 추출로 **직접 확인**했다.
  - 좌우가 같은 순간에 시작한다.
  - 한글이 깨지지 않는다.
  - 텍스트가 잘리지 않는다.
  - 원 속도다(영상 길이 ≈ 느린 쪽 total + 1.5 s).
- [ ] 결과표 숫자가 `summary.json`과 일치한다.
- [ ] 두 저장소 모두 commit하지 않았다. ARGO는 새 브랜치 `via-fastweb-bench`에서 작업한다(`git switch -c via-fastweb-bench`).

## 10. 함정
1. **`BU_NAME`을 빼먹으면** jev가 사용자 평소 Chrome(default daemon)에 붙어서 A/B의 브라우저가 달라진다.
2. **ARGO가 Chrome을 새로 띄우지 않게** `ARGO_BROWSER_NO_AUTOLAUNCH=1`과 `ARGO_BROWSER_ATTACH`를 같이 준다.
3. **card prose를 요약하거나 고치지 않는다.** 한 글자라도 다르면 "같은 Evaluator"가 아니다. 복사 후 원문과 diff로 확인한다.
4. **DONE이나 `[RESULT:SUCCESS]`는 성공 근거가 아니다.** 성공은 §5.3의 독립 검증만으로 판정한다.
5. **영상 속도와 대기 구간을 편집하지 않는다.** 느린 쪽이 수십 초여도 그대로 둔다.
6. **ARGO 쪽 LLM 모델을 임의로 바꾸지 않는다.** Phase 0에서 확인한 모델을 고정하고 표기한다.
7. **화면 녹화 권한이 없으면** ffmpeg가 검은 화면을 녹화한다. G7에서 반드시 프레임을 열어 본다.
8. **Retina crop:** avfoundation 출력은 물리 픽셀이다. 창 좌표(논리 pt)에 배율을 곱한다.
9. **`.env`와 `~/.argo/.env`의 키를** 로그, 이벤트, 문서에 출력하지 않는다. `jev-ultrafast/.env.example`에는 실제 키가 들어 있으므로 절대 커밋하지 않는다.
10. `tinicli::bench`는 argo-pc 로직의 **복사본**이다. 차이를 숨기지 말고 파일 상단과 §8 표에 나열한다.
