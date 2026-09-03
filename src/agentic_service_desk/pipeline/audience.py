"""대상별 정제 — 같은 답을 두 사람에게 (FR-61, WBS-5.7.2).

질의 콘솔(`/ask`)이 내는 초안은 **진술과 강도의 목록**이라 운영자가 근거를 보기에는
좋지만 그대로 누구에게 건네기에는 맞지 않는다. 질문을 한 사람에게는 내부 경로와
식별자가 소음이고, 원인을 찾는 개발자에게는 그것이 정확히 필요한 것이다.

## 파이프라인을 건드리지 않는다

**정제는 콘솔 전용이다.** `AnswerPipeline` 은 실제 답변 경로(유입 → 검수 → 게재)와
같은 코드이므로, 거기에 대상별 산출을 넣으면 **게재되는 글의 모양이 바뀐다.**
이 모듈은 이미 나온 초안을 다시 쓸 뿐이고 파이프라인은 그대로다.

## 새 사실을 더하지 않는다

정제가 사실을 보태면 그것은 정제가 아니라 **두 번째 생성**이고, 검수를 지나지 않은
채 사람에게 건네질 글에 근거 없는 말이 섞인다. 그래서 지시가 그것을 금하고,
`check_ungrounded_numbers`(검수 P1)를 두 산출에 그대로 걸어 **기계적으로도 한 번
더 본다.** 통과가 옳음을 보장하지는 않는다 — 수치 대조는 준기계적일 뿐이다(§5.5.1).

## 여기서 나온 글은 게재물이 아니다

`publish_answer` 를 부르지 않고 아무것도 저장하지 않는다. 운영자가 이 글을 그대로
사람에게 전달하면 **검수 체계 밖으로 나가는 것**이며, 그것을 막는 장치는 없다 —
화면이 그 사실을 적을 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_service_desk.knowledge.search import Hit
from agentic_service_desk.pipeline.answer import Draft
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


def build_prompt(question: str, draft: Draft, hits: list[Hit]) -> str:
    """정제 지시. **초안과 근거 원문만 준다** — 검수와 같은 입력 규율이다."""
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
    return f"""아래 초안을 **두 대상**에게 맞게 다시 쓴다.

**새 사실을 더하지 않는다.** 초안과 근거 원문에 없는 것은 쓰지 않는다 — 수치도,
고유명사도, 원인 추정도. 다시 쓰는 것이지 새로 답하는 것이 아니다.

- `customer` — **질문한 사람에게 보낼 말.** 내부 파일 경로 · 식별자 · 커밋 해시 ·
  코드 기호를 쓰지 않는다. 무엇을 하면 되는지와, 모르는 부분은 모른다는 것을 적는다.
- `developer` — **원인을 찾는 사람에게 줄 말.** 파일 경로 · 식별자 · 에러 문구를
  그대로 살리고, 확인할 지점을 순서대로 적는다.

초안의 `[추론]` · `[근거 얇음]` 진술은 **어느 쪽에서도 단정하지 않는다** — "…로
보인다" 처럼 그 불확실성을 남긴다.

출력은 **JSON 하나만** 낸다.

{{"customer": "…", "developer": "…"}}

질문: {question}

초안 진술:
{statements}

밝힌 경계:
{boundary}

근거 원문:
{grounds}"""


def parse(payload: dict, review: ReviewInput) -> list[Rendering]:
    """모델 응답을 정제 둘로 읽는다.

    한쪽이 비어 오면 **그쪽은 만들지 않는다** — 빈 칸을 보여 주는 것보다 없다고
    말하는 편이 정직하고, 화면이 그 자리를 비워 둔다.
    """
    out: list[Rendering] = []
    for audience in (CUSTOMER, DEVELOPER):
        text = str(payload.get(audience) or "").strip()
        if not text:
            continue
        verdict = check_ungrounded_numbers(
            ReviewInput(
                draft_body=text,
                grounding=review.grounding,
                source_text=review.source_text,
            )
        )
        out.append(
            Rendering(
                audience=audience,
                text=text,
                ungrounded=_numbers_of(verdict),
            )
        )
    return out


def _numbers_of(verdict) -> tuple[str, ...]:  # noqa: ANN001
    """반려 사유에서 근거 밖 수치를 꺼낸다. 없으면 빈 것."""
    if verdict is None:
        return ()
    detail = getattr(verdict, "detail", "") or ""
    return tuple(part.strip() for part in detail.split(":")[-1].split(",") if part.strip())
