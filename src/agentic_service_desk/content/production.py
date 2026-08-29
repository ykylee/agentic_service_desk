"""콘텐츠 제작 — 살아있는 문서를 만들고 갱신한다 (WBS-4.6.2·4.7.1, FR-36·37·43).

**새 파이프라인이 아니다** (§7.1, D10). 답변과 같은 5단계를 트리거만 바꿔 탄다 —
질문 대신 주기·임계가 부르고, 조회 대상이 질문이 아니라 **타입이 선언한 주 입력**이다.
여기까지가 1~3단계이고 4·5단계(검수·게재)는 4.6.4·4.6.3 이 잇는다.

## 입력 읽개는 타입마다가 아니라 주 입력 종류마다 하나다

FR-42 는 새 타입 추가에 코드 변경이 없어야 한다고 한다. 그것이 성립하는 이유는
읽개가 **셋뿐**이기 때문이다 — 지식베이스 · QnA 통계 · 둘 다. 타입이 넷에서 열이
되어도 읽개는 늘지 않는다. **셋이 다 붙었다** — 지식베이스(가이드) · QnA 통계(FAQ) ·
둘 다(칼럼·뉴스레터).

## 가이드가 FAQ 보다 먼저인 이유 (D50)

FAQ 는 QnA 통계에 종속돼 질문이 쌓여야 만들 수 있지만, **가이드는 소스코드만으로
만들 수 있다.** 1국면에 실제로 낼 수 있는 콘텐츠가 이것 하나다.

## FAQ — 무엇을 다룰지와 무엇에 기대어 쓸지는 다른 물음이다 (WBS-4.7.1, FR-37)

가이드는 그 둘이 한 원천이다. 지식베이스를 훑어 **거기 있는 것을** 쓴다.
FAQ 는 갈린다.

| | 무엇을 다루는가 | 무엇에 기대어 쓰는가 |
|---|---|---|
| 가이드 | 지식베이스 | 지식베이스 |
| **FAQ** | **반복 질문 분포** (§7.2) | **지식베이스** |

주 입력이 정하는 것은 앞 칸이다. 뒤 칸은 답변 파이프라인의 2단계와 같은 일이라
(§5.1) 여기서도 같은 검색을 쓴다 — 반복 질문 하나가 질의가 되고, 걸린 지식 항목이
그 문답의 근거가 된다.

**봇의 미검증 답변을 FAQ 본문으로 옮기지 않는 것이 이 분리의 핵심이다.** §5.3 은
반복 질문 탐지에 봇 답변을 **포함**하라고 하는데, 그것은 "무엇이 자주 묻히는가"가
작성자와 무관하기 때문이지 그 답을 그대로 실으라는 뜻이 아니다. 실으면 §5.3 이
막으려던 되먹임이 ingest 보다 나쁜 자리에서 일어난다 — 지식베이스에 고이는 것이
아니라 **이용자에게 바로 나간다.**

**반복되는데 근거가 없는 질문은 빼고, 뺐다고 말한다.** 그것은 FAQ 의 실패가 아니라
**지식 공백**이다 (§6.2) — 가장 자주 묻는데 지식베이스가 답을 모르는 자리이므로
ingest 우선순위로 되먹여야 할 것이지, 지어내서 채울 것이 아니다 (D3, FR-18).

## 칼럼 — 읽는 것이 둘인 이유는 쓸 수 있는 것이 둘이기 때문이다 (WBS-4.7.2, FR-40·41)

칼럼은 세 등급으로 갈린다 (§7.6.1) — **해설**은 허용, **권고**는 관찰을 함께 밝히는
조건으로 허용, **의견**은 금지다. 그 둘이 읽개의 둘과 정확히 짝을 이룬다.

| 쓸 수 있는 것 | 무엇에 기대는가 |
|---|---|
| **해설** — "이 기능은 이렇게 동작하고, 이런 배경에서 바뀌었다" | 지식베이스 |
| **권고** — "이 문의가 N건 있었고, Y 로 설정하면 피할 수 있다" | **관찰** (QnA 통계) |

`both` 가 반쪽으로 돌면 안 되는 이유가 여기서 드러난다. 관찰 없이 지식베이스만으로
권고를 쓰면 **그 순간 의견이 된다** (§7.6.2) — 그래서 4.7.1 까지 `both` 를
`UnsupportedInput` 으로 거부했다.

**저장소 히스토리는 별도 읽개가 아니다.** §7.2 는 칼럼의 주 입력에 히스토리를 함께
적었지만, 그것은 **이미 지식 항목 안에 있다** — §2.2.1 이 커밋 메시지를 원천에 넣은
이유가 "왜 그렇게 바뀌었는가"였고 ingest 가 그것을 읽어 항목으로 만든다. 여기서 원문을
따로 꺼내면 **ingest 를 우회한 글이 발행물에 실리고**, 그것은 검수 화면이 대조할 수
없는 근거가 된다.

**관찰은 초안에 박힌다.** 발행물은 회수할 수 없는데(§7.3) 지금 다시 세면 숫자가
달라져 검수가 대조할 수 없다 — 답변의 근거 버전 고정(D20)과 같은 이유다. 다만
`grounding` 과는 다른 열에 둔다: 저쪽은 지식 항목 id 라 stale 판정이 걸려 있고,
관찰은 **갱신되는 항목이 아니라 그 시점의 사실**이다.

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
**반복 질문 임계는 기다리지 않는다**: 그것은 지식이 뒤처졌다는 신호가 아니라 사람들이
물었다는 신호이므로, 낡은 항목을 뺀 채로 만드는 것이 맞다.
"""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agentic_service_desk.content import qna_stats, store
from agentic_service_desk.content.registry import ContentType, Input
from agentic_service_desk.ingest.agent import AgentOutputError, Harness, extract_json
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Search
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

    uncovered: tuple[str, ...] = ()
    """반복되는데 지식베이스가 답을 모르는 질문 (FAQ 만). **FAQ 의 실패가 아니라
    지식 공백이다** (§6.2) — 가장 자주 묻는 자리이므로 ingest 우선순위로 되먹일
    것이지 지어내서 채울 것이 아니다."""

    repeats: int = 0
    """가장 많이 반복된 질문의 횟수 (FAQ 만). 임계가 왜 찼는지·왜 안 찼는지를
    화면과 로그가 말할 수 있어야 한다."""

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


SOURCE_CHANGED = "source_changed"
"""가이드의 임계 — 커서를 견주므로 값이 아니라 **커밋**으로 판정한다."""

REPEAT_QUESTIONS = "repeat_questions"
"""FAQ 의 임계 — 가장 많이 반복된 질문의 **횟수** (O37, WBS-4.7.1)."""

THRESHOLD_LABEL = {SOURCE_CHANGED: "코드 변경", REPEAT_QUESTIONS: "반복 질문"}


class UnknownThreshold(NotImplementedError):
    """선언된 임계를 아무도 재지 않는다. **조용히 안 도는 것으로 두지 않는다.**

    모르는 임계를 `False` 로 취급하면 그 타입은 주기가 올 때까지 — 주기가 없으면
    영영 — 돌지 않는데, 그 침묵은 "아직 안 찼다"와 구분되지 않는다. 선언의 오타가
    기능의 부재로 보이는 것이 이 고장의 성질이다.
    """


def evaluate_trigger(
    conn: sqlite3.Connection,
    ctype: ContentType,
    *,
    source_commit: str | None = None,
    signals: dict[str, float] | None = None,
    now: datetime | None = None,
) -> Trigger:
    """주기와 임계를 본다.

    **첫 제작은 언제나 돈다** — 한 번도 만든 적이 없으면 기다릴 직전 판본이 없다.

    임계는 둘로 갈린다. `source_changed` 는 **커서 비교**라 값이 없고, 나머지는
    **잰 값**과 선언의 `threshold_value` 를 견준다. 재는 것은 여기가 아니라 부르는
    쪽이다 (`signals`) — 트리거가 원천을 읽기 시작하면 "돌지 않기로 한 주기"에도
    질의가 돈다.
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

    threshold = _threshold_met(ctype, run, source_commit=source_commit, signals=signals)

    reasons = []
    if periodic:
        reasons.append(f"주기 {ctype.trigger.period_days}일")
    if threshold:
        reasons.append(THRESHOLD_LABEL.get(ctype.trigger.threshold, ctype.trigger.threshold))
    return Trigger(
        due=periodic or threshold,
        periodic=periodic,
        threshold=threshold,
        reason=" · ".join(reasons),
    )


def _threshold_met(
    ctype: ContentType,
    run: store.Run,
    *,
    source_commit: str | None,
    signals: dict[str, float] | None,
) -> bool:
    name = ctype.trigger.threshold
    if name is None:
        return False
    if name == SOURCE_CHANGED:
        return bool(source_commit) and source_commit != run.last_commit
    measured = (signals or {}).get(name)
    if measured is None:
        raise UnknownThreshold(
            f"{ctype.id}: 임계 {name!r} 를 재는 곳이 없다 — 선언의 오타이거나 "
            "아직 만들지 않은 신호다. 모르는 임계는 '아직 안 찼다'와 구분되지 않으므로 "
            "조용히 넘기지 않는다"
        )
    return measured >= (ctype.trigger.threshold_value or 0)


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

{type_rules}
{update_rules}

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{output_shape}"""

_SHAPE = """{
  "title": "문서 제목",
  "body": "문서 본문 (Markdown)",
  "grounding": ["k-..."]
}"""

_SHAPE_WITH_OBSERVATIONS = """{
  "title": "문서 제목",
  "body": "문서 본문 (Markdown)",
  "grounding": ["k-..."],
  "observations": ["obs-..."]
}

`observations` 에는 **본문이 실제로 밝힌 관찰의 번호만** 넣는다. 아래 목록에 없는
번호를 만들지 않고, 쓰지 않은 관찰을 넣지도 않는다 — 이 목록이 나가는 글에 **근거로
함께 실리므로**, 쓰지 않은 것을 넣으면 읽는 사람은 그 조언이 그것에 기댄 줄로 읽는다."""

_TYPE_RULES_FAQ = """이 문서는 **문답 모음**이다. 아래에 실제로 반복해서 들어온 질문과,
각 질문에 대해 검색된 지식 항목이 있다. **자주 물은 것부터** 놓았고, 그 순서를 지킨다.

**질문은 일반화해서 쓴다.** 물어온 말을 그대로 옮기지 않는다 — 이름·사번·부서·금액·
날짜처럼 한 사람의 사정에 속하는 것은 싣지 않고, 여러 표현이 공통으로 묻는 것만
한 줄로 다듬는다. FAQ 는 공개 문서라 물어본 사람이 드러나서는 안 된다.

**근거가 답하지 못하는 질문은 빼고 쓴다.** 자주 물었다는 것이 답을 안다는 뜻은
아니다 — 근거 없이 채운 문답은 반복 질문이라 특히 널리 읽힌다.

각 문항은 질문 한 줄과 그에 대한 답으로 쓴다."""

_TYPE_RULES_COLUMN = """이 문서는 **칼럼**이다. 쓸 수 있는 것과 쓸 수 없는 것이 갈린다.

- **해설** — "이 기능은 이렇게 동작하고, 이런 배경에서 바뀌었다". **쓴다.** 근거가
  있는 사실의 재구성이고, 이 시스템이 가장 잘하는 종류다.
- **권고** — "이 문의가 N건 있었고, 원인은 X 이며, Y 로 하면 피할 수 있다".
  **관찰을 함께 밝히면 쓴다.**
- **의견** — "이 방식이 더 낫습니다", "~하는 것이 옳습니다". **쓰지 않는다.**

**관찰을 생략하고 조언만 남기면 그 순간 의견이 된다.** 권고를 쓸 때는 아래 관찰
목록에 있는 것을 문장 안에서 밝힌다 — "지난 N일 동안 ...형태의 문의가 M건 있었다"처럼.
목록에 없는 관찰을 지어내지 않는다. 밝힐 관찰이 없으면 **권고를 쓰지 않고 해설만 쓴다.**

**관찰을 일반 법칙으로 늘리지 않는다.** "4건 있었다"는 사실이지만 "다들 그렇게
씁니다"는 사실이 아니다. **센 것보다 넓게 말하지 않는다.**

**조직의 방침처럼 쓰지 않는다.** 사내 채널로 나간 글의 "앞으로는 ~하십시오"는 정책
공지로 읽히는데, 이 글은 그런 권위를 가지지 않는다.

이번 회차가 다룰 주제를 근거 안에서 **골라서** 쓴다 — 가진 것을 전부 늘어놓는 것은
칼럼이 아니라 목록이다."""

_FIRST = """이번이 **첫 제작**이다. 근거가 다루는 범위 안에서 문서를 처음부터 쓴다."""

_UPDATE = """아래에 **직전 판본**이 있다. 그것을 **고쳐서** 낸다 — 처음부터 다시 쓰지
않는다. 살아있는 문서라 사람은 **바뀐 곳만** 검수하며, 통째로 달라지면 무엇이
바뀌었는지 볼 수 없어 검수가 전문 검수가 된다.

근거와 어긋나는 부분만 고치고, 근거가 새로 다루는 것을 더한다. **그대로 둘 수 있는
문장은 글자 그대로 둔다.**"""


def _item_block(item) -> str:  # noqa: ANN001
    return f"### {item.id} — {item.title}\n{item.body}"


def _question_blocks(covered: tuple[tuple, ...]) -> list[str]:
    """반복 질문과 그 근거를 나란히 놓는다.

    **근거를 질문 아래에 둔다.** 항목을 따로 늘어놓으면 어느 근거가 어느 질문에
    걸린 것인지 모델이 다시 짐작해야 하고, 그 짐작이 틀리면 엉뚱한 근거를 단 문답이
    나온다 — 검수자는 근거가 붙어 있다는 사실만 보고 넘어가기 쉽다.
    """
    blocks = []
    for index, (group, items) in enumerate(covered, start=1):
        heard = "\n".join(f'- "{v}"' for v in group.variants)
        blocks.append(
            f"## 질문 {index} — {group.count}회 물음 (해결 {group.resolved_count}회)\n"
            f"물어온 말:\n{heard}\n\n근거:\n"
            + "\n\n".join(_item_block(i) for i in items)
        )
    return blocks


def _head(
    ctype: ContentType, previous, type_rules: str, *, observations: bool = False
) -> str:  # noqa: ANN001
    return (
        _RULES.replace("{kind}", "살아있는 문서" if ctype.living else "발행물")
        .replace("{title}", ctype.title)
        .replace("{language}", LANGUAGE)
        .replace("{type_rules}", type_rules)
        .replace("{update_rules}", _UPDATE if previous else _FIRST)
        .replace(
            "{output_shape}", _SHAPE_WITH_OBSERVATIONS if observations else _SHAPE
        )
    )


def build_prompt(
    ctype: ContentType,
    items: list,
    previous: store.ContentDraft | None,
    *,
    covered: tuple[tuple, ...] = (),
    observations: tuple = (),
) -> str:
    """모델에게 줄 것. **주 입력에 따라 재료의 모양이 다르다.**

    지식베이스 입력은 항목을 늘어놓고, QnA 통계 입력은 **질문마다 그 근거를 달아**
    준다 — FAQ 는 문답 모음이라 어느 근거가 어느 문항의 것인지가 재료에 이미 있어야
    한다. `both` 는 항목과 **관찰**을 나란히 놓는다: 해설은 앞엣것에, 권고는 뒤엣것에
    기댄다 (§7.6.1·§7.6.2).
    """
    if covered:
        parts = [
            _head(ctype, previous, _TYPE_RULES_FAQ),
            "",
            "반복해서 들어온 질문과 그 근거:",
            *_question_blocks(covered),
        ]
        return _with_previous(parts, previous)

    if ctype.input is Input.BOTH:
        parts = [
            _head(
                ctype, previous, _TYPE_RULES_COLUMN, observations=bool(observations)
            ),
            "",
            "근거로 쓸 수 있는 지식 항목:",
            *[_item_block(i) for i in items],
            "",
            *_observation_block(observations),
        ]
        return _with_previous(parts, previous)

    parts = [
        _head(ctype, previous, ""),
        "",
        "근거로 쓸 수 있는 지식 항목:",
        *[_item_block(i) for i in items],
    ]
    return _with_previous(parts, previous)


def _observation_block(observations: tuple) -> list[str]:
    """관찰 목록. **비어 있으면 비어 있다고 말한다** (§7.6.2).

    조용히 빼면 모델은 관찰이 있는지 없는지 모른 채 권고를 쓰고, 그 권고는 관찰을
    밝히지 않았으므로 의견이 된다.
    """
    if not observations:
        return [
            "관찰된 것 (권고의 근거):",
            "- **없다.** 이번 기간에 셀 만한 반복이 없었다 — **권고를 쓰지 않고 "
            "해설만 쓴다.**",
        ]
    return [
        "관찰된 것 (권고의 근거):",
        *[f"- {o.text}" for o in observations],
    ]


def _with_previous(parts: list[str], previous: store.ContentDraft | None) -> str:
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


@dataclass(frozen=True)
class Document:
    """모델이 낸 것 중 **받아들인 것**."""

    title: str
    body: str
    grounding: tuple[str, ...]
    cited: tuple[str, ...] = ()
    """본문이 실제로 밝혔다고 신고한 관찰 (§7.6.2).

    **박힌 관찰 전부와 다르다.** 검수는 전부를 봐야 지어낸 관찰을 가려낼 수 있지만,
    나가는 글의 근거 목록에는 **쓴 것만** 실린다 — 쓰지 않은 관찰이 근거로 붙으면
    읽는 사람은 그 조언이 그것에 기댄 줄로 읽고, 검수자도 밝혀진 줄로 본다 (§5.6.1).
    """


def parse_document(
    text: str, allowed_ids: set[str], *, allowed_observations: set[str] = frozenset()
) -> Document | None:
    """응답을 문서로. `None` 이면 쓸 것이 없다는 결정이다.

    지어낸 근거 id 는 버린다 — 없는 것을 가리키는 근거는 Lint 의 끊어진 링크가 되고,
    그때는 이미 문서가 나간 뒤다. 하나도 남지 않으면 **근거에서 나온 글인지 알 수
    없으므로** 초안으로 받지 않는다 (D3). 관찰 번호도 같은 규칙으로 거른다.
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
    cited = tuple(
        dict.fromkeys(
            str(o)
            for o in (payload.get("observations") or [])
            if str(o) in allowed_observations
        )
    )
    return Document(title=title, body=body, grounding=grounding, cited=cited)


def diff_of(previous: str, current: str) -> str:
    """변경분. **세어서 만든다** (§5.5.5).

    모델에게 "무엇을 바꿨는가"를 묻지 않는 이유는 그 답을 검증할 수 없기 때문이다 —
    검수자가 믿고 넘기면 그 표시는 없는 것과 같아진다.
    """
    lines = difflib.unified_diff(
        previous.splitlines(), current.splitlines(), lineterm="", n=2
    )
    return "\n".join(lines)


# --- 2단계 조회 ---------------------------------------------------------------

FAQ_GROUNDING_LIMIT = 3
"""반복 질문 하나에 붙일 근거 항목의 수.

답변의 기본값보다 좁게 잡는다 — FAQ 한 문항은 한 가지를 짧게 답하는 자리이고,
후보를 넓히면 **한 문답에 관계가 옅은 항목이 근거로 달린다.**
"""

MAX_QUESTIONS = 20
"""한 주기에 다룰 반복 질문의 수. **넘친 것은 다음 주기로 미루고, 미뤘다고 말한다.**

살아있는 문서는 직전 판본이 다시 입력으로 들어가므로(§7.3) 문항이 무한히 늘면
프롬프트가 함께 자라고, 그보다 먼저 **변경분 검수가 사람이 볼 수 없는 크기가 된다**
(§5.5.5). 조용히 자르지 않는 이유는 잘린 채로도 화면이 "전부 다뤘다"처럼 보이기
때문이다.
"""


@dataclass
class Material:
    """2단계가 모아 온 것. **타입마다가 아니라 주 입력 종류마다 모양이 다르다.**"""

    items: list = field(default_factory=list)
    """근거로 쓸 수 있는 지식 항목. 이것이 곧 `grounding` 에 허용되는 id 다."""

    stale: tuple[str, ...] = ()
    covered: tuple[tuple, ...] = ()
    """(반복 질문, 근거 항목들). **FAQ 만 채운다** — 문답마다 근거가 따로 붙는다."""

    uncovered: tuple[str, ...] = ()
    """반복되는데 근거를 못 찾은 질문. **지식 공백이다** (§6.2)."""

    repeats: int = 0
    deferred: int = 0
    """한도를 넘어 이번에 다루지 않은 반복 질문의 수."""

    observations: tuple = ()
    """관찰된 현상 (`both` 만). **권고의 근거이고 초안에 박힌다** (§7.6.2, FR-41)."""


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
        search: Search | None = None,
    ) -> None:
        self._conn = conn
        self._repo = repo
        self._harness = harness
        self._generated_by = generated_by
        # **임베딩 없는 검색이 기본이다.** FAQ 의 근거 조회는 배치의 한 걸음일 뿐이라
        # 임베딩 제공자가 없다고 서면 안 된다 — 검색이 이미 그렇게 만들어져 있다.
        self._search = search or Search(repo=repo, conn=conn)

    def run(self, ctype: ContentType, *, source_commit: str | None = None) -> Result:
        # ① 앞 초안이 아직 대기 중이면 다시 만들지 않는다. 한 타입이 Q3 를 채우면
        #    다른 타입이 밀리고, 사람은 같은 문서의 여러 판본을 차례로 보게 된다.
        if store.pending(self._conn, ctype.id):
            return self._record(
                ctype, store.Outcome.PENDING_REVIEW, "앞 초안이 아직 검수 대기다"
            )

        # ② 임계를 재는 것은 트리거보다 먼저다. **재는 것은 싸고 조회는 비싸다** —
        #    반복 분포는 SQL 한 번이지만 근거 조회는 후보마다 지식베이스를 훑는다.
        #    그래서 싼 쪽만 트리거 앞에 두고 비싼 쪽은 트리거가 선 뒤에 돈다.
        groups = (
            qna_stats.detect(self._conn, since=_window_start(ctype))
            if _needs_qna(ctype)
            else []
        )
        signals = {REPEAT_QUESTIONS: float(qna_stats.peak(groups))} if _needs_qna(ctype) else None

        trigger = evaluate_trigger(
            self._conn, ctype, source_commit=source_commit, signals=signals
        )
        if not trigger.due:
            return Result(type_id=ctype.id, outcome=store.Outcome.NOT_DUE)

        # ③ 근거를 모은다. 없으면 지어내지 않는다.
        material = self._read_input(ctype, groups)
        if not material.items:
            return self._record(
                ctype,
                store.Outcome.NO_GROUNDING,
                _no_grounding_detail(ctype, material),
                excluded_stale=material.stale,
                material=material,
            )

        # ④ 코드 변경이 부른 주기인데 지식이 아직 그 커밋을 읽지 않았으면 기다린다.
        #    지금 돌리면 같은 글이 나오고, 그것을 갱신이라 부르면 고쳤다는 기록만
        #    남는다 (§6.6.3 과 같은 순서).
        #
        #    **이 기다림은 `source_changed` 만의 것이다.** 반복 질문이 임계에 닿은
        #    것은 지식이 뒤처졌다는 신호가 아니라 사람들이 물었다는 신호이므로,
        #    낡은 항목을 뺀 채로 만드는 것이 맞다.
        if (
            material.stale
            and trigger.threshold
            and not trigger.periodic
            and ctype.trigger.threshold == SOURCE_CHANGED
        ):
            return self._record(
                ctype,
                store.Outcome.HELD,
                f"코드가 바뀌었지만 지식이 아직 낡다 ({len(material.stale)}건) — "
                "ingest 가 따라잡은 뒤에 만든다",
                excluded_stale=material.stale,
                material=material,
            )

        previous = store.current(self._conn, ctype.id) if ctype.living else None
        return self._generate(ctype, material, previous, trigger, source_commit)

    # --- 2단계 조회 ----------------------------------------------------------

    def _read_input(self, ctype: ContentType, groups: list) -> Material:
        """주 입력을 읽는다. **읽개는 주 입력 종류마다 하나다** — 타입마다가 아니다."""
        if ctype.input is Input.KNOWLEDGE:
            return self._from_knowledge()
        if ctype.input is Input.QNA_STATS:
            return self._from_qna_stats(ctype, groups)
        if ctype.input is Input.BOTH:
            return self._from_both(ctype, groups)
        raise UnsupportedInput(f"{ctype.id}: 주 입력 {ctype.input} 의 읽개가 없다")

    def _from_knowledge(self) -> Material:
        stored, _broken = self._repo.scan()
        # **소스코드 파생 지식이 중심이다** (§7.2). QnA 승격분만으로 쓴 가이드는
        # 사람들이 물어본 것의 모음이지 시스템 사용법이 아니다.
        items = [s.item for s in stored if any(p.commit for p in s.item.provenance)]
        stale = tuple(i.id for i in items if i.stale)
        return Material(items=[i for i in items if not i.stale], stale=stale)

    def _from_both(self, ctype: ContentType, groups: list) -> Material:
        """지식베이스와 관찰을 함께 읽는다 (§7.2, §7.6).

        **가이드와 달리 QnA 승격 항목도 받는다.** 가이드는 소스코드 파생 지식이
        중심이라 그것을 걸렀지만(§7.2), 칼럼의 해설은 "왜 그렇게 정해졌는가"를
        다루므로 티켓 해결에서 올라온 항목이 그 자리의 재료다.

        **관찰이 없어도 만든다.** 그때는 해설만 쓰는 회차이고, 프롬프트가 그렇게
        말한다 — 관찰이 없다는 사실을 감추면 모델이 밝힐 것 없는 권고를 쓴다.
        """
        stored, _broken = self._repo.scan()
        items = [s.item for s in stored]
        stale = tuple(i.id for i in items if i.stale)
        return Material(
            items=[i for i in items if not i.stale],
            stale=stale,
            observations=qna_stats.observations(
                groups, window_days=ctype.trigger.period_days or 0
            ),
        )

    def _from_qna_stats(self, ctype: ContentType, groups: list) -> Material:
        """반복 질문이 **무엇을 다룰지**를 정하고, 검색이 **무엇에 기대어 쓸지**를 정한다.

        답변 파이프라인의 2단계와 같은 검색을 쓴다 (§5.1) — 반복 질문 하나가 질의가
        되고 걸린 항목이 그 문답의 근거가 된다. **못 찾은 질문은 빼되 세어 둔다**:
        자주 묻는데 지식베이스가 모르는 자리가 곧 지식 공백이다 (§6.2).

        질의로는 **가장 최근 표현 하나**를 쓴다. 여러 표현을 이어 붙이면 낱말이
        늘어 표현 사전이 헐겁게 걸리는데, 그때 붙는 근거는 관계가 옅다 — 답변이
        질문 하나로 찾는 것과 같은 자리에 둔다.
        """
        minimum = int(ctype.trigger.threshold_value or 1)
        picked = qna_stats.candidates(groups, minimum=minimum)
        deferred = max(0, len(picked) - MAX_QUESTIONS)

        covered: list[tuple] = []
        uncovered: list[str] = []
        stale: list[str] = []
        for group in picked[:MAX_QUESTIONS]:
            hits = self._search.find(group.representative, limit=FAQ_GROUNDING_LIMIT)
            stale += [h.item.id for h in hits if h.is_stale]
            fresh = [h.item for h in hits if not h.is_stale]
            if fresh:
                covered.append((group, fresh))
            else:
                uncovered.append(group.representative)

        items: dict[str, object] = {}
        for _group, found in covered:
            for item in found:
                items.setdefault(item.id, item)
        return Material(
            items=list(items.values()),
            stale=tuple(dict.fromkeys(stale)),
            covered=tuple(covered),
            uncovered=tuple(uncovered),
            repeats=qna_stats.peak(groups),
            deferred=deferred,
        )

    # --- 3단계 생성 ----------------------------------------------------------

    def _generate(
        self,
        ctype: ContentType,
        material: Material,
        previous: store.ContentDraft | None,
        trigger: Trigger,
        source_commit: str | None,
    ) -> Result:
        stale = material.stale
        if self._harness is None:
            return self._record(
                ctype,
                store.Outcome.GENERATION_FAILED,
                "생성기가 없다",
                excluded_stale=stale,
                material=material,
            )

        prompt = build_prompt(
            ctype,
            material.items,
            previous,
            covered=material.covered,
            observations=material.observations,
        )
        allowed = {i.id for i in material.items}
        observable = {o.id for o in material.observations}
        try:
            parsed = parse_document(
                self._harness.run(prompt).text,
                allowed,
                allowed_observations=observable,
            )
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
                material=material,
            )
        if parsed is None:
            return self._record(
                ctype,
                store.Outcome.GENERATION_FAILED,
                "근거를 가리키지 않은 글이라 초안으로 받지 않는다 (D3)",
                excluded_stale=stale,
                material=material,
            )

        title, body, grounding = parsed.title, parsed.body, parsed.grounding
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
                material=material,
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
            observations=tuple(
                o.as_dict() | {"cited": o.id in parsed.cited}
                for o in material.observations
            ),
        )
        result = self._record(
            ctype,
            store.Outcome.PRODUCED,
            f"{trigger.reason} — 근거 {len(grounding)}건"
            + (f", 반복 질문 {len(material.covered)}건" if material.covered else "")
            + (f", 관찰 {len(material.observations)}건" if material.observations else "")
            + (f", 낡은 항목 {len(stale)}건 제외" if stale else "")
            + (
                f", 본문 {churn(previous.body, body) * 100:.0f}% 변경"
                if previous
                else ""
            ),
            generated=True,
            commit=source_commit,
            excluded_stale=stale,
            material=material,
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
        material: Material | None = None,
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
            uncovered=material.uncovered if material else (),
            repeats=material.repeats if material else 0,
        )


def _window_start(ctype: ContentType) -> str | None:
    """관찰을 셀 기간의 시작. **발행물만 창을 둔다.**

    칼럼은 회차라 그 회차가 다루는 기간이 있고, "지난 30일 동안"이라는 말이 성립하려면
    그 안에서 세야 한다 (§7.6.2). FAQ 는 살아있는 문서라 창이 없다 — 오래전부터
    반복된 것도 지금 자주 묻히면 여전히 FAQ 다.
    """
    if ctype.input is not Input.BOTH or not ctype.trigger.period_days:
        return None
    return (datetime.now(UTC) - timedelta(days=ctype.trigger.period_days)).isoformat()


def _needs_qna(ctype: ContentType) -> bool:
    """이 타입이 QnA 분포를 봐야 하는가.

    주 입력이 QnA 통계이거나, 임계가 그것을 재라고 선언했거나 — **둘은 같이 오는 것이
    보통이지만 하나만 선언될 수도 있다.** 주기로만 도는 FAQ 파생 타입이 그렇다.
    """
    return (
        ctype.input in (Input.QNA_STATS, Input.BOTH)
        or ctype.trigger.threshold == REPEAT_QUESTIONS
    )


def _no_grounding_detail(ctype: ContentType, material: Material) -> str:
    """왜 못 만들었는지. **"근거가 없다"로 뭉치지 않는다.**

    FAQ 가 비는 이유는 셋이고 대응이 다르다 — 아직 반복이 없는 것은 기다릴 일,
    반복은 있는데 지식이 없는 것은 **ingest 우선순위**의 일(§6.2), 근거가 낡은 것은
    Lint 와 ingest 의 일이다. 한 문장으로 뭉개면 운영자가 무엇을 해야 하는지 모른다.
    """
    if ctype.input is not Input.QNA_STATS:
        return (
            f"쓸 근거가 없다 — 낡은 항목 {len(material.stale)}건은 뺐다"
            if material.stale
            else "쓸 근거가 없다"
        )
    minimum = int(ctype.trigger.threshold_value or 1)
    if not material.covered and not material.uncovered:
        return (
            f"반복 {minimum}회에 닿은 질문이 없다 (가장 많이 반복된 것 "
            f"{material.repeats}회) — 재료가 없는 것이지 고장이 아니다"
        )
    return (
        f"반복 질문 {len(material.uncovered)}건이 임계에 닿았지만 지식베이스가 "
        "답을 모른다 — **지식 공백이다** (§6.2). 지어내지 않고 ingest 를 기다린다"
        + (f", 낡은 항목 {len(material.stale)}건은 뺐다" if material.stale else "")
    )
