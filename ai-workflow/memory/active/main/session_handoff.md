<!-- standard-ai-workflow-kit: v1.6.0 -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: draft
- Updated: 2026-08-27
- Related docs: [Project Profile](../../../docs/PROJECT_PROFILE.md), [Work Backlog](./work_backlog.md)

## Current Focus

M-001 컨셉 정리 진행 중. 컨셉 노트 **v0.12** (`ai-workflow/wiki/concepts/agentic-service-desk-concept.md`)
가 SSOT 이며, 확정 결정 **15가지**는 §0.2, 실패 방식 8종은 §0.3, 미결 쟁점 **37건**은 §15 에 있다.
문서가 982줄이므로 **§0 TL;DR 부터 읽는다.**

항목별 고도화 라운드 진행 중이며 O1 · O35 · O17 이 닫혔다. 다음은 §0.4 의 일곱 건.

`PURPOSE.md` 는 v10 이고 컨셉 노트와 정합을 맞춰 두었다. 컨셉이 바뀌면 **두 문서를 함께**
고쳐야 한다.

## Work Status

- TASK-2026-08-27-main-001 — M-001 WBS-1.2 — 컨셉 노트 작성: in_progress
-
- TASK-2026-08-27-main-002 — M-001 WBS-1.1 — PURPOSE.md 4-element 채움: done
- TASK-2026-08-27-agentic-service-desk-001 — 표준 AI 워크플로우 초기 도입: done

## Key Changes

- workflow_kit v1.6.0 부트스트랩 + git init(main) + `.gitignore` + 초기 커밋 `63ccdcc`
- `PURPOSE.md` v1→v13. v1 의 "사내 IT 헬프데스크" 가설은 사실과 달라 폐기했다
- 컨셉 노트 v0.1→v0.12, 12라운드. 무게중심이 QnA 응답 → **지식베이스**로 이동
- **O1 닫힘** — 모 시스템은 사내 업무 시스템, 중규모(5만~50만 줄) 다언어, 문서엔 일반화 기록
- **O35 닫힘** — 이용자 2천→7천명, 가동 1개월. 진짜 콜드 스타트이며 **운영 국면 3단계**(§1.3) 의 근거
- **O17 닫힘** — QnA 항목과 티켓은 **별개 엔터티**. 서로 다른 질문에 답하므로 동기화 대상이 아니다
- `ai-workflow/wiki/index.md` 신설 (R4 anchor 기반, AI agent query 진입점)
- M-001 진척 0.5 (WBS-1.1 done / WBS-1.2 in_progress)

## Next Actions

- [ ] O34 — 업무 규칙 공백을 티켓으로 보낼 것인가(A, 기본값), 운영 문서·설정을 세 번째 원천으로 들일 것인가(B)
- [ ] O40 — 티켓 종결 기록의 구조 (승격의 유일한 입력이다)
- [ ] O13 · O24 — 답변 검수 주체 / 콘텐츠 검수 강도
- [ ] O8 · O37 — 국면별 지표 임계 / FAQ 승격 임계 (이 규모에선 2~3회도 후보)
- [ ] O25 · O5 · O39 — 운영자 인원과 역할, 티켓 상태 집합
- [ ] O27 — 사람이 지식 항목을 직접 고치는가 (출처 추적 유지 여부)
- [ ] `docs/PROJECT_PROFILE.md` 의 TODO 플레이스홀더 (설치/실행/테스트 명령, 이해관계자) — 스택 확정 후

## Risks & Blockers

- 컨셉 노트의 **전제 2·3·7 은 모 시스템에 대한 요구사항**이다 (QnA 의 게재자 계정 구분,
  후속 답글 가능, 콘텐츠 게재 자리). 하나라도 깨지면 §5.3 · §6 · §7 을 다시 설계해야 하므로
  M-002 요구사항 단계에서 최우선 확인한다.
- 기술 선택(언어·DB·검색·LLM 런타임)은 O9·O11·O31 이 열려 있어 M-003 으로 미뤄 두었다.
  이 상태에서 구현을 시작하면 근거 없는 선택이 된다.
