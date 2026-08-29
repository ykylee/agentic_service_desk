<!-- standard-ai-workflow-kit: v1.6.0 -->
<!-- standard-ai-workflow-kit-fork: 프로젝트가 소유한다 — v1.6.0 에서 갈라져 §1~§5 를 손으로 채웠다 -->

# Project Workflow Profile

- 문서 목적: 프로젝트 특화 규칙과 실행/검증 기준을 정의한다.
- 범위: 프로젝트 개요, 문서 구조, 기본 명령, 검증 포인트, 예외 규칙
- 대상 독자: 개발자, 운영자, AI agent, 프로젝트 온보딩 담당자
- 상태: active
- 최종 수정일: 2026-08-30
- 관련 문서: [컨셉 허브](../ai-workflow/wiki/concepts/agentic-service-desk-concept.md) · [요구사항](./REQUIREMENTS.md) · [설계](./architecture/README.md) · [구현 로드맵](./IMPLEMENTATION_ROADMAP.md)

## 1. 프로젝트 개요
- 프로젝트명: Agentic Service Desk
- 프로젝트 목적: 모 시스템(사내 업무 시스템)의 소스코드와 QnA 이력을 지식베이스로 구축하고, 그 지식으로 답변과 콘텐츠를 자동 생산·검수·게재하며 해결까지 추적한다
- 주요 이해관계자: 운영자 1인(겸업, 모 시스템 개발자와 동일인) · 모 시스템 이용자(사내 2,000명 → 7,000명 예정)

## 2. 문서 구조 (Path)
- 문서 위키 홈: `ai-workflow/wiki/index.md`
- 운영 문서 홈: ai-workflow/memory/active/
- 백로그 위치: `ai-workflow/memory/active/<branch>/backlog/`
- 세션 인계 문서: `ai-workflow/memory/active/<branch>/session_handoff.md`
- 환경 기록 위치: `ai-workflow/memory/active/project_status_assessment.md`

## 3. 기본 명령 (Commands)
- 설치: `uv sync`
- 로컬 실행: `uv run asd-web` (온라인 · http://127.0.0.1:8000) / `uv run asd-worker` (배치)
- 빠른 테스트: `uv run pytest -q`
- 격리 테스트: `uv run pytest tests/ -q`
- 실행 확인: `uv run asd-web` 후 `curl -s localhost:8000/health`
- 하네스 동기화: `uv run asd sync-harness` (`.env` → `~/.pi/agent/models.json`, ADR-009)

## 4. 검증 포인트 (Validation)
- 코드 변경: `uv run pytest -q` 통과. 경계(어댑터·산출물 필터)를 건드리면 해당 NFR 을 함께 확인한다
- 문서 변경: 컨셉 8편의 § 참조 정합, 닫힌 쟁점 오참조 0, 요구사항↔컨셉 추적성 유지
- UI 변경: 단계별 대기열 노출(FR-59)이 지켜지는지 확인
- 배포/운영: 도입 단계(S0~S5)를 올릴 때는 앞 단계 대기열이 밀려 있지 않은지 먼저 본다 (D51)

## 5. 예외 규칙 (Policy)
- 병합: 지식베이스는 additive merge — 충돌 시 폐기하지 않고 양쪽을 남기고 모순 표시 (D38)
- 승인: 대외 게재(S3 이후)와 도입 단계 상향은 운영자 승인. 국면 후퇴는 자동 (D24)
- 제약: **모 시스템 소스코드를 외부로 전송하지 않는다** (NFR-1). LLM·임베딩 모두 로컬
- 기타: 컨셉 문서의 절 번호(§)는 8개 문서를 넘어 전역으로 유일하다. **재번호 금지** — 상호참조가 전부 § 기반이다

## 다음에 읽을 문서
- [세션 인계 문서](../ai-workflow/memory/active/main/session_handoff.md)
- [작업 백로그](../ai-workflow/memory/active/main/backlog/)
