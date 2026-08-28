<!-- standard-ai-workflow-kit: v1.6.0 -->

# Wiki Index — Agentic Service Desk

- 문서 목적: 이 저장소 wiki 의 anchor 기반 카탈로그. AI agent 가 query 시 **가장 먼저** 로드한다 (R4).
- 범위: 페이지 목록 · 1줄 요약 · 상태
- 대상 독자: AI agent, 저장소 maintainer
- 상태: active
- 최종 수정일: 2026-08-28
- 관련 문서: [`PURPOSE.md`](../memory/active/PURPOSE.md) (방향성), [roadmap](../memory/active/roadmap/index.md)

> 이 파일은 **anchor 기반**이다. 자유 산문을 쓰지 않는다.

## concepts  {#concepts}

컨셉은 8개 문서로 나뉜다. **절 번호(§)는 문서를 넘어 전역으로 유일**하므로, 본문의
`§N` 참조는 아래 표에서 문서를 찾아 읽는다. 전체 지도는 허브의
[문서 지도](./concepts/agentic-service-desk-concept.md#doc-map)에 있다.

| 절 | 문서 |
|---|---|
| §0 · §1 · §10 | [`agentic-service-desk-concept`](./concepts/agentic-service-desk-concept.md) — **허브, 먼저 읽는다** |
| §2 · §3 · §4 | [`knowledge-sources`](./concepts/knowledge-sources.md) |
| §5 · §7 | [`production-pipeline`](./concepts/production-pipeline.md) |
| §6 | [`qna-and-tickets`](./concepts/qna-and-tickets.md) |
| §8 | [`operator-dashboard`](./concepts/operator-dashboard.md) |
| §9 | [`system-architecture`](./concepts/system-architecture.md) |
| §11~§14 | [`concept-reference`](./concepts/concept-reference.md) |
| §15 · §16 | [`open-issues`](./concepts/open-issues.md) |

### [[concepts/agentic-service-desk-concept]]  {#agentic-service-desk-concept}

- 상태: draft (라운드 27) — **컨셉 판단 완료**
- 요약: **컨셉 허브.** 한 줄 정의, 확정 결정 **55가지**(§0.2), 실패 방식 15종(§0.3), 남은 미결의 분류(§0.4), 다음 단계(§0.5), 모 시스템 프로파일·운영 국면·채택·도입 순서(§1), 전체 핵심 루프(§10).
- 먼저 읽을 곳: `§0 TL;DR` → `문서 지도`
- 관련: [`PURPOSE.md`](../memory/active/PURPOSE.md), [`roadmap M-001`](../memory/active/roadmap/M-001-concept.md)

### [[concepts/knowledge-sources]]  {#knowledge-sources}

- 상태: draft
- 요약: 지식의 두 원천(소스 저장소 = 코드 + 히스토리 / QnA 이력)과 교차 검증, 커버리지 공백과 세 후보의 처리(§2), llm-wiki·OKF 차용(§3), 에이전트 기반 ingest 와 Lint(§4).

### [[concepts/production-pipeline]]  {#production-pipeline}

- 상태: draft
- 요약: 모든 산출물의 공통 5단계(분석·조회·생성·검수·게재), 자동 게재의 위험 W1~W3, 되먹임 차단과 해결 등급, **판단할 수 없는 것은 답하지 않는다**(§5), 콘텐츠 제작과 타입 확장(§7).

### [[concepts/qna-and-tickets]]  {#qna-and-tickets}

- 상태: draft
- 요약: QnA 수명주기와 상태, 등급별 지표, QnA 항목과 티켓의 관계(별개 엔터티·전건 발행), 티켓 종결 기록(지식 항목의 초안), 답변 이력의 근거·출처·버전 고정(§6).

### [[concepts/operator-dashboard]]  {#operator-dashboard}

- 상태: draft
- 요약: 작업 대기열 여덟 지점과 방치 비용 기반 우선순위, 현황 화면 네 종, 운영자가 루프의 일부라는 것, 누가 지식을 고치는가의 모순(§8).

### [[concepts/system-architecture]]  {#system-architecture}

- 상태: draft
- 요약: 컴포넌트 지도와 책임, 저장소 분리(지식=파일 / 운영=DB), 실행 경로 분리(온라인 / 배치), 어댑터 격리, 규칙의 단일 집행 지점(§9).

### [[concepts/concept-reference]]  {#concept-reference}

- 상태: draft
- 요약: 액터(§11), 핵심 용어 사전(§12), 시스템 경계 — 소유하는 것과 아닌 것(§13), 컨셉이 서 있는 전제(§14).

### [[concepts/open-issues]]  {#open-issues}

- 상태: draft
- 요약: 미결 31건과 **닫힌 24건**. 컨셉 단계 판단은 모두 닫혔고, 남은 항목은 M-002 요구사항 / 실데이터 임계값 / M-003 설계 / 운영 중 확인으로 갈린다 (분류는 허브 §0.4).

## entities  {#entities}

*(아직 없음 — 구현이 시작되면 컴포넌트 단위로 추가한다)*

## decisions  {#decisions}

*(아직 없음 — 컨셉의 확정 결정 D1~D20 이 안정되면 ADR 로 승격한다)*

## patterns  {#patterns}

*(아직 없음)*

## queries  {#queries}

*(아직 없음)*

## 다음에 읽을 문서  {#next}

- [`ai-workflow/memory/active/PURPOSE.md`](../memory/active/PURPOSE.md) — 방향성 (4-element)
- [`ai-workflow/memory/active/roadmap/index.md`](../memory/active/roadmap/index.md) — 마일스톤과 SDLC 순서
- [`docs/PROJECT_PROFILE.md`](../../docs/PROJECT_PROFILE.md) — 프로젝트 메타
