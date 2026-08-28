"""종결 기록 초안 생성 (FR-11, §6.5.4).

필드가 넷(필수)이면 종결이 무거워진다. 티켓(Q1)은 방치 비용이 높은 대기열이므로
처리가 느려지면 그 자체가 문제다. 그래서 **초안은 에이전트가 채우고 사람은 확인하고
고친다** — §4 의 "지식은 에이전트가 짓는다"와 같은 형태이며, 사람의 역할은 작성이
아니라 판정이다.

## 무효화 조건만은 채우지 않는다

여기가 이 모듈의 유일한 함정이다. 에이전트가 잘 하는 일(요약·일반화)을 시키면서
**잘 못하는 일 하나를 남겨 두는 것**이 설계다 (§5.6.4).

> 에이전트는 무효화 조건을 **후보로 제시**할 수는 있다. 다만 **선택 자체는 사람이
> 한다** — 기본값을 미리 채워 두면 강제 입력 지점의 효과가 사라진다.

그래서 프롬프트는 후보만 요구하고, 파서는 값이 오더라도 **후보로만 읽는다.**
모델이 지시를 어겨 확정값을 보내도 그것이 값이 되지 않는 이유가 여기 있다 —
`resolution.draft()` 가 애초에 그 인자를 받지 않는다.

주의할 점은 §6.5.4 가 이미 밝혔다. 에이전트가 채운 초안을 사람이 **읽지 않고 그대로
승인**하면 §5.3 의 되먹임 차단이 무력해진다 — 형식만 사람 검증이고 실질은 자기
산출물이기 때문이다. 무효화 조건을 비워 두는 것이 그것을 막는 구조다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.ingest.agent import AgentOutputError, Harness, extract_json
from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations import resolution as resolution_domain
from agentic_service_desk.operations.manual_entry import Entry
from agentic_service_desk.operations.resolution import Ground, GroundKind

_PROMPT = """담당자가 메신저로 받은 질문과 그 답변이다. 이것을 **지식 항목의 초안**으로 옮긴다.

규칙:
1. **일반화된 질문** — 원 질문에서 개인·상황 특정 요소(이름·사번·특정 건 번호·날짜)를
   걷어낸 형태로 다시 쓴다. 같은 유형의 질문 전체를 대표해야 한다.
2. **답** — 재사용 가능한 진술로 다시 쓴다. 이 한 건이 아니라 같은 유형 전체에
   적용되도록. 답변에 없는 내용을 지어내지 않는다.
3. **근거** — 이 답이 어디서 왔는가. 종류는 넷 중 하나다.
   code(코드 위치) · commit(커밋) · config(설정) · person(담당자 확인).
   답변만 있고 코드 근거가 확인되지 않으면 person 이다. **추측해서 code 라고 하지 않는다.**
4. **무효화 조건 후보** — "무엇이 바뀌면 이 답이 틀려지는가"의 **후보를 제시만 한다.**
   고르는 것은 사람이므로 하나로 정하지 않고 그럴듯한 것을 나열한다.
   연결형은 {"kind":"linked","refs":["경로"]}, 주기형은 {"kind":"periodic","period_days":180}.
5. 원인·적용 범위·재발 가능성은 아는 만큼만 쓰고 모르면 비운다.

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{
  "generalized_question": "...",
  "answer": "...",
  "grounding": [{"kind": "person", "ref": "담당자 확인"}],
  "invalidation_candidates": [{"kind": "periodic", "period_days": 180}],
  "cause": null,
  "scope": null,
  "recurrence": null
}"""


def build_prompt(entry: Entry) -> str:
    return "\n".join(
        [_PROMPT, "", f"질문 원문:\n{entry.question}", "", f"담당자의 답변:\n{entry.answer}"]
    )


@dataclass
class DraftReport:
    """한 주기가 무엇을 했는가."""

    drafted: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.drafted)


def parse_draft(text: str) -> dict:
    """응답을 초안 필드로 바꾼다. **무효화 조건은 후보로만 읽는다.**

    모델이 지시를 어기고 `invalidation` 을 확정값으로 보내도 여기서 후보로 내려앉는다.
    형식을 어긴 쪽이 조용히 이기지 않게 하는 자리다.
    """
    payload = extract_json(text)
    question = str(payload.get("generalized_question") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or not answer:
        raise AgentOutputError("일반화된 질문이나 답이 비었다")

    grounding = [
        Ground(kind=GroundKind(g["kind"]), ref=str(g["ref"]).strip())
        for g in (payload.get("grounding") or [])
        if isinstance(g, dict) and g.get("kind") in set(GroundKind) and str(g.get("ref") or "").strip()
    ]
    if not grounding:
        # 근거를 못 뽑았어도 사실 하나는 남아 있다 — **담당자가 그렇게 답했다.**
        # 지어내지 않고 그 사실을 근거로 둔다 (D3).
        grounding = [Ground(kind=GroundKind.PERSON, ref="담당자 답변")]

    raw_candidates = list(payload.get("invalidation_candidates") or [])
    if isinstance(payload.get("invalidation"), dict):
        raw_candidates.append(payload["invalidation"])

    return {
        "generalized_question": question,
        "answer": answer,
        "grounding": grounding,
        "invalidation_candidates": _candidates(raw_candidates),
        "cause": _optional(payload.get("cause")),
        "scope": _optional(payload.get("scope")),
        "recurrence": _optional(payload.get("recurrence")),
    }


def _candidates(raw: list) -> list[Invalidation]:
    out: list[Invalidation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                Invalidation(
                    kind=InvalidationKind(item["kind"]),
                    refs=tuple(str(r) for r in (item.get("refs") or ())),
                    period_days=item.get("period_days"),
                )
            )
        except (KeyError, ValueError):
            # 후보 하나가 형식을 어겨도 나머지는 쓴다. 어차피 **고르는 것은 사람**이다.
            continue
    return out


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


class Drafter:
    """등록된 원문에서 종결 기록 초안을 만든다."""

    def __init__(self, harness: Harness) -> None:
        self._harness = harness

    def run(self, conn: sqlite3.Connection) -> DraftReport:
        report = DraftReport()
        for entry in manual_entry.awaiting_draft(conn):
            try:
                fields = parse_draft(self._harness.run(build_prompt(entry)).text)
            except (AgentOutputError, RuntimeError) as exc:
                report.failures.append(f"{entry.qna_item_id}: {exc}")
                continue
            resolution_domain.draft(conn, ticket_id=entry.ticket_id, **fields)
            report.drafted.append(entry.ticket_id)
        return report
