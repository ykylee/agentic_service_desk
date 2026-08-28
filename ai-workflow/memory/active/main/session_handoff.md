<!-- standard-ai-workflow-kit: v1.6.0 -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: draft
- Updated: 2026-08-28
- Related docs: [Project Profile](../../../docs/PROJECT_PROFILE.md), [Work Backlog](./work_backlog.md)

## Current Focus

**M-001(컨셉) 완료. M-002(요구사항)로 넘어왔다.** 다음 산출물은 `docs/REQUIREMENTS.md`.

컨셉은 **v1.0**(최종 리뷰 완료), `ai-workflow/wiki/concepts/` 아래 **8개 문서**다. 허브는
`agentic-service-desk-concept.md` 이고 **§0 TL;DR → 문서 지도** 순으로 읽는다.

- **절 번호(§)는 8개 문서를 넘어 전역으로 유일**하다. 새 절을 더할 때 **번호를 다시
  매기지 않는다** — 상호참조가 전부 § 기반이라 재번호는 문서 전체를 깨뜨린다.
- 확정 결정 **55가지**(D1~D55)는 §0.2, 실패 방식 15종은 §0.3, 남은 미결의 분류는 §0.4.
- **컨셉 단계에서 판단할 것은 모두 닫혔고 최종 리뷰까지 마쳤다** (닫힌 쟁점 25건). 남은 31건은
  M-002 요구사항 / 실데이터 임계값 / M-003 설계 / 운영 중 확인으로 갈린다.
- 컨셉이 바뀌면 **8개 문서 + PURPOSE.md 정합을 함께** 맞춘다.

## Work Status

-
-
- TASK-2026-08-27-main-001 — M-001 WBS-1.2 — 컨셉 노트 작성: done
- TASK-2026-08-27-main-002 — M-001 WBS-1.1 — PURPOSE.md 4-element 채움: done
- TASK-2026-08-27-agentic-service-desk-001 — 표준 AI 워크플로우 초기 도입: done

## Key Changes

- 컨셉 27라운드 + 최종 리뷰 완료 — v0.1 → **v1.0**, 8개 문서 2575줄, 확정 결정 55가지
- 최종 리뷰에서 모순 5건 해소 — **국면(1~3)과 단계(S0~S5)는 다른 축**임을 명시, D31 은 S3 부터 적용, D42 가 D41 을 뒤집지 않는 이유, 콘텐츠 언어 비대칭 감수, §10 핵심 루프 재작성(전건 티켓·승격 세 경로·수동 등록 반영)
- M-001 done, M-002 in_progress 로 전환 (roadmap issues 0)
- `PURPOSE.md` v28 (beta) — 컨셉과 정합
- 저장소: https://github.com/ykylee/agentic_service_desk (public)

## Next Actions

- [ ] **M-002 WBS-2.1 — `docs/REQUIREMENTS.md` 작성**
- [ ] 그 안에서 **최우선은 §9.8.1 의 API 표면 여섯** — 질문 · 답변(작성자 계정 포함) ·
      후속 답글 · 해결 표시 상태 · 답변 게재 · 콘텐츠 게재. 컨셉의 전제 2·3·7 이 실은
      이 목록의 다른 표현이다
- [ ] M-002 에서 함께 정할 미결: O14(정정 경로) · O15(봇 답변자 표기) ·
      O6(개인정보 보관 범위) · O45(보존 기간)
- [ ] O41(커밋 위생)은 모 시스템 저장소 히스토리 표본이 있으면 지금도 확인 가능
- [ ] `docs/PROJECT_PROFILE.md` 의 TODO 플레이스홀더 — 스택이 정해지는 M-003 이후

## Risks & Blockers

- **API 표면이 좁으면 기능이 소리 없이 죽는다** (D34). 특히 답변의 작성자 계정(전제 2)이
  빠지면 §5.3 되먹임 차단이 무력해지는데, 파이프라인은 계속 돌고 지식도 계속 자란다 —
  오답을 섞은 채로. M-002 에서 가장 먼저 확인할 것.
- **1인 겸업이 처리량 상한**이다 (§8.4, §8.6). 기능 도입은 여섯 단계(S0~S5)로 나누고,
  앞 단계 대기열이 밀려 있으면 다음을 켜지 않는다 (§1.5.4).
- 기술 선택(언어·DB·검색·LLM 런타임)은 M-003 몫이다. 지금 고르면 근거가 없다 (§9.1).
