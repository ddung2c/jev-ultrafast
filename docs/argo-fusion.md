# ARGO(VIA) × Jev Ultrafast 융합 분석

- 기준: ARGO `origin/via-dev-demo-mode`(legacy, 최신 커밋 `eba08d81e3`), jev-ultrafast `main`
- 범위: VIA 파이프라인 ① Voice Engine → ② Context Engine → ③ Evaluator → ④ Router → ⑤ Agent 중 **③~⑤ 구간**
- 결과물: 개선 지점과 우선순위, PoC 1건 설계. 코드 수정은 다음 단계에서 한다.

ARGO 경로는 모두 저장소 루트 기준이다. 수치는 ARGO 코드 주석이나 plan 문서에 **기록된 값**이다. 별도 표시가 없으면 이번에 재측정하지 않았다.

## 1. 요약

- **병목:** 음성 턴의 시간 대부분은 ③ 이후 ⑤의 **웹 실행 루프**에서 쓰인다.
  - VIA의 웹 요청은 모두 `desktopweb` lane으로 간다. 이 lane은 tinicore ReAct 서브에이전트라서 **스텝마다 거대한 프롬프트로 DSL을 생성**한다.
  - 기록된 Naver 턴 하나가 48초였다. 그중 LLM 두 번(draft 60,647 tokens / 3.1s, 검증 92,571 tokens / 8.2s)만으로 17초를 썼다.
- **jev가 가진 답:** 같은 성격의 과제(Google Flights 검색)를 7.1초에 끝낸다.
  - 한 스텝에 **TypeSafe 요청 1번**으로 operation과 target을 **선택**한다.
  - 텍스트 LLM은 `TYPE_TEXT`일 때만 부른다.
- **ARGO가 가진 답:** jev에 없는 것을 ARGO가 갖고 있다.
  - 음성, 문맥, 의도 해석, thread 연속성(web-slot)
  - 시작 URL 지식(`web_skill` URL Patterns)
  - 브라우저 slot 풀
- **결론:** ⑤의 웹 lane 앞단에 jev를 **"fastweb" 실행기**로 두고, 실패할 때만 기존 `desktopweb`으로 넘긴다. 연결은 VIA가 이미 가진 `CliAgent` 전송으로 하므로 ARGO 런타임은 건드리지 않는다.

## 2. 현재 구조 (legacy branch 실제 코드 기준)

### 2.1 ③ Evaluator
- 코드: `via/via-core/src/evaluator.rs`. host가 준 `LlmClient`로 **one-shot 호출**을 한다. argo-pc에서는 Azure OpenAI, `temperature 0`, 비스트리밍이다.
- 출력: `IntentSet { intents: [Intent{goal, agent, thread, entities, attachments}], clarify }`(`via/via-core/src/intent.rs`)
- 파싱: 모델이 쓴 텍스트에서 JSON을 추출해 역직렬화한 뒤 roster로 검증한다.
  - strict 스키마(`intent_set_schema()`)는 정의되어 있지만 `response_format`으로 전달하는 호출 지점이 없다.
- 라우팅 근거: lane별 `CapabilityCard`의 prose뿐이다(`argo-pc/rust-backend/src/via_ask.rs::build_orchestrator`).
- 브라우저 문맥: `[브라우저]` 블록 한 줄씩만 들어간다(`render_web_browser_block`).
  - 예: `1 (desktopweb-0) [키보드 검색 - Coupang | www.coupang.com]: <goal> — <url>`

### 2.2 ⑤ desktopweb lane
- 흐름: `SubAgentDirectHandler{kind:"desktopweb"}` → `WebSlotPool` lease(Chrome 3개, CDP 9222/9401+) → `bridge.run_sub_agent_task`
  - 코드: `argo-pc/rust-backend/src/{via_ask,web_slots,bridge}.rs`
- 루프: `tinicore/src/agent/sub_agents/desktop_web.rs`의 ReAct 루프다. 최대 30회, 사용 모델은 앱 설정의 LLM이다.
- 관측: 주입한 JS DOM walker의 `SnapshotNode{ref:"rN", role, text, ariaLabel, …}`. 최대 250개이고, 페이지 텍스트는 15k자까지다.
- 행동: 줄 단위 DSL을 **모델이 생성**한다(`navigate`, `click_ref`, `click_text`, `click_xy`, `type_text`, `evaluate_js`, … 약 30종).
- 완료: `DesktopWebCompletionGate`가 LLM 턴을 한 번 더 써서 검증한다. 실패나 partial이면 전체를 최대 3회 재실행한다.
- 안전: `desktopweb_*`는 `ApprovalMode::Never`다. 명시적 `navigate`만 origin allowlist를 검사한다.

### 2.3 기록된 지연

| 항목 | 값 | 출처 |
|---|---|---|
| Naver 웹 턴 전체 | 48 s | `tinicore/src/agent/sub_agents/desktop_web.rs` 주석 (2026-09-08) |
| draft 턴 | 60,647 tokens · 3.1 s | 같은 곳 |
| 완료 검증 턴 | 92,571 tokens · 8.2 s (2차 8.8 s) | 같은 곳 |
| 검증 거절 후 재시도 | 27 s 낭비 (1차 20.4 s 성공 → 2차 T+48 s) | `via_ask.rs` ~L3691 |
| 콜드 브라우저 | T+10.3 s에 사용 가능 | `web_slots.rs` ~L573 |
| VIA 날씨 질의 | 15.79 s (agent) / 17.31 s (interface) | `docs/plans/VIA_EDGE_CATALOG_AND_DIRECT_MODE.md` |
| 스테이지별 계측 | **없음** ("no latency figure in the doc is measured") | `docs/plans/VOICE_INTERACTION_API.md` §6.7 |

비교용 jev 수치: Google Flights 7,073 ms. 페이지 로딩 대기, 텍스트 생성, stale 재결정이 모두 포함된 값이다(`docs/performance.md`).

## 3. 장점 대조

| 영역 | ARGO/VIA 강점 | jev 강점 |
|---|---|---|
| 입력 | 음성, 발화 문맥, 지시 대상(pointing), 다중 의도 | 한 문장 goal |
| 연속성 | thread / web-slot, follow-up 라우팅 | 없음 (단일 세션) |
| 시작점 | `web_skill` URL Patterns, `desktopweb_open` | **navigate 없음**. 시작 URL이 필요함 |
| 스텝 결정 | 대형 프롬프트로 DSL **생성** | 동적 요소 표에서 operation과 target을 **선택**. 요청 1회 |
| 텍스트 | 메인 LLM이 함께 생성 | `TYPE_TEXT`일 때만 소형 LLM 호출. stale 재시도 시 재사용 |
| 완료 | LLM 검증 턴 + 재시도 | `DONE` 확률 + 독립 결과 검증(`examples/flights.py`) |
| 안전 | 넓은 DSL (`evaluate_js`, `click_xy`), 승인 없음 | 관측된 노드 index만 실행, freshness·가림 재확인 |
| 계측 | 없음 | 결정별 latency, 확률, trace |

## 4. 개선 지점 (우선순위순)

### ① ⑤ 웹 실행: 생성 → 선택 (효과 최대)
- **현재:** 스텝마다 60k+ token 프롬프트로 DSL을 생성한다.
- **적용:** 관측 결과를 jev의 인덱스 요소 표로 바꾸고, 한 번의 요청에서 operation 확률과 operation별 target head를 받는다.
  - jev 루프를 그대로 쓰거나, ARGO `SnapshotNode`를 jev `action_space()` 입력 형태로 매핑한다.
  - 실행은 선택된 operation의 target만 한다. `click_ref` / `type_text` / `select_option`으로 매핑한다.
- **기대:** 스텝당 수 초 → 수백 ms. 토큰은 자릿수 단위로 감소한다.
- **근거 코드:** jev `jev_ultrafast/model.py::choose`, ARGO `tinicore/src/tools/builtins/desktop_web.rs::parse_dsl`

### ② ⑤ 완료 검증: LLM 검증 턴 제거
- **현재:** completion gate가 LLM 턴을 한 번 더 쓰고(8.2 s), 거절되면 전체를 재실행한다.
- **적용:** `DONE` 확률에 goal 유형별 **결정적 검증**(URL, 제목, 가시 텍스트 조건)을 결합한다. 불확실할 때만 기존 gate를 쓴다.
- **기대:** 턴당 약 8 s를 절감하고, 재시도 낭비(27 s 사례)가 줄어든다.

### ③ ⑤ 안전성: 모델 출력의 표현력 축소
- **현재:** `ApprovalMode::Never`인데 `evaluate_js`와 `click_xy`가 모두 모델 선택지에 있다.
- **적용:** jev 원칙을 적용한다. 모델 출력은 관측된 노드 index 또는 option index뿐이고, selector·좌표·코드는 불가하다. 실행 직전에 페이지 fingerprint와 가림 여부를 재확인한다.
  - 결제·전송 같은 비가역 행동은 operation 수준에서 `BLOCKED`로 처리하거나 음성 확인으로 돌린다.

### ④ ③ Evaluator: 라우팅을 유한 선택으로
- **현재:** 설계 문서는 "라우팅은 capability 분류"라고 하지만, 실제로는 IntentSet JSON 전체를 생성한다.
- **적용:** 생성이 꼭 필요한 부분과 선택으로 충분한 부분을 나눈다.
  - `goal` 문자열: 지시어를 풀어 쓴 문장. **생성**
  - `agent` lane, `thread`(new / continue:slot N / steer), 지시 대상 후보: **선택 + 확률**
  - 확률이 낮으면 `clarify`로 보낸다. 지금은 파싱 실패일 때만 clarify가 된다.
- **기대:** 라우팅 지연과 오라우팅이 줄고, 확신도 기반 되묻기가 가능해진다. `VoiceMode::Direct`(③ 생략)와도 공존할 수 있다.

### ⑤ ② → ⑤ 사전 관측 / 투기 실행
- **현재:** Context Engine에는 web-slot의 title과 url만 있고, 에이전트는 첫 관측부터 다시 한다. 콜드 브라우저에서 불필요한 첫 `observe`로 2.4 s를 쓴 기록도 있다.
- **적용:** "always hot" 원칙을 브라우저로 확장한다.
  - 활성 slot의 jev 스냅샷(원자적, 한 번의 브라우저 호출)을 전사 delta마다 갱신해 두고, delegation signal 시점에 freeze한다.
  - follow-up("그거 장바구니에 담아")은 발화가 끝나기 전에 첫 결정을 투기적으로 계산할 수 있다. jev는 이미 stale 판정과 재사용 규칙을 갖고 있다.

### ⑥ 시작 URL: ARGO가 jev의 빈틈을 메움
- **jev의 빈틈:** jev에는 navigate가 없다. 앞서 "네이버에서 서울 날씨"를 하려면 start URL이 필요했던 이유다.
- **메우는 방법:** ARGO의 `web_skill` URL Patterns(`tinicore/src/skills/web_skill.rs`)나 `desktopweb_open`으로 시작 페이지를 열고, 그 뒤부터 jev가 선택한다.
  - 검색형 goal은 URL 템플릿으로 결과 페이지에 바로 진입할 수 있다.
  - 단, PC용으로 번들된 web skill은 현재 없다. 모바일 에셋만 있다.

### ⑦ 계측: 효과를 입증할 공통 trace
- **현재:** VIA 문서 스스로 스테이지 계측이 없다고 명시한다.
- **적용:** jev의 결정 trace 형식(결정별 `latency_ms`, 확률, 실행 결과, 텍스트 호출 수)을 fastweb 실행 결과와 함께 저장한다. ARGO `llm_call_steps` 표와 나란히 비교한다.

### 사내 배포 과제 (PoC 범위 밖)
- TypeSafe는 외부 API다. PoC에서만 허용하기로 했다.
- 사내 배포 시에는 "선택"을 내부 모델로 구현해야 한다. 후보는 두 가지다.
  - Azure OpenAI logprob 기반 선택
  - on-device 제약 디코딩. ARGO에 LiteRT-LM 선례가 있다(`argo-mobile/.../OnDeviceLlmBridge.kt`).

## 5. PoC 설계: jev를 VIA "fastweb" CLI 에이전트로 (개선 ①·②·⑥)

### 5.1 왜 CLI 전송인가
- `via/via-agents/src/cli.rs`의 `CliAgent`는 구현되어 있지만, 아직 이를 생성하는 host가 없다.
- 규약은 다음과 같다.
  - 실행: `program [base_args] [resume_args] -- <goal>`
  - stdout 각 줄 → `AgentOutput::Progress`
  - 누적 출력 → `Result`
  - non-zero exit, timeout → `Failed`
- VIA 원칙("ARGO enters via its public surfaces only", "agents are black boxes")을 지키면서 ARGO 런타임 수정 없이 붙일 수 있다.

### 5.2 A단계: jev-ultrafast (macOS에서 검증)
- **`examples/via_agent.py`**
  - 사용법: `via_agent.py [--start-url URL] -- <goal>`
  - 시작 URL 결정 순서: 인자 → web_skill 스타일 URL 패턴 → 기본 검색엔진
    - 검색어 추출은 소형 텍스트 모델 경로를 재사용한다.
    - policy에 사이트별 계획이나 하드코딩 값은 넣지 않는다(`AGENTS.md`).
  - 실행: `Agent(url, goal)`
  - stdout: 액션당 1줄. 마지막 줄은 음성으로 읽을 한 문장이다. VIA가 누적 출력을 Result로 쓰므로 짧게 유지한다.
  - 종료 코드: `done` → 0. `blocked`, 스텝 한도, 확신 부족 → non-zero(VIA에서는 `Failed`, 곧 fallback 신호).
- **브라우저 대상:** browser-harness의 `BU_CDP_WS`로 임의 CDP endpoint를 받는다. ARGO web-slot 재사용을 대비한 것이다.
- **계측:** `artifacts/via_runs/<ts>.json`에 결정별 latency, 모델 호출 수, 총 ms를 저장한다.
- **테스트:** 오프라인 테스트(URL 결정, exit code, stdout 형식). 유료 API는 호출하지 않는다.

### 5.3 B단계: argo-pc 연동 (Windows, 설계만)
- **등록:** `via_ask.rs::build_orchestrator`에 `CliAgent::new(CliAgentConfig{ id:"fastweb", program:"uv", base_args:[…via_agent.py], deadline, … })`를 등록한다.
- **capability card:** "단일 사이트 검색·조회·탐색. 로그인·결제·전송 금지."
- **fallback:** `Failed`면 같은 goal을 `desktopweb`으로 재디스패치한다. 기존 `Orchestrator::dispatch_direct`(correction redispatch 경로)를 재사용한다.
- **slot 공유:** `web_slots.rs`의 slot CDP 포트를 `BU_CDP_WS`로 전달해 같은 thread의 브라우저를 이어 쓴다.

### 5.4 성공 기준
| 시나리오 | ARGO 기록 | PoC 목표 |
|---|---|---|
| 네이버 "서울 날씨" 검색 → 결과 표시 | 15.8–17.3 s (VIA 날씨), 48 s (Naver 웹 턴) | ⑤ 구간 < 5 s |
| 쿠팡 상품 검색 → 결과 목록 | 20–48 s (재시도 포함 사례) | < 8 s, 재시도 0 |
| 없는 요소를 요구하는 goal | 재시도 3회 | 즉시 non-zero → fallback |

- 결과 판정은 `DONE` 신호가 아니라 최종 페이지를 독립적으로 확인해서 한다.
- 수치는 PoC 실행 후 이 표에 채운다.
