<!-- standard-ai-workflow-kit: v1.6.0 -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: draft
- Updated: 2026-08-27
- Related docs: [Project Profile](../../../docs/PROJECT_PROFILE.md), [Work Backlog](./work_backlog.md)

## Current Focus

M-001 컨셉 정리 진행 중. 컨셉 노트 v0.9 (`ai-workflow/wiki/concepts/agentic-service-desk-concept.md`)
가 SSOT 이며, 확정 결정 12가지는 §0.2, 미결 쟁점 33건은 §15 에 있다. 다음 세션은
**항목별 고도화 라운드**로 들어간다 — 우선순위는 §0.4 의 6건.

`PURPOSE.md` 는 v10 이고 컨셉 노트와 정합을 맞춰 두었다. 컨셉이 바뀌면 **두 문서를 함께**
고쳐야 한다.

## Work Status

- TASK-2026-08-27-main-001 — M-001 WBS-1.2 — 컨셉 노트 작성: in_progress
-
- TASK-2026-08-27-main-002 — M-001 WBS-1.1 — PURPOSE.md 4-element 채움: done
- TASK-2026-08-27-agentic-service-desk-001 — 표준 AI 워크플로우 초기 도입: done

## Key Changes

- workflow_kit v1.6.0 부트스트랩 + git init(main) + `.gitignore` + 초기 커밋 `63ccdcc`
- `PURPOSE.md` v1→v10. v1 의 "사내 IT 헬프데스크" 가설은 사실과 달라 폐기했다
- 컨셉 노트 v0.1→v0.9, 9라운드. 무게중심이 QnA 응답 → **지식베이스**로 이동
- `ai-workflow/wiki/index.md` 신설 (R4 anchor 기반, AI agent query 진입점)
- M-001 진척 0.5 (WBS-1.1 done / WBS-1.2 in_progress)

## Next Actions

- [ ] O1 — 모 시스템의 도메인과 규모 확정 (다른 쟁점 다수가 여기 매달려 있다)
- [ ] O17 — QnA 항목과 티켓이 하나인가 둘인가 (데이터 모델 전체가 갈린다)
- [ ] O13 — 검수(5단계 중 4단계) 주체 확정
- [ ] O3 — 명시적 해결로 인정할 구체적 신호 (O2 연동 형태에 종속)
- [ ] O27 — 사람이 지식 항목을 직접 고치는가 (출처 추적 유지 여부)
- [ ] O25 — 운영자 인원과 역할 분리
- [ ] `docs/PROJECT_PROFILE.md` 의 TODO 플레이스홀더 (설치/실행/테스트 명령, 이해관계자) — 스택 확정 후

## Risks & Blockers

- 컨셉 노트의 **전제 2·3·7 은 모 시스템에 대한 요구사항**이다 (QnA 의 게재자 계정 구분,
  후속 답글 가능, 콘텐츠 게재 자리). 하나라도 깨지면 §5.3 · §6 · §7 을 다시 설계해야 하므로
  M-002 요구사항 단계에서 최우선 확인한다.
- 기술 선택(언어·DB·검색·LLM 런타임)은 O1·O9·O11 이 열려 있어 M-003 으로 미뤄 두었다.
  이 상태에서 구현을 시작하면 근거 없는 선택이 된다.
