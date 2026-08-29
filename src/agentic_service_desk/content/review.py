"""콘텐츠 검수 — 4단계 (WBS-4.6.4, FR-39, §5.5.5, §7.6.4).

**콘텐츠는 국면과 무관하게 전수 사람 승인이다** (FR-39, D23). 답변에 있는 자동 게재
관문이 여기에는 **아예 없다** — 빈도가 낮고(주기적), 노출이 넓고(불특정 다수), 회수가
어렵다(발행물은 불가). 셋 다 더 엄격하게 볼 이유이고, 빈도가 낮다는 것은 그것이
부담이 되지 않는다는 뜻이기도 하다 (§5.5.5).

## 그래서 에이전트 검수의 역할이 답변과 다르다

답변에서 에이전트 검수는 **통과/반려의 한 축**이다 — 자동 게재로 가느냐를 가르므로,
판정할 수 없으면 통과시키지 않는다 (§5.6.1). 여기서는 사람이 반드시 보므로 그 축이
없다. 에이전트가 하는 일은 하나다: **어디를 먼저 볼지 말해 준다.**

> 소견이 없다고 통과가 아니고, 소견이 있다고 반려도 아니다. **판정은 사람이 한다.**

1인 겸업이 대기열을 훑을 때 판단을 줄여 주는 것이 화면의 일이라는 §8.6.3 과 같은
자리이며, 답변의 근거 강도 표시(§5.6.5)가 하던 일을 콘텐츠에서는 이 소견이 한다.

## 무엇을 세는가

**기계적으로 확정되는 것만 센다.** 확률적 판정에 맡기면 같은 초안이 볼 때마다 다른
소견을 내고, 그러면 검수자가 소견을 믿지 않게 된다.

| 소견 | 무엇을 보는가 | 왜 |
|---|---|---|
| **P4** | 근거에 낡은 항목이 있는가 | 낡은 지식을 현재형으로 쓴다 |
| **P1** | 본문의 수치가 근거 원문에 있는가 | 없는 수치는 지어낸 것이다 |
| **인용 어긋남** | 따옴표 안 문장이 근거 원문에 있는가 | **라이브에서 잡았다** — 근거가 갱신됐는데 옛 문장을 그대로 인용한 채 남았다 |
| **P8** | 명령형·당위 표현이 있는가 | 정책 공지처럼 읽힌다 (§7.6.4). **칼럼류에만 붙는다** |
| **P6·P7** | 가치 판단인가, 관찰을 넘겨 말했는가 | **의미 판정이라 모델이 본다** (§7.6.4) |

## 의미 판정은 따로 돌고, 결과는 초안에 박힌다 (WBS-4.7.2)

P6·P7 은 문장 형태로 확정되지 않는다 — "이 방식이 더 낫습니다"는 형태로 잡히지만
"X 를 쓰면 대개 문제가 없습니다"는 형태가 같은 해설과 구분되지 않는다. 그래서 모델이
본다. 다만 **답변 검수와 역할이 다르다**: 여기서 나온 것은 소견이지 판정이 아니다.

**배치에서 돌고 화면에서 돌지 않는다.** 화면이 열릴 때마다 모델을 부르면 같은 초안이
볼 때마다 다른 소견을 내고, 검수자는 곧 그 소견을 믿지 않게 된다. 결과는
`content_draft.agent_findings` 에 박히며 — **`None` 은 "아직 안 봤다"이고 `[]` 는
"봤는데 없다"다.** 구분하지 않으면 판정이 돌지 않은 초안이 통과한 것처럼 보인다
(§5.6.1).

**관찰도 근거로 준다.** FR-41 이 요구하는 "관찰을 함께 밝혔는가"는 초안만 봐서는
판정할 수 없다 — 무엇이 관찰됐는지를 알아야 지어낸 관찰을 가려낼 수 있다.

**선언이 정한다** (FR-42). P6·P7 을 든 타입만 이 판정을 받는다 — 가이드에 붙이면
사용 설명의 권고 문장이 전부 걸려 소견이 소음이 된다.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.content import store
from agentic_service_desk.content.registry import ContentType
from agentic_service_desk.ingest.agent import AgentOutputError, extract_json
from agentic_service_desk.pipeline import review as review_domain
from agentic_service_desk.pipeline.review import Reject, ReviewInput, Verdict

_QUOTED = re.compile(r'["“”]([^"“”]{8,})["“”]')
"""따옴표 안의 문장. **여덟 자 미만은 세지 않는다** — 낱말 하나를 강조한 따옴표까지
인용으로 보면 소견이 소음이 되어 결국 아무도 안 본다."""

_POLICY_VOICE = re.compile(
    r"(하십시오|하시기 바랍니다|해야 합니다|하셔야 합니다|해 주십시오|"
    r"권장합니다|금지합니다|원칙입니다|반드시 .*(?:한다|합니다))"
)
"""P8 의 신호 — 명령형·당위 표현 (§7.6.4).

**문장 형태만으로 판정된다**고 컨셉이 못 박은 것이 이 검사의 근거다. 모델에 물으면
같은 문장이 볼 때마다 다르게 판정된다.
"""


@dataclass(frozen=True)
class Finding:
    """소견 하나. **반려가 아니라 지목이다.**"""

    reason: Reject | None
    detail: str

    @property
    def label(self) -> str:
        return str(self.reason) if self.reason else "확인"


@dataclass
class Findings:
    """한 초안에 대한 소견 전부."""

    items: list[Finding] = field(default_factory=list)
    pending_semantic: bool = False
    """의미 판정을 받아야 하는 타입인데 **아직 돌지 않았다** (§7.6.4).

    "봤는데 없다"와 섞으면 판정이 돌지 않은 초안이 통과한 것처럼 보인다 (§5.6.1).
    """

    @property
    def look_here_first(self) -> str:
        """**무엇부터 보라고 말해 준다.**

        비어 있는 것이 "통과"가 아니다 — 여기 없는 것은 기계가 확정할 수 없는
        것뿐이고, 사람이 볼 이유는 그대로 남는다.
        """
        waiting = (
            " **P6·P7 의미 판정은 아직 돌지 않았다** — 다음 배치가 본다."
            if self.pending_semantic
            else ""
        )
        if not self.items:
            return (
                "기계 검사에 걸린 것은 없다. **통과가 아니라 지목할 것이 없다는 "
                "뜻이다** — 근거 대조와 어조는 사람이 본다." + waiting
            )
        return " · ".join(f"{f.label} {f.detail}" for f in self.items) + waiting


def check_quotations(body: str, source_text: dict[str, str]) -> Finding | None:
    """따옴표 안 문장이 근거 원문에 그대로 있는가.

    **라이브에서 잡은 것이다.** 근거 항목의 본문이 바뀌었는데 문서는 옛 문장을 그대로
    인용한 채 남았다 — 인용은 "원문 그대로"라는 주장이므로, 그 주장은 대조로 확정할
    수 있다. 답변에서 `확인됨` 을 세어서 검증한 것과 같은 자리다.

    공백만 정규화해 비교한다. 그보다 느슨하게 보면 바뀐 인용을 놓치고, 더 엄격하게
    보면 줄바꿈 하나로 소견이 뜬다.
    """
    source = " ".join(" ".join(t.split()) for t in source_text.values())
    missing = [
        q for q in _QUOTED.findall(body) if " ".join(q.split()) not in source
    ]
    if not missing:
        return None
    return Finding(
        reason=None,
        detail=(
            "근거 원문에 없는 인용이다 — 근거가 갱신됐는데 옛 문장이 남았을 수 있다: "
            + " / ".join(f'"{q[:40]}"' for q in missing[:3])
        ),
    )


def check_policy_voice(body: str) -> Finding | None:
    """P8 — 조직의 정책·방침처럼 읽히는 서술 (§7.6.4, §7.6.3).

    **귀속의 문제가 근거 문제보다 크다.** AI 가 "앞으로는 이렇게 하시는 것이
    좋습니다"라고 쓰면 사내에서는 **정책 공지로 읽히는데**, 이 시스템은 그런 권위를
    가질 수 없다.
    """
    hits = _POLICY_VOICE.findall(body)
    if not hits:
        return None
    return Finding(
        reason=Reject.P8,
        detail="정책 공지처럼 읽히는 표현이다: " + ", ".join(sorted(set(hits))[:3]),
    )


def sources_of(
    draft: store.ContentDraft, source_text: dict[str, str]
) -> tuple[tuple[str, ...], dict[str, str]]:
    """근거 원문 — **지식 항목과 관찰을 함께.**

    박은 사실도 근거다 — 관찰(§7.6.2)이든 기간 요약(§7.2)이든. 검사에 넣지 않으면
    "지난 30일 동안 4건"의 `4` 가 근거 원문에 없는 수치가 되어 **P1 이 관찰을 밝힌
    문장을 지목한다** — FR-41 이 요구한 바로 그 문장이다.

    **저장에서는 나눠 두고 검사에서만 합친다.** `grounding` 에는 stale 판정과
    지식베이스 커밋 고정이 걸려 있어 관찰이 섞이면 없는 항목을 찾게 된다.
    """
    merged = dict(source_text)
    ids = list(draft.grounding)
    for fact in store.facts_of(draft):
        merged[fact.id] = fact.text
        ids.append(fact.id)
    return tuple(ids), merged


def stored_findings(draft: store.ContentDraft) -> list[Finding]:
    """박아 둔 의미 판정 소견을 되살린다. **판정이 아직 안 돌았으면 그렇다고 말한다.**"""
    if draft.agent_findings is None:
        return []
    out = []
    for payload in draft.agent_findings:
        raw = str(payload.get("reason") or "")
        try:
            reason = Reject(raw)
        except ValueError:
            reason = None
        out.append(Finding(reason=reason, detail=str(payload.get("detail") or "")))
    return out


def needs_semantic(ctype: ContentType) -> bool:
    """이 타입이 의미 판정을 받는가. **선언이 정한다** (FR-42, §7.6.4)."""
    return any(
        str(r) in ctype.review.extra_rejections for r in (Reject.P6, Reject.P7)
    )


def inspect(
    ctype: ContentType,
    draft: store.ContentDraft,
    *,
    source_text: dict[str, str],
    stale_ids: frozenset[str] = frozenset(),
) -> Findings:
    """기계가 확정할 수 있는 것과 **박아 둔 의미 판정**을 모은다. 판정하지 않는다."""
    findings = Findings(pending_semantic=needs_semantic(ctype) and draft.agent_findings is None)
    grounding, sources = sources_of(draft, source_text)

    # P4·P1 은 답변과 같은 검사다 — 두 벌로 만들면 "근거 원문에 있다"의 뜻이
    # 두 곳에서 갈린다.
    shared = ReviewInput(
        draft_body=draft.body,
        grounding=grounding,
        source_text=sources,
        stale_ids=stale_ids,
    )
    for check in review_domain.MECHANICAL:
        verdict = check(shared)
        if verdict is not None:
            findings.items.append(Finding(reason=verdict.reason, detail=verdict.detail))

    quoted = check_quotations(draft.body, sources)
    if quoted is not None:
        findings.items.append(quoted)

    # **선언이 정한다** (FR-42). P8 은 칼럼류에만 붙는다 — 가이드에 붙이면 사용
    # 설명의 "~해야 합니다"까지 걸려 소견이 소음이 된다.
    if str(Reject.P8) in ctype.review.extra_rejections:
        policy = check_policy_voice(draft.body)
        if policy is not None:
            findings.items.append(policy)

    findings.items.extend(stored_findings(draft))
    return findings


# --- 의미 판정 (P6·P7, FR-41) -------------------------------------------------

_SEMANTIC_RULES = """아래 **칼럼 초안**을 읽고 문제가 되는 곳을 지목한다.

당신은 이 글이 왜 이렇게 쓰였는지 **모른다.** 알 필요도 없다 — 글에 적힌 것과 아래
근거·관찰만 본다. **판정하지 않는다**: 사람이 판정하므로 당신이 할 일은 어디를 먼저
볼지 말해 주는 것이다.

칼럼은 **해설과 권고까지만** 쓸 수 있다. 셋을 지목한다.

- **P6 — 가치 판단.** 근거로 환원되지 않는 평가다. "이 방식이 더 낫습니다",
  "~하는 것이 옳습니다", "권장할 만합니다". 사실의 재구성(해설)은 P6 가 아니다.
- **P7 — 관찰의 확대.** 센 것보다 넓게 말했다. "4건 있었다"는 사실이지만 "다들
  그렇게 씁니다", "대부분의 이용자가", "일반적으로 그렇습니다"는 사실이 아니다.
- **관찰 없는 권고.** 조언인데 무엇을 관찰했는지 밝히지 않았거나, **아래 관찰
  목록에 없는 관찰**을 들었다. 관찰을 생략한 조언은 그 순간 의견이 된다.

**해설은 지목하지 않는다.** 근거에 있는 사실을 이어 설명한 것은 이 글이 해야 할
일이다 — 트집을 잡는 것이 목적이 아니다.

출력은 **JSON 하나만** 낸다. 지목할 것이 없으면 빈 목록을 낸다.

{"findings": [{"reason": "P6", "quote": "문제가 되는 문장 그대로", "detail": "왜 그런지 한 문장"}]}
{"findings": []}"""


def build_semantic_prompt(draft: store.ContentDraft, sources: dict[str, str]) -> str:
    """의미 판정 프롬프트. **초안과 근거·관찰뿐이다** (§5.5.2, FR-20)."""
    grounding, merged = sources_of(draft, sources)
    blocks = "\n\n".join(
        f"### {g}\n{merged.get(g, '(원문 없음)')}" for g in grounding
    )
    return "\n".join([_SEMANTIC_RULES, "", "초안:", draft.body, "", "근거와 관찰:", blocks])


def parse_semantic(text: str) -> list[dict]:
    """응답을 소견 목록으로.

    **사유를 못 댄 지목은 버리지 않고 사유 없는 소견으로 남긴다** — 여기서 나오는
    것은 반려가 아니라 지목이므로, 분류가 안 되어도 "여기를 보라"는 값은 남는다.
    """
    payload = extract_json(text)
    out = []
    for raw in payload.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        reason = str(raw.get("reason") or "").strip()
        detail = str(raw.get("detail") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if quote:
            detail = f'"{quote[:60]}" — {detail}' if detail else f'"{quote[:60]}"'
        if not detail:
            continue
        out.append({"reason": reason if reason in _SEMANTIC_REASONS else "", "detail": detail})
    return out


_SEMANTIC_REASONS = {str(Reject.P6), str(Reject.P7)}


def inspect_semantically(
    conn: sqlite3.Connection,
    ctype: ContentType,
    draft: store.ContentDraft,
    *,
    harness,  # noqa: ANN001 — Harness. 없으면 부르지 않는다
    source_text: dict[str, str],
) -> list[dict] | None:
    """모델에게 P6·P7 을 묻고 결과를 초안에 박는다. **실패는 박지 않는다.**

    실패를 빈 목록으로 박으면 "봤는데 없다"가 되어 **돌지 않은 판정이 통과한 것처럼
    보인다** (§5.6.1). 박지 않으면 다음 주기가 다시 시도하고, 그때까지 화면은
    "아직 안 봤다"고 말한다.
    """
    if harness is None:
        return None
    try:
        findings = parse_semantic(harness.run(build_semantic_prompt(draft, source_text)).text)
    except (AgentOutputError, RuntimeError):
        return None
    store.record_findings(conn, draft.id, findings)
    return findings


# --- 판정 ---------------------------------------------------------------------


class FinalCheckRequired(RuntimeError):
    """발행물이라 승인만으로는 나갈 수 없다 (§7.3, §5.5.5)."""


def awaiting_final_check(ctype: ContentType) -> bool:
    """승인된 뒤에도 **발행 직전 최종 확인**이 남았는가.

    발행물은 W3 를 해소할 수 없어(§7.3) 되돌릴 수 없다 — 그래서 검수가 전문이고
    발행 직전에 한 번 더 본다. **상태로 저장하지 않고 선언에서 읽는다**: 저장하면
    선언과 두 벌이 되고, 어긋나면 낮은 쪽이 이긴다.
    """
    return ctype.review.final_check


def decide(
    conn: sqlite3.Connection,
    ctype: ContentType,
    draft: store.ContentDraft,
    *,
    approved: bool,
    reason: Reject | None = None,
    detail: str = "",
) -> None:
    """사람이 판정한다 (FR-39). **판정 사건을 검수 기록에 남긴다** (FR-22).

    **반려에는 사유를 요구한다.** 사유 없는 반려는 기록으로 쓸 수 없고(§5.5.6),
    그 분포가 없으면 반려율이 읽히지 않는다.

    기록의 `kind` 가 `content` 인 것이 중요하다 — 답변 반려율에 섞이면 그 숫자가
    "사람이 에이전트의 답변을 얼마나 믿는가"를 더는 뜻하지 않는다.
    """
    if not approved and reason is None:
        raise ValueError("반려에는 사유가 필요하다 (§5.5.6) — 사유 없는 반려는 기록이 아니다")

    store.decide(conn, draft.id, approved=approved)
    review_domain.record(
        conn,
        review=ReviewInput(
            draft_body=draft.body, grounding=draft.grounding, source_text={}
        ),
        verdict=Verdict(
            passed=approved, reason=reason, detail=detail, checked_by="human"
        ),
        kind=review_domain.CONTENT,
    )
    _close_ticket(conn, draft, approved=approved)


def _close_ticket(
    conn: sqlite3.Connection, draft: store.ContentDraft, *, approved: bool
) -> None:
    """판정이 끝나면 그 작업은 끝났다.

    **강제 입력 지점을 여기서 다시 묻지 않는다** (§5.6.4) — 모순 판정과 같은 이유다:
    콘텐츠 판정은 diff 를 읽어야만 누를 수 있는 버튼이라 그 목적이 이미 달성됐다.
    무효화 조건은 이 초안에 묶는다.

    **이 기록은 승격 대상이 아니다.** 콘텐츠는 이 시스템이 쓴 것이므로 지식으로
    되돌아오면 자기 요약을 다시 배우는 순환이 된다 (T4, §7.4) — `PROMOTABLE_SOURCES`
    가 QnA 유래만 받는 것이 그 집행 지점이다.
    """
    if not draft.ticket_id:
        return

    from agentic_service_desk.knowledge.item import Invalidation, InvalidationKind
    from agentic_service_desk.operations import resolution as resolution_domain
    from agentic_service_desk.operations import ticket as ticket_domain

    verdict = "승인" if approved else "반려"
    resolution_domain.draft(
        conn,
        ticket_id=draft.ticket_id,
        generalized_question=f"콘텐츠 초안 {draft.id} 를 게재할 것인가",
        answer=f"판정: {verdict}",
        grounding=[
            resolution_domain.Ground(
                kind=resolution_domain.GroundKind.PERSON,
                ref=f"운영자 판정 · {draft.type_id}",
            )
        ],
        drafted_by="human",
    )
    resolution_domain.confirm(
        conn,
        draft.ticket_id,
        invalidation=Invalidation(
            kind=InvalidationKind.LINKED, refs=(draft.id,)
        ),
    )
    ticket_domain.transition(conn, draft.ticket_id, ticket_domain.State.CLOSED)
