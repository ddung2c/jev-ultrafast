# VIA fastweb PoC — 구현 설계서

> 이 문서만 읽고 구현할 수 있도록 작성했다. 배경 분석은 [argo-fusion.md](argo-fusion.md)에 있다.
> 구현 전에 `README.md`와 `AGENTS.md`를 읽는다. AGENTS.md 규칙이 이 문서보다 우선한다.

## 0. 목표와 산출물

**목표:** ARGO `desktopweb` lane(대형 LLM이 스텝마다 DSL을 *생성*)과 jev(요소 표에서 *선택*)의 속도 차이를, 실제로 실행한 기록과 영상으로 보여준다. 동시에 VIA `CliAgent`로 그대로 붙일 수 있는 CLI를 만든다.

**데모 시나리오 (고정):** 네이버 홈에서 시작해 "서울 날씨"를 검색하고 결과가 보이면 멈춘다.

| # | 산출물 | 경로 |
|---|---|---|
| 1 | VIA용 CLI 로직 (테스트 가능한 모듈) | `jev_ultrafast/via.py` + `pyproject.toml`에 `jev-via` 스크립트 |
| 2 | 오프라인 테스트 | `tests/test_via.py` |
| 3 | 녹화 스크립트 (CDP screencast + 독립 검증) | `scripts/record_via.py` |
| 4 | 렌더 스크립트 (영상, GIF, 비교표 이미지, 마크다운 표) | `scripts/render_via.py` |
| 5 | 영상과 비교표 | `docs/via-demo.mp4`, `docs/via-demo.gif`, `docs/via-comparison.png` |
| 6 | ARGO 쪽 문서 | `~/workspaces/ARGO/docs/plans/FASTWEB_JEV_FUSION.md` |

**하지 않는 것:** ARGO Rust 코드 수정, git commit/push(사용자가 요청할 때만), 에이전트 루프(`agent.py`, `model.py`, `browser.py`, `snapshot.js`, `questions.py`) 수정.

## 1. 환경 사실 (확인됨)

- 실행: `~/Library/Python/3.12/bin/uv` (PATH에 없음). 예: `~/Library/Python/3.12/bin/uv run --env-file .env python ...`
- `.env`에 `TYPESAFE_API_KEY`, `TEXT_MODEL_API_KEY`(DeepSeek, `deepseek-flash`)가 있다. `.env`는 git에서 제외되어 있다.
- 브라우저: `Agent(url, goal)`가 browser-harness 데몬을 통해 **사용자 Chrome에 백그라운드 탭**을 연다(`Browser.__init__` → `Target.createTarget`). Chrome에서 `chrome://inspect/#remote-debugging`이 켜져 있어야 한다. 다른 CDP endpoint는 환경변수 `BU_CDP_WS`로 지정한다(browser-harness 기능, 코드 수정 불필요).
- `ffmpeg` 8.1: `/usr/local/bin/ffmpeg`
- 폰트: 한글은 `/System/Library/Fonts/AppleSDGothicNeo.ttc`, 숫자는 `/System/Library/Fonts/Menlo.ttc`. `render_demo.py`의 Arial은 한글을 못 그리므로 쓰지 않는다.
- `artifacts/`는 `.gitignore`에 있다. 원시 녹화는 여기에 둔다.
- 현재 작업 트리에 커밋 안 된 변경이 있다(대시보드 "Any website" 모드, `.env.example`). 건드리지 않는다. `.env.example`에는 실제 키가 들어 있으니 절대 커밋하지 않는다.

## 2. 재사용할 기존 코드 (새로 만들지 말 것)

| 필요 | 이미 있는 것 |
|---|---|
| 루프 실행 | `jev_ultrafast.Agent(url, goal)`, `agent.run()`은 스텝마다 `snapshot()` 상태를 yield |
| 스텝 기록 | `state["history"][i]`: `step, action(라벨), kind, operation, target, probability, confidence, latency_ms(TypeSafe), text, text_helper, text_latency_ms, usage, executed_ms, elapsed_ms, page_changed, url` |
| 모델 호출 수 | TypeSafe 호출 = `len(state["decisions"])` (stale로 버려진 결정 포함). 텍스트 LLM 호출 = `len(state["text_calls"])` |
| 총 시간 | `state["elapsed_ms"]` (첫 예측 시점부터. 초기 페이지 로드는 제외) |
| 최종 페이지 | `state["page"]`: `url, title, text, actions` |
| 연속 녹화 | `scripts/record_flights.py`의 `Page.startScreencast` + `drain_events()` 스레드 패턴 |
| 영상 합성 | `scripts/render_demo.py`의 프레임 선택(`ts <= t`인 마지막 프레임), 30fps PNG → ffmpeg MP4 → palette GIF |
| 결과 검증 패턴 | `examples/flights.py::verify(page)`: DONE을 믿지 않고 최종 페이지를 독립 확인 |

`agent.run()`의 종료 방식:
- `status == "done"` 또는 `"blocked"`면 제너레이터가 끝난다.
- 액션 60개(`MAX_STEPS`) 초과, 결정 120개 초과, 모델 API 오류는 **`ValueError`/`RuntimeError`를 raise**한다.
- `StalePage`는 `tick` 안에서 처리되므로 밖으로 나오지 않는다.

## 3. 작업 1: `jev_ultrafast/via.py`

VIA `CliAgent` 규약(`ARGO/via/via-agents/src/cli.rs`):
- 실행: `program [base_args] -- <goal>`
- stdout 한 줄 → `AgentOutput::Progress`
- 누적 stdout → `Result`
- exit ≠ 0 → `Failed`

### 3.1 공개 함수

```python
SITES: dict[str, str]          # 사이트 별칭 → 홈 URL
def start_url(goal: str, explicit: str | None = None) -> str
def step_line(entry: dict) -> str
def summarize(state: dict) -> dict
def exit_code(status: str) -> int
def main(argv: list[str] | None = None) -> int
```

**`SITES`와 `start_url`**
- 별칭 표 (goal 문자열에 소문자 기준 부분 일치하면 선택, 표 순서대로 첫 일치):
  - `네이버`/`naver` → `https://www.naver.com`
  - `구글`/`google` → `https://www.google.com`
  - `쿠팡`/`coupang` → `https://www.coupang.com`
  - `다음`/`daum` → `https://www.daum.net`
  - `유튜브`/`youtube` → `https://www.youtube.com`
  - `위키`/`wikipedia` → `https://en.wikipedia.org`
- 우선순위: `explicit` → 별칭 일치 → 기본값 `https://www.google.com`.
- `explicit`는 `demo.start_url()`과 같은 규칙으로 검증한다(http/https만, 도메인만 주면 `https://` 추가). 중복을 피하려면 `from .demo import start_url as _normalize`로 재사용한다.
- **LLM을 쓰지 않는다.** 검색어를 URL에 넣지도 않는다. 홈에서 시작해 jev가 직접 입력하고 클릭해야 데모가 의미 있고, AGENTS.md의 "하드코딩 값 금지"에도 맞다.
- 사이트 이름을 URL로 바꾸는 일은 시작점 결정일 뿐이라 policy가 아니다. `agent.py`나 `questions.py`에는 넣지 않는다.

**`step_line(entry)`**: `history` 항목 하나를 한 줄로 만든다. 예:
```
step 02  TYPE_TEXT  검색어를 입력해 주세요.  "서울 날씨"  ·  decision 312 ms  ·  text 841 ms  ·  p 0.97
```
- 형식: `step {step:02d}  {operation}  {action[:40]}` 뒤에 `text`가 있으면 `  "{text}"`, 그 뒤 `  ·  decision {latency_ms} ms`, `text_latency_ms > 0`이면 `  ·  text {…} ms`, 마지막 `  ·  p {probability:.2f}`.
- 줄바꿈 문자는 공백으로 바꾼다(VIA는 한 줄을 Progress 하나로 본다).

**`summarize(state)`** 반환값 (trace와 렌더러가 함께 쓴다):
```python
{
  "status": state["status"],
  "total_ms": state["elapsed_ms"],
  "actions": len(history),
  "typesafe_calls": len(state["decisions"]),
  "text_calls": len(state["text_calls"]),
  "decision_ms_median": median([d["latency_ms"] for d in decisions]) or None,
  "decision_ms_max": ...,
  "text_ms_total": sum(c["latency_ms"] for c in text_calls),
  "tokens": {...},        # 아래 참고
  "final_url": state["page"]["url"],
  "final_title": state["page"]["title"],
}
```
- `tokens`: 각 decision의 `usage`와 text_call의 `usage`에서 **숫자인 값만 키별로 합산**한다. TypeSafe와 DeepSeek의 usage 키 이름이 다를 수 있으므로 `{"typesafe": {...}, "text": {...}}`로 나눠 보관한다. 키 이름을 추측해 하드코딩하지 않는다. 첫 실제 실행 후 trace를 열어 어떤 키가 오는지 확인하고, 문서의 비교표에는 실제로 존재하는 키만 쓴다.

**`exit_code(status)`**
- `"done"` → 0
- `"blocked"` → 2
- 예외(`ValueError`/`RuntimeError`: 예산 초과, 모델 오류) → 3 (`main`에서 처리)

**`main(argv)`**
- 인자: `[--start-url URL] [--trace-dir DIR] [--quiet] [--keep-open] -- <goal ...>`. `--` 뒤의 토큰들을 공백으로 이어 goal로 쓴다.
- `trace-dir` 기본값: `artifacts/via_runs/<YYYYmmdd-HHMMSS>/`
- 흐름:
  1. `url = start_url(goal, args.start_url)`
  2. `with Agent(url, goal) as agent:` (`--keep-open`이면 `close()` 생략. flights 예제의 `--keep-open`과 같은 의미)
  3. `for state in agent.run():` 새로 추가된 history 항목마다 `step_line` 출력(`--quiet`이면 생략). 반드시 `flush=True`.
  4. 예외가 나면 잡아서 status를 `"error"`, 메시지를 기록하고 exit 3.
  5. `finally`에서 `agent.snapshot()`과 `summarize()`를 `trace.json`에 저장한다. screenshot 필드는 제외한다(`page.screenshot`을 `None`으로).
  6. 마지막 줄 **하나**는 음성으로 읽힐 결과다:
     - done: `RESULT done · {total_ms/1000:.1f}s · {actions} actions · {final_title}`
     - blocked: `RESULT blocked · {이유}` (마지막 history action 또는 "no supported action")
     - error: `RESULT error · {메시지}`
  7. `return exit_code(...)`. 스크립트 진입점은 `raise SystemExit(main())`.
- 주의: `cli.rs`는 **누적 stdout 전체**를 `Result`로 쓴다. 음성으로 너무 길게 읽히지 않도록 VIA에 붙일 때는 `--quiet`로 등록해 RESULT 줄만 남긴다(B단계 메모). 사람이 볼 때는 기본값(스텝 출력)을 쓴다.

**`pyproject.toml`**: `[project.scripts]`에 `jev-via = "jev_ultrafast.via:main"`을 추가한다. 그러면 VIA에서 `uv run jev-via --quiet -- <goal>`로 부를 수 있다. `main`은 int를 반환하므로 console script의 exit code로 그대로 쓰인다.

## 4. 작업 2: `tests/test_via.py` (유료 API 호출 금지)

기존 `tests/test_agent.py`처럼 `pytest`와 `unittest.mock`을 쓴다.

1. `start_url`
   - `"네이버에서 서울 날씨"` → naver
   - `"search Google for X"` → google
   - 일치 없음 → google
   - `explicit="en.wikipedia.org"` → `https://en.wikipedia.org`
   - `explicit="javascript:alert(1)"` → `ValueError`
2. `step_line`: TYPE_TEXT 항목(텍스트와 text ms 포함), CLICK 항목(text 없음), 개행이 섞인 라벨 → 한 줄.
3. `summarize`: 가짜 state(decision 3개 중 1개는 stale, text_call 1개, usage 숫자와 문자열 혼합)에서 `typesafe_calls == 3`, 숫자 usage만 합산되는지.
4. `main` 흐름: `monkeypatch`로 `jev_ultrafast.via.Agent`를 가짜 클래스로 바꾼다. 가짜 클래스는 `run()`이 미리 만든 state 2개를 yield하고, `snapshot()`, `close()`, 컨텍스트 매니저를 구현한다.
   - done → 반환 0, stdout 마지막 줄이 `RESULT done`으로 시작
   - blocked → 2
   - `run()`이 `RuntimeError` raise → 3이고 `RESULT error`
   - `tmp_path`에 `trace.json`이 생기고 screenshot이 없음
   - `capsys`로 stdout 확인

## 5. 작업 3: `scripts/record_via.py`

`record_flights.py`를 **복사해서 일반화**한다. 구조는 그대로 둔다.

- 인자: `record_via.py OUTPUT_DIR [--start-url URL] -- <goal>`
  - 기본 goal: `네이버에서 서울 날씨를 검색하고 날씨 결과가 보이면 멈춰.`
- `url = via.start_url(goal, start_url)`로 시작 URL을 정하고 `Agent(url, goal)`을 연다.
- 첫 프레임, screencast 스레드, epoch 재설정, `finally`의 drain과 stop은 `record_flights.py`와 **동일하게** 한다(원래 속도 유지, 대기 시간 포함).
- 루프 안에서 새 history 항목마다 `via.step_line()`을 출력한다. 녹화 중에도 VIA와 같은 로그가 나오게 하기 위함이다.
- `finally`에서 `state.json`에 다음을 넣는다.
  - `snapshot()`
  - `summary = via.summarize(state)`
  - `final_page = agent.browser.observe(screenshot=False)`
  - `verification = verify_naver_weather(final_page)`
  - `recording_errors`, `source_hashes` (flights와 동일)
  - `start_url`, `goal`
- **독립 검증 `verify_naver_weather(page)`** (이 스크립트 안에만 둔다. policy가 아니다):
  - `host`: `urlparse(page["url"]).hostname == "search.naver.com"`
  - `query`: URL의 `query` 파라미터(`parse_qs`)에 `"날씨"`와 `"서울"`이 모두 들어 있음
  - `weather_visible`: `page["text"]`에 `"°"`가 있고, `"날씨"`가 있음
  - 반환: `{"passed": all(...), "checks": {...}}`
  - 실패하면 `SystemExit("Final-page verification failed")`. 렌더러는 검증에 통과한 녹화만 받는다.
- 네이버는 검색창이 자동완성 팝업을 띄울 수 있다. 이것은 jev가 처리할 일이니 스크립트에서 개입하지 않는다. 실패하면 goal 문구만 조정하고 다시 녹화한다(시도 횟수와 실패 로그는 문서에 남긴다).

## 6. 작업 4: `scripts/render_via.py`

`render_demo.py`를 기반으로 하되 한글 폰트와 비교 패널을 넣는다.

- 입력: `render_via.py SOURCE_DIR`. `assert state["verification"]["passed"] and not state["recording_errors"]`.
- 캔버스: 1536×1000, 30fps. 프레임 수 = `round((end + 500) * 30 / 1000)`. 원래 속도를 유지하고, 끝에 0.5초만 정지한다(AGENTS.md: "Keep demonstration footage at its original speed").
- **좌측 브라우저 영역**: `render_demo.py`와 같은 크롬 프레임에 screencast를 붙인다. 주소줄 텍스트는 해당 시점 history의 `url` 호스트를 쓴다.
- **우측 패널 (x ≥ 1180)**:
  1. `JEV FASTWEB` 라벨과 경과 시간 `{t/1000:05.2f}` (Menlo 52)
  2. 스텝 목록: `executed_ms <= t`인 history를 최대 7개 표시. 각 줄은 `✓ {operation} {action[:14]}`와 오른쪽의 `{latency_ms} ms`. 한글이 있으므로 AppleSDGothicNeo를 쓴다.
  3. 비교 막대 (가장 눈에 띄는 요소):
     - 제목 `같은 종류의 작업 · ARGO 기록값과 비교`
     - 막대 A: `jev (이 영상)`. 길이는 `t / 48000` 비율이고 초록색이다. 실시간으로 자란다.
     - 막대 B: `ARGO desktopweb (기록, 2026-09-08 Naver)`, 48.0 s, 회색, 고정.
     - 막대 C: `ARGO VIA 날씨 질의 (기록)`, 15.8 s, 회색, 고정.
     - 막대 아래 작은 글씨로 `ARGO 값은 코드 주석/문서의 기록값이며 동일 조건 재측정이 아님`을 **반드시** 적는다.
  4. 하단: `TypeSafe {typesafe_calls}회 · 텍스트 LLM {text_calls}회 · 중앙 결정 {decision_ms_median} ms`. `t`까지의 누적값을 쓴다.
- 하단 진행선과 캡션은 `render_demo.py`와 같다. 캡션에는 텍스트 모델 이름(`state["text_calls"][0]["model"]`)을 쓴다.
- 출력:
  - `docs/via-demo.mp4`, `docs/via-demo.gif` (ffmpeg 인자는 `render_demo.py`와 동일)
  - `docs/via-comparison.png`: 1200×520 정지 이미지. 아래 §7의 표를 그린다.
  - stdout에 §7 마크다운 표를 출력한다(문서에 붙여 넣기용).
- 중간 PNG는 `SOURCE_DIR/video-frames/`에 쓴다(`exist_ok=False`, 재렌더할 때는 폴더를 지운다).

## 7. 비교표 (문서와 이미지 공통)

| 항목 | ARGO desktopweb (기록) | jev fastweb (실측) |
|---|---|---|
| 작업 | Naver 웹 턴 (2026-09-08) / VIA 날씨 질의 | 네이버 "서울 날씨" 검색 |
| 총 시간 | 48 s / 15.8–17.3 s | `{total_ms/1000:.1f} s` |
| 스텝 결정 방식 | 대형 LLM이 DSL 생성 | TypeSafe 선택 (operation+target 1요청) |
| 결정 1회 지연 | draft 3.1 s, 검증 8.2 s | 중앙값 `{decision_ms_median} ms` |
| 결정 1회 입력 토큰 | 60,647 / 92,571 | `{usage에 실제로 있는 키만}` 없으면 "—" |
| 모델 호출 | ReAct 턴 + 완료 검증 턴 (+재시도 최대 3) | TypeSafe `{typesafe_calls}` · 텍스트 `{text_calls}` |
| 완료 판정 | LLM 검증 턴 | 최종 페이지 독립 검증 (`verify_naver_weather`) |

출처: ARGO 수치는 `tinicore/src/agent/sub_agents/desktop_web.rs` 주석과 `docs/plans/VIA_EDGE_CATALOG_AND_DIRECT_MODE.md`이다. 표 아래에 "동일 조건 비교가 아님(기기, 네트워크, 과제 문구가 다름)"을 명시한다. jev 쪽 숫자는 반드시 이번 `state.json`의 값만 쓴다. 추정치를 쓰지 않는다.

## 8. 작업 5: ARGO 문서 `docs/plans/FASTWEB_JEV_FUSION.md`

- 위치: `~/workspaces/ARGO/docs/plans/`. sparse-checkout 범위라 이미 체크아웃되어 있고, 브랜치는 `via-dev-demo-mode`다.
- 내용: `docs/argo-fusion.md`의 §1, §2.3, §4(개선 지점 7개), §5(PoC 설계)를 ARGO 독자 기준으로 옮긴다.
  - 경로 표기는 ARGO 루트 기준으로 쓰고, jev 파일은 `jev-ultrafast/...`로 쓴다.
  - 맨 앞에 "상태: PoC A단계 완료 / B단계(argo-pc 등록) 미착수" 한 줄을 둔다.
- §7 비교표에 실측값을 채운다.
- 영상과 이미지는 **ARGO 저장소에 복사하지 않고** jev 저장소 경로(`jev-ultrafast/docs/via-demo.mp4`)로 링크한다. 대용량 바이너리를 ARGO에 넣지 않기 위함이다.
- B단계 등록 예시를 넣는다: `CliAgentConfig { id: "fastweb", program: "uv", base_args: ["run", "--directory", "<jev 경로>", "--env-file", ".env", "jev-via", "--quiet"], .. }`. 실제 필드명은 `via/via-agents/src/cli.rs`의 `CliAgentConfig`를 열어 맞춘다.
- ARGO에서도 commit하지 않는다.

## 9. 실행 순서와 완료 기준

```bash
cd ~/workspaces/jev-ultrafast
UV=~/Library/Python/3.12/bin/uv
# 1) 구현 후 정적 검사
$UV run ruff check . && $UV run pytest -q && node --check jev_ultrafast/static/app.js && $UV build
# 2) CLI 실측 (Chrome 원격 디버깅 켜진 상태)
$UV run --env-file .env jev-via -- "네이버에서 서울 날씨를 검색하고 날씨 결과가 보이면 멈춰."; echo "exit=$?"
# 3) 녹화 → 렌더
$UV run --env-file .env python scripts/record_via.py artifacts/via/naver-weather
$UV run python scripts/render_via.py artifacts/via/naver-weather
open docs/via-demo.mp4
```

완료 기준:
- [ ] ruff, pytest, node check, uv build가 모두 통과한다.
- [ ] `jev-via` 실측: exit 0, 마지막 줄이 `RESULT done …`, `trace.json`이 생성된다.
- [ ] 실패 경로: `--start-url https://example.com -- "로그인 버튼을 눌러"`처럼 불가능한 goal에서 exit ≠ 0이고 `RESULT blocked` 또는 `RESULT error`가 나온다.
- [ ] `record_via.py`의 검증이 passed이고 `recording_errors`가 비어 있다.
- [ ] `docs/via-demo.mp4`를 **직접 열어 확인**한다. 첫 프레임이 빈 화면이 아니고, 한글이 깨지지 않고(□ 없음), 비교 막대가 자라고, 끝이 결과 페이지다.
- [ ] 비교표의 jev 숫자가 `state.json`의 `summary`와 일치한다.
- [ ] ARGO 문서가 생성되고, 표에 실측값이 들어 있다.

## 10. 함정 (구현 중 자주 틀리는 것)

1. **DONE을 성공으로 보고하지 않는다.** 영상과 문서의 "성공"은 `verify_naver_weather` 결과만 근거로 한다.
2. **속도를 바꾸지 않는다.** 프레임 보간, 가속, 대기 구간 삭제 모두 금지.
3. **ARGO 수치는 "기록값"으로만 쓴다.** 동일 조건 비교처럼 쓰지 않는다. "N배 빠름" 같은 문구는 쓰지 않거나, 쓰더라도 조건 차이를 바로 옆에 붙인다.
4. `agent.run()` 예외는 제너레이터 밖으로 나온다. `for` 루프 전체를 `try`로 감싼다.
5. `step_line`은 **새로 추가된** history만 출력한다. 스텝마다 `len(history)`를 기억해 둔다.
6. `trace.json`에 스크린샷 base64를 넣지 않는다(수 MB).
7. screencast 스레드와 에이전트가 같은 CDP 세션을 쓴다. `record_flights.py`의 ack 처리와 `drain_events` 필터(`session_id`)를 그대로 유지한다.
8. `.env.example`을 스테이징하거나 커밋하지 않는다.
