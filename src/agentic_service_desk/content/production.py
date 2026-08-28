"""콘텐츠 제작 — 지식베이스에서 살아있는 문서를 만들고 갱신한다 (WBS-4.6.2, FR-36·43).

**새 파이프라인이 아니다** (§7.1, D10). 답변과 같은 5단계를 트리거만 바꿔 탄다 —
질문 대신 주기·임계가 부르고, 조회 대상이 질문이 아니라 **타입이 선언한 주 입력**이다.
여기까지가 1~3단계이고 4·5단계(검수·게재)는 4.6.4·4.6.3 이 잇는다.

## 입력 읽개는 타입마다가 아니라 주 입력 종류마다 하나다

FR-42 는 새 타입 추가에 코드 변경이 없어야 한다고 한다. 그것이 성립하는 이유는
읽개가 **셋뿐**이기 때문이다 — 지식베이스 · QnA 통계 · 둘 다. 타입이 넷에서 열이
되어도 읽개는 늘지 않는다. 지금 있는 것은 지식베이스 읽개 하나이고, QnA 통계는
FAQ 가 오는 WBS-4.7.1 이 붙인다.

## 가이드가 FAQ 보다 먼저인 이유 (D50)

FAQ 는 QnA 통계에 종속돼 질문이 쌓여야 만들 수 있지만, **가이드는 소스코드만으로
만들 수 있다.** 1국면에 실제로 낼 수 있는 콘텐츠가 이것 하나다.

## 여기서 정한 것 넷

**① 언어는 판정하지 않고 고정한다** (FR-43, D55). 답변은 질문 언어로 쓰지만(FR-17)
콘텐츠는 1차 언어다. 근거 본문을 보고 언어를 판정하면 영문 지식이 늘었을 때
**살아있는 문서 하나가 갱신마다 언어를 바꾼다.**

**② 갱신은 직전 판본을 입력에 넣는다.** 매번 백지에서 쓰면 문서가 주기마다 통째로
달라지고, 그러면 **변경분 검수(§5.5.5)가 전문 검수와 같아진다** — diff 가 곧 전문이다.
살아있는 문서의 재실행은 "같은 문서를 다시 씀"이지 새로 씀이 아니다 (§7.3).

**③ 무엇이 바뀌었는지 모델에게 묻지 않는다.** diff 는 셀 수 있다 — 세어서 만든다.
모델의 자기 신고는 검증할 수 없고, 검수자가 그것을 믿으면 표시가 없는 것과 같아진다.

**④ 낡은 근거는 빼되, 코드 변경이 부른 주기는 기다린다.** 답변은 질문에 답해야 해서
낡은 근거라도 쓰고 강도를 내리지만(§5.6.5), **가이드는 무엇을 다룰지 고를 수 있다** —
확신할 수 없는 항목은 안 다루고 다음 갱신에 들인다. 그것이 §7.3 이 말한 "갱신으로
W3 를 흡수한다"이다. 다만 **코드 변경 임계로 도는 주기**는 다르다: 지식이 아직 그
커밋을 읽지 않았는데 지금 돌리면 **같은 글이 나오고**, 그것을 갱신이라 부르면 고쳤다는
기록만 남는다 — 4.5.7 이 정정에서 정한 것과 같은 순서다 (§6.6.3).
"""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agentic_service_desk.content import store
from agentic_service_desk.content.registry import ContentType, Input
from agentic_service_desk.ingest.agent import AgentOutputError, Harness, extract_json
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.operations import ticket as ticket_domain

LANGUAGE = "한국어"
"""1차 언어 (FR-43, D55). **판정하지 않는다** — 콘텐츠는 한 언어로 유지된다."""

CONTENT_STAGES = frozenset({"S4", "S5"})
"""콘텐츠가 도는 단계 (FR-59, D49).

**목록이지 부등호가 아니다.** 단계 이름의 사전 순서에 기대면 이름을 바꾸는 순간
조용히 다르게 돌고, `PUBLISHING_STAGES` 가 같은 이유로 이미 목록이다.
"""


class UnsupportedInput(NotImplementedError):
    """읽개가 아직 없는 주 입력. **조용히 건너뛰지 않는다.**

    건너뛰면 선언은 있는데 아무것도 나오지 않는 타입이 생기고, 그 침묵은 "만들 것이
    없다"와 구분되지 않는다.
    """


@dataclass
class Result:
    """한 번의 제작."""

    type_id: str
    outcome: store.Outcome
    detail: str = ""
    draft_id: str | None = None
    grounding: tuple[str, ...] = ()
    excluded_stale: tuple[str, ...] = ()
    """근거에서 뺀 낡은 항목. **뺐다는 사실을 남긴다** — 조용히 빠지면 가이드가
    다루던 주제가 사라진 것을 아무도 모른다."""

    diff: str = ""
    """직전 판본과의 변경분. **세어서 만든다** — 모델에게 묻지 않는다."""

    churn: float = 0.0
    """바뀐 줄의 비율. **근거가 조금 바뀌었는데 문서가 많이 달라졌으면 거기를 먼저
    본다** — 갱신이 고쳐 쓰기가 아니라 다시 쓰기가 된 신호다 (§7.3)."""

    @property
    def produced(self) -> bool:
        return self.outcome is store.Outcome.PRODUCED


# --- 트리거 -------------------------------------------------------------------


@dataclass(frozen=True)
class Trigger:
    """지금 돌 이유가 있는가 (§7.5-2)."""

    due: bool
    periodic: bool = False
    """주기가 찼는가. **코드 변경과 나눠 든다** — 낡은 근거로 기다릴지가 여기서 갈린다."""

    threshold: bool = False
    reason: str = ""


def evaluate_trigger(
    conn: sqlite3.Connection,
    ctype: ContentType,
    *,
    source_commit: str | None = None,
    now: datetime | None = None,
) -> Trigger:
    """주기와 임계를 본다.

    **첫 제작은 언제나 돈다** — 한 번도 만든 적이 없으면 기다릴 직전 판본이 없다.
    """
    run = store.last_run(conn, ctype.id)
    if run is None or run.last_generated_at is None:
        return Trigger(due=True, periodic=True, reason="첫 제작")

    now = now or datetime.now(UTC)
    periodic = False
    if ctype.trigger.period_days is not None:
        last = datetime.fromisoformat(run.last_generated_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        periodic = now - last >= timedelta(days=ctype.trigger.period_days)

    threshold = False
    if ctype.trigger.threshold == "source_changed":
        threshold = bool(source_commit) and source_commit != run.last_commit

    reasons = []
    if periodic:
        reasons.append(f"주기 {ctype.trigger.period_days}일")
    if threshold:
        reasons.append("코드 변경")
    return Trigger(
        due=periodic or threshold,
        periodic=periodic,
        threshold=threshold,
        reason=" · ".join(reasons),
    )


# --- 3단계 생성 ---------------------------------------------------------------

_RULES = """당신은 사내 시스템의 **{kind}**을 쓴다. 제목은 "{title}" 이다.

**근거로 준 지식 항목만 쓴다.** 항목에 없는 사실을 채워 넣지 않는다 — 근거가 붙어
있으면 읽는 사람은 그것을 검증된 사실로 읽으므로, 지어낸 문장은 근거가 없는 문장보다
해롭다.

**{language} 로 쓴다.** 근거를 인용할 때는 원문 그대로 옮긴다.

**본문에 문서 제목을 다시 쓰지 않는다.** 제목은 `title` 로 따로 낸다 — 본문 맨 앞에
또 쓰면 갱신할 때마다 한 줄씩 쌓인다.

이것은 **가치 판단이 아니라 설명**이다. "이 방식이 더 낫습니다", "앞으로는 ~하십시오"
같은 문장을 쓰지 않는다 — 이 시스템은 조직의 입장을 대신 말할 수 없다.

{update_rules}

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{
  "title": "문서 제목",
  "body": "문서 본문 (Markdown)",
  "grounding": ["k-..."]
}"""

_FIRST = """이번이 **첫 제작**이다. 근거가 다루는 범위 안에서 문서를 처음부터 쓴다."""

_UPDATE = """아래에 **직전 판본**이 있다. 그것을 **고쳐서** 낸다 — 처음부터 다시 쓰지
않는다. 살아있는 문서라 사람은 **바뀐 곳만** 검수하며, 통째로 달라지면 무엇이
바뀌었는지 볼 수 없어 검수가 전문 검수가 된다.

근거와 어긋나는 부분만 고치고, 근거가 새로 다루는 것을 더한다. **그대로 둘 수 있는
문장은 글자 그대로 둔다.**"""


def build_prompt(
    ctype: ContentType, items: list, previous: store.ContentDraft | None
) -> str:
    blocks = [f"### {i.id} — {i.title}\n{i.body}" for i in items]
    parts = [
        _RULES.replace("{kind}", "살아있는 문서" if ctype.living else "발행물")
        .replace("{title}", ctype.title)
        .replace("{language}", LANGUAGE)
        .replace("{update_rules}", _UPDATE if previous else _FIRST),
        "",
        "근거로 쓸 수 있는 지식 항목:",
        *blocks,
    ]
    if previous:
        # **제목을 본문 앞에 붙이지 않는다.** 붙이면 모델이 그것을 본문의 일부로
        # 읽고 그대로 되받아, **갱신할 때마다 제목이 한 줄씩 는다** — 라이브에서 잡았다.
        parts += [
            "",
            f"직전 판본의 제목: {previous.title}",
            "직전 판본의 본문:",
            previous.body,
        ]
    return "\n".join(parts)


def strip_title_heading(title: str, body: str) -> str:
    """본문 맨 앞의 제목 줄을 걷어낸다.

    **프롬프트로 부탁하고 코드가 지킨다.** 모델은 대체로 따르지만 대체로는 충분하지
    않다 — 한 번 새면 그 줄이 다음 판본의 입력이 되고, 갱신마다 제목이 한 줄씩
    쌓인다. 라이브에서 실제로 밟았다.

    제목과 다른 h1 은 건드리지 않는다. 그것은 문서가 스스로 고른 머리말이다.
    """
    lines = body.lstrip().splitlines()
    if not lines or not lines[0].startswith("# "):
        return body.strip()
    if lines[0][2:].strip() != title.strip():
        return body.strip()
    return "\n".join(lines[1:]).strip()


def churn(previous: str, current: str) -> float:
    """바뀐 줄의 비율. **부탁으로 지켜지지 않는 것은 재서 드러낸다.**

    갱신은 "고쳐 쓰기"여야 하는데(§7.3) 모델은 근거가 그대로인 문단까지 다시 쓴다 —
    라이브에서 봤다. 막을 방법이 프롬프트뿐이라면 **적어도 세어서 검수자에게
    말해 준다**: 근거는 한 항목만 바뀌었는데 문서의 절반이 달라졌다면 그것이
    먼저 볼 곳이다 (§5.6.5 가 답변에서 한 것과 같은 일).
    """
    before, after = previous.splitlines(), current.splitlines()
    total = max(len(before), len(after))
    if not total:
        return 0.0
    same = sum(
        block.size
        for block in difflib.SequenceMatcher(None, before, after).get_matching_blocks()
    )
    return 1.0 - (same / total)


def parse_document(text: str, allowed_ids: set[str]) -> tuple[str, str, tuple[str, ...]] | None:
    """응답을 (제목, 본문, 근거)로. `None` 이면 쓸 것이 없다는 결정이다.

    지어낸 근거 id 는 버린다 — 없는 것을 가리키는 근거는 Lint 의 끊어진 링크가 되고,
    그때는 이미 문서가 나간 뒤다. 하나도 남지 않으면 **근거에서 나온 글인지 알 수
    없으므로** 초안으로 받지 않는다 (D3).
    """
    payload = extract_json(text)
    title = str(payload.get("title") or "").strip()
    body = strip_title_heading(title, str(payload.get("body") or ""))
    if not body:
        return None
    grounding = tuple(
        dict.fromkeys(
            str(g) for g in (payload.get("grounding") or []) if str(g) in allowed_ids
        )
    )
    if not grounding:
        return None
    return title, body, grounding


def diff_of(previous: str, current: str) -> str:
    """변경분. **세어서 만든다** (§5.5.5).

    모델에게 "무엇을 바꿨는가"를 묻지 않는 이유는 그 답을 검증할 수 없기 때문이다 —
    검수자가 믿고 넘기면 그 표시는 없는 것과 같아진다.
    """
    lines = difflib.unified_diff(
        previous.splitlines(), current.splitlines(), lineterm="", n=2
    )
    return "\n".join(lines)


# --- 실행기 -------------------------------------------------------------------


class ContentProducer:
    """타입 하나를 1~3단계에 태운다.

    **4·5단계는 밖에서 잇는다** — 검수는 WBS-4.6.4(Q3), 게재는 4.6.3(문서 면 upsert).
    여기서 나온 것은 **아직 나간 것이 아니다.**
    """

    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        repo: KnowledgeRepository,
        harness: Harness | None = None,
        generated_by: str = "",
    ) -> None:
        self._conn = conn
        self._repo = repo
        self._harness = harness
        self._generated_by = generated_by

    def run(self, ctype: ContentType, *, source_commit: str | None = None) -> Result:
        # ① 앞 초안이 아직 대기 중이면 다시 만들지 않는다. 한 타입이 Q3 를 채우면
        #    다른 타입이 밀리고, 사람은 같은 문서의 여러 판본을 차례로 보게 된다.
        if store.pending(self._conn, ctype.id):
            return self._record(
                ctype, store.Outcome.PENDING_REVIEW, "앞 초안이 아직 검수 대기다"
            )

        trigger = evaluate_trigger(self._conn, ctype, source_commit=source_commit)
        if not trigger.due:
            return Result(type_id=ctype.id, outcome=store.Outcome.NOT_DUE)

        # ② 근거를 모은다. 없으면 지어내지 않는다.
        items, stale = self._read_input(ctype)
        if not items:
            detail = (
                f"쓸 근거가 없다 — 낡은 항목 {len(stale)}건은 뺐다"
                if stale
                else "쓸 근거가 없다"
            )
            return self._record(
                ctype, store.Outcome.NO_GROUNDING, detail, excluded_stale=stale
            )

        # ③ 코드 변경이 부른 주기인데 지식이 아직 그 커밋을 읽지 않았으면 기다린다.
        #    지금 돌리면 같은 글이 나오고, 그것을 갱신이라 부르면 고쳤다는 기록만
        #    남는다 (§6.6.3 과 같은 순서).
        if stale and trigger.threshold and not trigger.periodic:
            return self._record(
                ctype,
                store.Outcome.HELD,
                f"코드가 바뀌었지만 지식이 아직 낡다 ({len(stale)}건) — "
                "ingest 가 따라잡은 뒤에 만든다",
                excluded_stale=stale,
            )

        previous = store.current(self._conn, ctype.id) if ctype.living else None
        return self._generate(ctype, items, previous, trigger, source_commit, stale)

    # --- 2단계 조회 ----------------------------------------------------------

    def _read_input(self, ctype: ContentType) -> tuple[list, tuple[str, ...]]:
        """주 입력을 읽는다. **읽개는 주 입력 종류마다 하나다** — 타입마다가 아니다."""
        if ctype.input is not Input.KNOWLEDGE:
            # **반쪽으로 만들지 않는다.** `both` 를 지식베이스만으로 돌리면 칼럼이
            # 관찰(§7.6.2) 없이 나가는데, 관찰을 생략한 권고는 그 순간 의견이 된다.
            raise UnsupportedInput(
                f"{ctype.id}: 주 입력 {ctype.input} 의 읽개가 아직 없다 — QnA 통계는 "
                "WBS-4.7.1 이 붙인다. 선언은 있으나 아직 만들 수 없다"
            )

        stored, _broken = self._repo.scan()
        # **소스코드 파생 지식이 중심이다** (§7.2). QnA 승격분만으로 쓴 가이드는
        # 사람들이 물어본 것의 모음이지 시스템 사용법이 아니다.
        items = [s.item for s in stored if any(p.commit for p in s.item.provenance)]
        stale = tuple(i.id for i in items if i.stale)
        return [i for i in items if not i.stale], stale

    # --- 3단계 생성 ----------------------------------------------------------

    def _generate(
        self,
        ctype: ContentType,
        items: list,
        previous: store.ContentDraft | None,
        trigger: Trigger,
        source_commit: str | None,
        stale: tuple[str, ...],
    ) -> Result:
        if self._harness is None:
            return self._record(
                ctype, store.Outcome.GENERATION_FAILED, "생성기가 없다", excluded_stale=stale
            )

        prompt = build_prompt(ctype, items, previous)
        allowed = {i.id for i in items}
        try:
            parsed = parse_document(self._harness.run(prompt).text, allowed)
        except (AgentOutputError, RuntimeError) as exc:
            # **하네스 실패까지 여기서 받는다.** 콘텐츠 제작은 배치 주기의 한 걸음일
            # 뿐인데, 모델이 응답하지 않았다고 예외가 위로 올라가면 **그 tick 의
            # 나머지 타입이 통째로 멈춘다** — 라이브에서 pi 타임아웃으로 밟았다.
            # 실패는 기록으로 남고 다음 주기에 다시 시도된다.
            return self._record(
                ctype,
                store.Outcome.GENERATION_FAILED,
                f"생성하지 못했다 — {exc}",
                excluded_stale=stale,
            )
        if parsed is None:
            return self._record(
                ctype,
                store.Outcome.GENERATION_FAILED,
                "근거를 가리키지 않은 글이라 초안으로 받지 않는다 (D3)",
                excluded_stale=stale,
            )

        title, body, grounding = parsed
        # **바뀐 것이 없으면 초안을 만들지 않는다.** 대기열이 빈 판정으로 채워지면
        # 실제로 볼 것이 그 사이에 묻힌다 (§8.6).
        if previous is not None and body.strip() == previous.body.strip():
            return self._record(
                ctype,
                store.Outcome.UNCHANGED,
                f"{trigger.reason} 으로 돌렸으나 직전 판본과 같다",
                generated=True,
                commit=source_commit,
                excluded_stale=stale,
            )

        # **티켓을 함께 발행한다** (§6.4.3, FR-45). Q3 는 작업 대기열이라 초안
        # 하나가 처리 하나이고, 티켓이 있어야 순위(`next_up`)에도 오른다 — 없으면
        # 콘텐츠만 "다음에 볼 것"에서 빠져 §8.2 가 걱정한 자리로 돌아간다.
        ticket = ticket_domain.issue(self._conn, source=ticket_domain.Source.CONTENT)
        draft_id = store.save(
            self._conn,
            type_id=ctype.id,
            title=title or ctype.title,
            body=body,
            grounding=grounding,
            based_on=previous.id if previous else None,
            generated_by=self._generated_by,
            ticket_id=ticket.id,
        )
        result = self._record(
            ctype,
            store.Outcome.PRODUCED,
            f"{trigger.reason} — 근거 {len(grounding)}건"
            + (f", 낡은 항목 {len(stale)}건 제외" if stale else "")
            + (
                f", 본문 {churn(previous.body, body) * 100:.0f}% 변경"
                if previous
                else ""
            ),
            generated=True,
            commit=source_commit,
            excluded_stale=stale,
        )
        result.draft_id = draft_id
        result.grounding = grounding
        if previous:
            result.diff = diff_of(previous.body, body)
            result.churn = churn(previous.body, body)
        return result

    def _record(
        self,
        ctype: ContentType,
        outcome: store.Outcome,
        detail: str,
        *,
        generated: bool = False,
        commit: str | None = None,
        excluded_stale: tuple[str, ...] = (),
    ) -> Result:
        store.record_run(
            self._conn,
            type_id=ctype.id,
            outcome=outcome,
            generated=generated,
            commit=commit,
            detail=detail,
        )
        return Result(
            type_id=ctype.id,
            outcome=outcome,
            detail=detail,
            excluded_stale=excluded_stale,
        )
