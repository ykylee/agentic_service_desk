"""Agentic Service Desk.

모 시스템의 **소스 저장소(코드 + 히스토리)** 와 **QnA 이력** 을 지식베이스로 구축하고,
그 지식을 근거로 답변과 콘텐츠를 생산·검수·게재하며, 각 QnA 를 해결까지 추적한다.

설계 문서
    - 왜      : ai-workflow/wiki/concepts/  (8편, v1.0)
    - 무엇을  : docs/REQUIREMENTS.md
    - 어떻게  : docs/architecture/
    - 순서    : docs/IMPLEMENTATION_ROADMAP.md

패키지 구조가 곧 설계다. 각 하위 패키지의 docstring 이 그 경계가 무엇을 소유하고
어떤 요구사항의 지배를 받는지 밝힌다.
"""

__version__ = "0.1.0"
