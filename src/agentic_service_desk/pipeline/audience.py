"""대상별 정제 — 같은 답을 두 사람에게 (FR-61, WBS-5.7.2).

질의 콘솔(`/ask`)이 내는 초안은 **진술과 강도의 목록**이라 운영자가 근거를 보기에는
좋지만 그대로 누구에게 건네기에는 맞지 않는다. 질문을 한 사람에게는 내부 경로와
식별자가 소음이고, 원인을 찾는 개발자에게는 그것이 정확히 필요한 것이다.

## 고객용은 게재 경로 안에 있다

**정제하지 않으면 내부 구현이 그대로 나간다.** 초안은 지식의 말로 쓰이므로 파일
경로와 코드 식별자가 들어가고, `Draft.body` 가 곧 게재 본문이다 — 질문한 사람이
`dashboard_api/app.py` 를 받는다.

그래서 고객용 정제는 파이프라인의 4단계이고 **검수 앞**이다. 뒤에 두면 검수가 본
글과 나가는 글이 달라져 FR-20 이 무너진다.

**개발자용은 나가지 않는다.** 모 시스템의 쓰기 표면은 답변 게재 하나뿐이고 그
독자는 질문자다 — 개발자용은 운영자 화면 전용이다.

## 새 사실을 더하지 않는다

정제가 사실을 보태면 그것은 정제가 아니라 **두 번째 생성**이고, 검수를 지나지 않은
채 사람에게 건네질 글에 근거 없는 말이 섞인다. 그래서 지시가 그것을 금하고,
`check_ungrounded_numbers`(검수 P1)를 두 산출에 그대로 걸어 **기계적으로도 한 번
더 본다.** 통과가 옳음을 보장하지는 않는다 — 수치 대조는 준기계적일 뿐이다(§5.5.1).

## 개수를 지키는 것이 규약이다

고객용 정제는 **진술 하나를 진술 하나로** 다시 쓴다. 글 전체를 새로 쓰면 진술과
강도의 대응이 끊겨 ADR-007 의 강도 표시가 어디에도 붙지 못하고, 개수가 달라지면
합치거나 지어낸 것이라 **사실을 더하지 않았다는 전제가 깨진다.** 어기면 정제를
버리고 원본을 내보낸다 — 어긋난 글보다 투박한 글이 낫다.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_service_desk.knowledge.search import Hit
from agentic_service_desk.pipeline.answer import Draft, Statement
from agentic_service_desk.pipeline.review import ReviewInput, check_ungrounded_numbers

CUSTOMER = "customer"
DEVELOPER = "developer"


@dataclass(frozen=True)
class Rendering:
    """한 대상을 위한 글 하나."""

    audience: str
    text: str
    ungrounded: tuple[str, ...] = ()
    """근거 원문에 없는 수치 (검수 P1 과 같은 대조).

    **비어 있는 것이 옳음의 증거는 아니다.** 수치만 보므로 근거 밖 고유명사나
    과장은 이 검사를 지난다.
    """


def build_developer_prompt(
    question: str, draft: Draft, hits: list[Hit], language: str = "ko"
) -> str:
    """개발자용 정제 지시.

    **고객용은 여기서 만들지 않는다** — 파이프라인 4단계가 이미 만들었고 그것이
    게재 본문이다. 콘솔이 다시 만들면 화면에 보이는 글과 나가는 글이 갈린다.

    개발자용은 나갈 곳이 없으므로(모 시스템의 쓰기 표면은 답변 게재 하나뿐이고
    그 독자는 질문자다) 운영자 화면 전용이다 — 원본 진술의 경로와 식별자를
    **살리는 쪽**으로 다시 쓴다.
    """
    grounds = "\n".join(
        f"--- {h.item.id}: {h.item.title} ---\n{h.item.body}"
        for h in hits
        if h.item.id in draft.grounding
    )
    statements = "\n".join(f"- [{s.confidence}] {s.text}" for s in draft.statements)
    boundary = (
        "\n".join(f"- {u}" for u in draft.unanswered)
        if draft.unanswered
        else "(밝힌 경계 없음)"
    )
    tongue = "한국어" if language == "ko" else "영어"
    return f"""아래 초안을 **원인을 찾는 개발자**가 읽을 글로 다시 쓴다.

**{tongue} 로 쓴다** — 식별자와 경로는 원문 그대로 둔다.

파일 경로 · 식별자 · 함수명 · 에러 문구를 **그대로 살리고**, 확인할 지점을
순서대로 적는다.

**새 사실을 더하지 않는다.** 초안과 근거 원문에 없는 것은 쓰지 않는다 — 수치도,
고유명사도, 원인 추정도. 다시 쓰는 것이지 새로 답하는 것이 아니다.

`[추론]` · `[근거 얇음]` 진술은 **단정하지 않는다** — "…로 보인다"처럼 그
불확실성을 남긴다.

출력은 **JSON 하나만** 낸다.

{{"developer": "…"}}

질문: {question}

초안 진술:
{statements}

밝힌 경계:
{boundary}

근거 원문:
{grounds}"""


def render_of(audience: str, text: str, review: ReviewInput) -> Rendering | None:
    """글 하나를 정제 결과로 싼다. **근거 밖 수치를 함께 센다** (검수 P1 과 같은 대조).

    비어 있으면 만들지 않는다 — 빈 칸을 보여 주는 것보다 없다고 말하는 편이 정직하고,
    화면이 그 자리를 비워 둔다.
    """
    text = (text or "").strip()
    if not text:
        return None
    verdict = check_ungrounded_numbers(
        ReviewInput(
            draft_body=text,
            grounding=review.grounding,
            source_text=review.source_text,
        )
    )
    return Rendering(audience=audience, text=text, ungrounded=_numbers_of(verdict))


def _numbers_of(verdict) -> tuple[str, ...]:  # noqa: ANN001
    """반려 사유에서 근거 밖 수치를 꺼낸다. 없으면 빈 것."""
    if verdict is None:
        return ()
    detail = getattr(verdict, "detail", "") or ""
    return tuple(part.strip() for part in detail.split(":")[-1].split(",") if part.strip())


def build_customer_prompt(
    question: str, draft: Draft, hits: list[Hit], language: str = "ko"
) -> str:
    """고객용 정제 지시 — **진술 하나를 진술 하나로.**

    검수와 같은 입력 규율이다: 초안과 근거 원문만 준다.

    **언어를 못 박는다** (FR-17). 1단계가 판정한 언어로 3단계가 썼는데, 정제가
    조용히 다른 언어로 옮기면 **질문한 사람이 읽을 수 없는 답이 나간다.**
    2026-09-03 라이브에서 한국어 질문의 정제 결과가 영어로 나오며 드러났다.
    """
    tongue = "한국어" if language == "ko" else "영어"
    grounds = "\n".join(
        f"--- {h.item.id} ---\n{h.item.body}"
        for h in hits
        if h.item.id in draft.grounding
    )
    numbered = "\n".join(
        f"{i}. [{s.confidence}] {s.text}" for i, s in enumerate(draft.statements, start=1)
    )
    n = len(draft.statements)
    return f"""아래 진술들을 **질문한 사람이 읽을 말**로 다시 쓴다.

**{tongue} 로 쓴다.** 질문한 사람의 언어이며, 바꾸면 그 사람이 읽을 수 없다.

이 글은 질문자에게 그대로 나간다. 내부 파일 경로 · 코드 식별자 · 함수명 · 커밋
해시 · 변수명을 쓰지 않는다 — 무엇을 하면 되는지, 무엇이 원인인지를 그 사람의
말로 적는다.

**새 사실을 더하지 않는다.** 아래 진술과 근거 원문에 없는 것은 쓰지 않는다 —
수치도, 고유명사도, 원인 추정도. 다시 쓰는 것이지 새로 답하는 것이 아니다.

`[추론]` · `[근거 얇음]` 이 붙은 진술은 **단정하지 않는다** — "…로 보인다"처럼
그 불확실성을 남긴다.

**진술 {n}개를 그대로 {n}개로 낸다.** 합치거나 나누지 않는다 — 순서도 같다.

출력은 **JSON 하나만** 낸다.

{{"statements": [{n}개의 문자열]}}

질문: {question}

진술:
{numbered}

근거 원문:
{grounds}"""


def parse_customer(payload: dict, draft: Draft) -> tuple[Statement, ...]:
    """정제된 진술을 읽는다. **개수가 다르면 통째로 버린다.**

    합치거나 나눈 것은 "다시 쓴 것"이 아니라 새로 쓴 것이고, 강도를 물려줄 짝도
    사라진다. 어긋난 글보다 투박한 원본이 낫다.
    """
    texts = payload.get("statements")
    if not isinstance(texts, list) or len(texts) != len(draft.statements):
        return ()
    out: list[Statement] = []
    for text, base in zip(texts, draft.statements, strict=True):
        text = str(text or "").strip()
        if not text:
            return ()
        # **강도와 근거는 물려받는다** — 정제는 판정을 다시 하지 않는다.
        out.append(Statement(text=text, confidence=base.confidence, grounding=base.grounding))
    return tuple(out)
