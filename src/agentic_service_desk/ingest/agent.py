"""Ingest 에이전트 — 원천을 읽어 지식 항목을 짓는다 (FR-3, ADR-003).

지식베이스는 사람이 손으로 쓰지 않는다. **에이전트가 짓는다**(§4). 언어별 파서를
두지 않고 LLM 이 코드를 직접 읽어 개념을 뽑으므로, 다언어 코드베이스에 언어 수만큼
도구를 만들 필요가 없다.

**모델이 정하는 것과 코드가 정하는 것을 나눴다.**

| 모델이 정한다 | 코드가 정한다 |
|---|---|
| 어떤 개념이 있는가, 제목과 본문 | **출처**(커밋 해시·QnA id) — FR-4 |
| 어느 기존 항목을 갱신할 것인가 | 원천에 무엇이 들어가는가 — FR-9 · NFR-4 |
| 무효화 조건의 후보 | 조건이 없을 때의 대비값, 사람 편집 보호 |

출처를 모델에게 맡기지 않는 것이 이 분리의 핵심이다. **우리는 지금 어느 커밋을
읽는지 이미 알고 있다.** 모델에게 물으면 틀릴 수 있고, 틀린 출처는 붙어 있다는
사실 때문에 오히려 그럴듯해진다 (§2.2.3 이 운영 문서를 배제한 것과 같은 이유다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Protocol

from agentic_service_desk.ingest.config_paths import exclude_config_paths
from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)


class Harness(Protocol):
    """에이전트를 돌리는 것. `PiHarness` 가 이 모양이다."""

    def run(self, prompt: str, *, cwd: str | None = None): ...  # noqa: ANN201


class AgentOutputError(RuntimeError):
    """에이전트 출력이 형식을 어겼다."""


# --- 원천 -----------------------------------------------------------------


@dataclass(frozen=True)
class SourceMaterial:
    """소스 저장소에서 온 원천 한 묶음.

    **코드만이 아니라 커밋 메시지가 함께 온다** (D16, §2.2.1) — 히스토리가
    "왜 그렇게 정했는가"의 1차 출처이므로, 코드만 넣으면 원천의 절반이 사라진다.
    """

    commit: str
    """이 묶음의 기준 커밋. **그대로 출처가 된다** (FR-4)."""

    messages: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()
    """`(경로, 내용)`. 설정 파일은 여기 오기 전에 이미 잘렸다 (FR-9)."""

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(path for path, _ in self.files)


@dataclass(frozen=True)
class QnaMaterial:
    """QnA 에서 온 원천 하나.

    **산출물 필터를 지나온 것만 여기 온다** (NFR-4) — 이 자료구조를 직접 만들어
    ingest 를 부르면 그 문을 건너뛰게 되므로, 만드는 자리를 한 곳으로 유지한다.
    """

    answer_id: str
    question_id: str
    question: str
    answer: str


# --- 에이전트가 내놓는 것 --------------------------------------------------


@dataclass(frozen=True)
class ProposedItem:
    """에이전트가 제안한 항목. **출처가 없다** — 출처는 코드가 붙인다."""

    title: str
    body: str
    item_id: str | None = None
    """갱신 대상. 비어 있으면 새 항목이다 (ingest 절차 2단계)."""

    invalidation_kind: str | None = None
    refs: tuple[str, ...] = ()
    period_days: int | None = None
    used_paths: tuple[str, ...] = ()
    """근거로 삼은 경로. **원천에 실제로 있던 것만 살아남는다.**"""


@dataclass
class AgentReport:
    """한 번의 에이전트 호출 결과."""

    proposals: list[ProposedItem] = field(default_factory=list)
    dropped_paths: list[str] = field(default_factory=list)
    """FR-9 로 잘라낸 경로. 조용히 빼지 않는다."""


# --- 프롬프트 --------------------------------------------------------------

_RULES = """당신은 지식베이스를 짓는 에이전트다. 아래 원천을 읽고 지식 항목을 만들거나 갱신한다.

규칙:
1. 단위는 **개념**이다. 파일이나 함수 단위로 나누지 않는다. "결재 한도가 결정되는 규칙",
   "알림이 발송되는 조건" 같은 단위로 잡는다. 처음에는 굵게 잡는다.
2. **설정값을 지식으로 쓰지 않는다.** "한도는 부서 등급으로 결정된다"는 지식이고
   "지금 이 부서의 한도는 300만 원"은 상태다. 상태는 굳는 순간 틀려진다.
3. **원천의 언어를 그대로 따른다.** 같은 개념을 언어별로 나누지 않으며, 한 항목 안에
   여러 언어가 섞여도 된다.
4. 이미 있는 항목 목록을 준다. 같은 개념이면 **새로 만들지 말고 그 id 로 갱신**한다.
5. 각 항목에 **무효화 조건**을 단다 — 무엇이 바뀌면 이 지식이 틀려지는가.
   코드에 묶을 수 있으면 kind="linked" 와 refs(경로 목록), 묶을 대상이 없으면
   kind="periodic" 과 period_days.
6. 원천에서 확인되지 않는 것은 쓰지 않는다. 추측해서 채우지 않는다.
7. `used_paths` 에는 **이 항목을 지을 때 실제로 읽은 파일의 경로**를 적는다.
   아래 `--- 경로 ---` 로 주어진 것 중에서만 고르고, **그대로 옮겨 적는다.**
   이것은 무효화 조건(`refs`)과 다르다 — `refs` 는 *무엇이 바뀌면 틀려지는가*이고
   `used_paths` 는 *무엇을 보고 썼는가*다. 여기가 비면 그 항목은 커밋까지만
   되짚을 수 있고, **어느 파일에서 왔는지는 영영 알 수 없게 된다.**

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{
  "items": [
    {
      "id": null,
      "title": "개념의 이름",
      "body": "본문. 마크다운.",
      "invalidation": {"kind": "linked", "refs": ["src/a.py"], "period_days": null},
      "used_paths": ["src/a.py"]
    }
  ]
}

만들 것이 없으면 {"items": []} 를 낸다."""


def _index_block(index: list[tuple[str, str]]) -> str:
    if not index:
        return "이미 있는 항목: (없다 — 처음이다)"
    lines = "\n".join(f"- {item_id}: {title}" for item_id, title in index)
    return f"이미 있는 항목:\n{lines}"


def build_source_prompt(material: SourceMaterial, index: list[tuple[str, str]]) -> str:
    parts = [_RULES, "", _index_block(index), "", f"원천 — 커밋 {material.commit}"]
    if material.messages:
        parts += ["", "커밋 메시지 (왜 그렇게 정했는가의 1차 출처):"]
        parts += [f"  {m}" for m in material.messages]
    for path, content in material.files:
        parts += ["", f"--- {path} ---", content]
    return "\n".join(parts)


def build_qna_prompt(material: QnaMaterial, index: list[tuple[str, str]]) -> str:
    return "\n".join(
        [
            _RULES,
            "",
            _index_block(index),
            "",
            f"원천 — QnA {material.question_id}",
            "",
            f"질문: {material.question}",
            f"답변: {material.answer}",
        ]
    )


# --- 출력 파싱 --------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)


def _snippet(text: str, limit: int = 300) -> str:
    """실패 메시지에 응답 앞부분을 싣는다.

    무엇이 왔는지 없이 "형식을 어겼다"만 남기면 **로그를 봐도 고칠 수가 없다.**
    모델 출력은 비결정적이라 재현이 어려우므로, 터진 그 자리에서 증거를 남긴다.
    """
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def extract_json(text: str) -> dict:
    """응답에서 JSON 객체를 꺼낸다.

    모델은 지시해도 앞뒤에 말을 붙이고, 코드 울타리를 여러 개 쓰기도 한다. 그래서
    **울타리를 전부 훑어 처음 읽히는 객체**를 쓰고, 하나도 없으면 중괄호 균형을
    세어 가장 바깥 객체를 찾는다. 정규식으로 `{.*}` 를 잡으면 본문 안의 중괄호에서
    끊기므로 쓰지 않는다.
    """
    for match in _FENCE.finditer(text):
        payload = _try_object(match.group(1).strip())
        if payload is not None:
            return payload
    payload = _try_object(_outermost_object(text))
    if payload is not None:
        return payload
    raise AgentOutputError(f"응답에서 JSON 객체를 찾지 못했다 — 받은 것: {_snippet(text)}")


def _try_object(candidate: str) -> dict | None:
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _outermost_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def parse_proposals(text: str) -> list[ProposedItem]:
    """응답을 제안 목록으로 바꾼다. **제목이나 본문이 비면 버린다.**

    형식의 **빗나감과 어김을 가른다.** 모델이 "만들 것이 없다"를 `[]` 대신 `null` 로
    쓰거나 항목 하나를 배열로 감싸지 않는 것은 빗나감이다 — 뜻이 분명하므로 읽어
    준다. `items` 키 자체가 없는 것은 어김이라 실패로 올린다: 그때는 모델이 다른
    것을 답한 것이고, 조용히 "만들 것 없음"으로 넘기면 **원천 하나가 소리 없이
    지식이 되지 못한 채 처리 완료로 표시된다.**
    """
    payload = extract_json(text)
    if "items" not in payload:
        raise AgentOutputError(f"`items` 가 없다 — 받은 것: {_snippet(text)}")

    raw_items = payload["items"]
    if raw_items is None:
        raw_items = []
    elif isinstance(raw_items, dict):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raise AgentOutputError(
            f"`items` 가 목록이 아니다 ({type(raw_items).__name__}) — 받은 것: {_snippet(text)}"
        )

    proposals: list[ProposedItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        body = str(raw.get("body") or "").strip()
        if not title or not body:
            continue
        inv = raw.get("invalidation") or {}
        inv = inv if isinstance(inv, dict) else {}
        proposals.append(
            ProposedItem(
                title=title,
                body=body,
                item_id=(str(raw["id"]) if raw.get("id") else None),
                invalidation_kind=(str(inv["kind"]) if inv.get("kind") else None),
                refs=tuple(str(r) for r in (inv.get("refs") or [])),
                period_days=inv.get("period_days"),
                used_paths=tuple(str(p) for p in (raw.get("used_paths") or [])),
            )
        )
    return proposals


# --- 실행 -------------------------------------------------------------------

DEFAULT_PERIOD_DAYS = 180
"""묶을 대상이 없을 때의 재확인 주기 (§6.5.3 periodic).

무효화 조건 없이 항목을 만들지 않는다 — 그러면 그 지식은 **영영 stale 판정을 받지
못한다.** 모델이 조건을 빠뜨렸을 때 항목을 버리는 대신 대비값을 채우는 쪽을 택했다:
버리면 지식이 사라지지만, 대비값은 늦더라도 언젠가 재확인 대기열로 올라온다.
"""


DEFAULT_ATTEMPTS = 3
"""출력이 형식을 어겼을 때 같은 묶음을 다시 부르는 횟수 (첫 호출 포함).

**모델 출력은 비결정적이라, 같은 프롬프트가 두 번째에 성공한다.** 2026-08-30
부트스트랩에서 묶음의 48%(62/128)가 형식 위반으로 버려졌는데 그중 48건이
**빈 응답**이었다 — 원천에 문제가 있어서가 아니라 그 호출이 아무것도 내지
않아서다. 한 번 더 부르는 값은 초 단위지만, 버린 묶음은 그 실행에서 영영
지식이 되지 못한다.

커서가 실행 중에는 움직이지 않으므로 버려진 묶음도 다음 실행에서 다시 읽히긴
한다. 그러나 실패율이 그대로면 **다음 실행에서도 같은 비율로 떨어진다** —
다시 읽는 것은 구제책이 아니다.
"""


class IngestAgent:
    """원천 하나를 에이전트에 넣고 제안을 받는다."""

    def __init__(
        self,
        harness: Harness,
        *,
        attempts: int = DEFAULT_ATTEMPTS,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> None:
        self._harness = harness
        self._attempts = max(1, attempts)
        self._on_retry = on_retry

    def from_source(
        self, material: SourceMaterial, index: list[tuple[str, str]]
    ) -> list[ProposedItem]:
        return self._call(build_source_prompt(material, index))

    def from_qna(
        self, material: QnaMaterial, index: list[tuple[str, str]]
    ) -> list[ProposedItem]:
        return self._call(build_qna_prompt(material, index))

    def _call(self, prompt: str) -> list[ProposedItem]:
        """부르고 읽는다. **형식 위반만 다시 부른다.**

        `AgentOutputError` 는 "이번 출력이 어긋났다"는 뜻이라 다시 부르면 달라질
        수 있다. `HarnessError` 는 다르다 — 중단 신호(code=143)나 타임아웃이고,
        **다시 부르면 종료를 방해하거나 같은 시간을 한 번 더 태운다.** 그래서
        여기서 걸러 잡지 않고 그대로 올린다.
        """
        last: AgentOutputError | None = None
        for attempt in range(1, self._attempts + 1):
            result = self._harness.run(prompt)
            try:
                return parse_proposals(result.text)
            except AgentOutputError as exc:
                last = _with_stderr(exc, result)
                if attempt < self._attempts and self._on_retry is not None:
                    self._on_retry(attempt, last)
        assert last is not None
        raise last


def _with_stderr(exc: AgentOutputError, result: object) -> AgentOutputError:
    """실패 메시지에 하네스의 stderr 를 붙인다.

    **`rc=0` 인데 본문이 비어 오는 일이 있다.** 그 이유가 적히는 자리는 stderr
    뿐인데, 본문만 보고 있으면 로그에 `받은 것: ` 뒤가 비어 있는 줄만 쌓인다 —
    레이트리밋인지, 모델이 정말 아무 말도 안 한 것인지 가릴 수가 없다.

    `Harness` 규약은 `.text` 만 요구하므로 `err` 는 있을 때만 읽는다.
    """
    parts = [str(exc)]
    if getattr(result, "all_thinking", False):
        # **"아무 말도 안 했다"가 아니라 "사고만 하다 잘렸다"이다.**
        # 이 구분이 없으면 로그에 빈 줄만 쌓여 원인을 찾을 수 없다.
        raw = str(getattr(result, "raw", "") or "")
        parts.append(
            f"사고만 하다 끝났다 (출력 한도 초과로 보인다) — 걷어내기 전 {len(raw):,}자: "
            f"{_snippet(raw, 160)}"
        )
    err = str(getattr(result, "err", "") or "").strip()
    if err:
        parts.append(f"stderr: {_snippet(err, 200)}")
    return AgentOutputError(" | ".join(parts)) if len(parts) > 1 else exc


# --- 제안 → 지식 항목 (출처는 여기서 붙는다) --------------------------------


def to_knowledge_item(
    proposal: ProposedItem,
    *,
    provenance: list[Provenance],
    base: KnowledgeItem | None = None,
) -> KnowledgeItem:
    """제안에 **출처를 붙여** 지식 항목으로 만든다 (FR-4).

    `base` 가 있으면 갱신이다 — **불변 `id` 를 물려받고 출처는 합친다.** 덮어쓰면
    이 항목이 원래 어느 커밋에서 왔는지가 사라지고, 답변 이력의 근거 링크(D20)가
    가리키던 자리도 흐려진다.
    """
    if not provenance:
        raise ValueError("출처 없이 지식 항목을 만들 수 없다 (D3, FR-4)")

    invalidation = _invalidation_for(proposal)
    merged = list(base.provenance) if base else []
    for p in provenance:
        if p not in merged:
            merged.append(p)

    item = KnowledgeItem(
        title=proposal.title,
        body=proposal.body,
        provenance=merged,
        invalidation=invalidation,
        stale=False,
        edited_by_human=base.edited_by_human if base else False,
    )
    if base:
        item.id = base.id
    return item


def _invalidation_for(proposal: ProposedItem) -> Invalidation:
    """무효화 조건을 정한다. 모델의 제안을 쓰되 **성립하지 않으면 대비값으로 간다.**"""
    if proposal.invalidation_kind == InvalidationKind.LINKED and proposal.refs:
        return Invalidation(kind=InvalidationKind.LINKED, refs=proposal.refs)
    if proposal.invalidation_kind == InvalidationKind.PERIODIC:
        return Invalidation(
            kind=InvalidationKind.PERIODIC,
            period_days=proposal.period_days or DEFAULT_PERIOD_DAYS,
        )
    # 조건이 없거나 형식을 어겼다. 근거로 쓴 경로가 있으면 그것에 묶는 것이
    # 자연스럽다 — 그 파일이 바뀌면 이 지식이 틀려질 수 있다는 뜻이기 때문이다.
    if proposal.used_paths:
        return Invalidation(kind=InvalidationKind.LINKED, refs=proposal.used_paths)
    return Invalidation(kind=InvalidationKind.PERIODIC, period_days=DEFAULT_PERIOD_DAYS)


def source_provenance(proposal: ProposedItem, material: SourceMaterial) -> list[Provenance]:
    """소스 원천의 출처. **원천에 실제로 있던 경로만 인정한다.**

    모델이 없는 경로를 지어내면 Lint 의 "출처 커밋이 실재하는가" 검사(ADR-002 결정 4)가
    깨진 링크를 잡게 되는데, 그때는 이미 그 항목이 답변의 근거로 쓰인 뒤다. 붙이는
    자리에서 막는 편이 낫다.

    인정되는 경로가 하나도 없어도 **커밋은 남는다** — 출처 없는 항목은 만들지 않는다.
    """
    valid = [p for p in proposal.used_paths if p in material.paths]
    if not valid:
        return [Provenance(commit=material.commit)]
    return [Provenance(commit=material.commit, path=p) for p in valid]


def qna_provenance(material: QnaMaterial) -> list[Provenance]:
    return [Provenance(qna=material.question_id)]


def prepare_source_material(
    commit: str, messages: list[str], files: list[tuple[str, str]]
) -> tuple[SourceMaterial, list[str]]:
    """원천 묶음을 만든다. **설정 파일은 여기서 잘린다** (FR-9).

    원천을 만드는 자리를 하나로 두는 이유는 산출물 필터와 같다 — 여러 곳에서 만들면
    언젠가 한 곳이 배제를 잊는다.
    """
    kept_paths, dropped = exclude_config_paths([path for path, _ in files])
    kept = tuple((path, content) for path, content in files if path in set(kept_paths))
    return SourceMaterial(commit=commit, messages=tuple(messages), files=kept), dropped
