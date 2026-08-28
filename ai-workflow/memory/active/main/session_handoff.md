<!-- standard-ai-workflow-kit: v1.6.0 -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: draft
- Updated: 2026-08-28
- Related docs: [Project Profile](../../../docs/PROJECT_PROFILE.md), [Work Backlog](./work_backlog.md)

## Current Focus

**M-001(컨셉)·M-002(요구사항) 완료. M-003(설계)로 넘어왔다.** 다음 산출물은 `docs/architecture`.

`docs/REQUIREMENTS.md` 가 컨셉에서 끌어낸 요구를 담는다 — **XR 7 · FR 50 · NFR 9 · CO 8 · PO 4**,
모든 항목이 컨셉 결정(D)에 추적되고 검증 방법을 갖는다. 설계는 이 문서의 FR/NFR 를 만족시키는
기술 선택이며, **CO-8 이 여기서 풀린다** (언어·DB·검색·LLM 런타임).

컨셉은 **v1.0**(최종 리뷰 완료), `ai-workflow/wiki/concepts/` 아래 **8개 문서**다. 허브는
`agentic-service-desk-concept.md` 이고 **§0 TL;DR → 문서 지도** 순으로 읽는다.

- **절 번호(§)는 8개 문서를 넘어 전역으로 유일**하다. 새 절을 더할 때 **번호를 다시
  매기지 않는다** — 상호참조가 전부 § 기반이라 재번호는 문서 전체를 깨뜨린다.
- 확정 결정 **55가지**(D1~D55)는 §0.2, 실패 방식 15종은 §0.3, 남은 미결의 분류는 §0.4.
- **컨셉 판단 완료 + 최종 리뷰 완료** (닫힌 쟁점 **28건**). 남은 28건은
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
- **M-002 완료** — `docs/REQUIREMENTS.md` 작성. 요구사항 단계에서 **컨셉의 누락을 발견**했다: API 표면에 **답변 수정(XR-7)이 없어** 정정 경로가 성립하지 않았다. 컨셉 §9.8.1 을 일곱 표면으로 정정
- O14(정정 경로)·O15(봇 표기)·O6(개인정보 범위) 닫힘. O45 는 원칙만 확정(구체 기간은 사내 정책)
- 최종 리뷰에서 모순 5건 해소 — **국면(1~3)과 단계(S0~S5)는 다른 축**임을 명시, D31 은 S3 부터 적용, D42 가 D41 을 뒤집지 않는 이유, 콘텐츠 언어 비대칭 감수, §10 핵심 루프 재작성(전건 티켓·승격 세 경로·수동 등록 반영)
- M-001 done, M-002 in_progress 로 전환 (roadmap issues 0)
- `PURPOSE.md` v28 (beta) — 컨셉과 정합
- 저장소: https://github.com/ykylee/agentic_service_desk (public)

## Next Actions

- [ ] **M-003 WBS — `docs/architecture` 작성** (기술 선택: 언어·DB·검색·LLM 런타임)
- [ ] **먼저 사용자 확인이 필요한 4건** (`docs/REQUIREMENTS.md` §7)
      1. XR-7(답변 수정) 가능 여부 — 불가하면 PO-1 정정 방식을 바꿔야 한다
      2. 보존 기간 (사내 개인정보 정책)
      3. **XR-2 작성자 계정 노출** — 대안이 없는 유일한 항목이다
      4. O41 커밋 위생 (저장소 히스토리 표본)
- [ ] M-003 에서 풀리는 미결: O11(입도)·O31(검색)·O9(모델)·O10(소스 보유)·O46(버전 단위)·
      O33(일관성)·O32(자원 배분)·O28(알림)·O55(등록 부담)·O51(불확실성 표시)
- [ ] `docs/PROJECT_PROFILE.md` 의 TODO 플레이스홀더 — 스택이 정해지면

## Risks & Blockers

- **API 표면이 좁으면 기능이 소리 없이 죽는다** (D34). 요구사항 §2.2 가 표면별 대안과
  대가를 정리했는데, **대안이 없는 항목은 XR-2(작성자 계정) 하나**다 — 없으면 봇 답변을
  영구 배제해야 하고 지식 성장 경로 B 가 통째로 막힌다. 설계 전에 확인해야 한다.
- **1인 겸업이 처리량 상한**이다 (§8.4, §8.6). 기능 도입은 여섯 단계(S0~S5)로 나누고,
  앞 단계 대기열이 밀려 있으면 다음을 켜지 않는다 (§1.5.4).
- 기술 선택(언어·DB·검색·LLM 런타임)은 M-003 몫이다. 지금 고르면 근거가 없다 (§9.1).
