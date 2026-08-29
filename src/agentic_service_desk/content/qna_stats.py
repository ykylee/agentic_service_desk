"""반복 질문 탐지 — FAQ 의 주 입력 (WBS-4.7.1, FR-37, §7.2).

FAQ 가 다른 타입과 다른 점은 **무엇을 쓸지 지식베이스가 정하지 않는다**는 것이다.
가이드는 소스코드 파생 지식을 훑어 쓰지만, FAQ 는 **사람들이 실제로 반복해서 물은
것**만 다룬다 — 그것이 §7.2 가 FAQ 의 주 입력을 "QnA 통계 (반복 질문 분포)"로 적은
뜻이고, FAQ 가 가이드보다 뒤에 오는 이유이기도 하다 (D50, §1.5.1).

## 세는 것과 쓰는 것을 나눈다

여기가 §5.3 의 표에서 가장 헷갈리기 쉬운 자리다.

| 용도 | 봇 답변 (미해결) | 근거 |
|---|---|---|
| Ingest · 승격 | **제외** | 검증되지 않은 자기 산출물이다 |
| **반복 질문 탐지 · FAQ 후보** | **포함** | "무엇이 자주 묻히는가"는 **작성자와 무관하다** |

그래서 이 모듈은 산출물 필터를 지나지 않는다 — 필터의 docstring 이 "통계를 내는
쪽은 이 문을 쓰지 않는다"고 밝힌 그 자리다. 봇이 답했든 아무도 답하지 않았든,
**같은 질문이 세 번 들어왔다는 사실은 그대로 사실이다.**

다만 여기서 나오는 것은 **무엇을 다룰지**뿐이다. 그 질문에 **뭐라고 답할지**는
지식베이스가 정한다(`production._read_input`) — 봇의 미검증 답변을 FAQ 본문으로
옮기면 §5.3 이 막으려던 되먹임이 ingest 보다 더 나쁜 자리에서 일어난다. 그것은
지식베이스 안에 고이는 것이 아니라 **이용자에게 바로 나간다.**

## 개인정보를 애초에 읽지 않는다 (PO-3)

질문 원문은 읽지만 `asker_account` 는 읽지 않는다. FAQ 는 공개 문서라 질문자가
누구인지가 실려서는 안 되는데, **프롬프트에 넣지 않는 것보다 꺼내 오지 않는 것이
확실하다.** 질문 본문에 남은 개인·상황 요소(사번·금액)는 프롬프트가 일반화를
요구하고, 새어 나간 수치는 Q3 검수의 P1 이 잡는다 — 근거 원문에 없는 수치이기 때문이다.

## 묶는 방법 — 형태소 분석기를 쓰지 않는다

ADR-003 제약 1 이 여기에도 걸린다. 대신 **앞이 어간**이라는 것만 가정한다 —
"한도가"와 "한도는"은 뒤 한 글자가 다르고, `approval` 과 `approvals` 도 그렇다.
전제가 맞지 않는 언어에서는 반복이 **덜 묶이고, 덜 묶이는 것이 이 자리에서는 손해다**
(`SIMILARITY` 참고) — 임계에 닿지 않은 반복은 조용히 사라져 "물어본 사람이 없다"와
같은 모양이 된다. 그 언어가 실제로 들어올 때 다시 볼 일이다.

**낱말 하나가 겹친 것은 같은 질문이라는 증거가 아니다.** 질문에는 개념을 부르는 말만
있는 것이 아니라 질문의 틀("어떻게", "하나요")도 있다 — 검색에서 `MIN_VOCAB_MATCHES`
가 막은 것과 같은 고장이고, 라이브에서 실제로 밟았다. 그래서 둘을 요구한다.

불용어 목록으로 틀 낱말을 걷어내지 않는 이유가 하나 더 있다. `search._drop_indistinct`
는 "절반 넘는 항목을 가리키는 말"을 버리는데, **여기서는 그 셈이 거꾸로 돈다** —
같은 질문이 반복될수록 그 질문의 낱말이 전체의 절반을 넘어가고, 그러면 **가장 자주
물은 것부터 지워진다.** 반복을 세는 자리에서 빈도로 낱말을 버릴 수는 없다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.knowledge.search import tokenize

MIN_SHARED = 2
"""같은 질문으로 보려면 이만큼의 낱말이 함께 맞아야 한다.

하나로 두면 "어떻게" 하나로 VPN 질문과 결재 질문이 한 묶음이 된다 (§검색의 라이브
사례와 같은 고장). 실제로 같은 질문의 다른 표현은 여러 낱말이 함께 맞는다.
"""

SIMILARITY = 0.6
"""겹침 비율의 하한. **짧은 쪽의 낱말이 얼마나 덮이는가**로 잰다.

합집합(Jaccard)도, 양쪽 비율의 최솟값도 아니다. 어느 쪽을 쓰든 **한쪽이 사정을 길게
쓰면 비율이 떨어진다** — 라이브에서 그것으로 같은 주제가 갈렸다:

    "결재 한도가 어떻게 정해지나요"
    "결재 한도는 어떻게 정해지는지 알고 싶습니다"   ← 최솟값 0.5 로 따로 묶였다

뒤엣것은 앞엣것에 인사말이 붙었을 뿐인데, 늘어난 낱말이 자기 쪽 분모를 키워 같은
질문을 다른 질문으로 만들었다. 짧은 쪽 기준으로 재면 0.75 다.

**덜 묶는 쪽이 안전하지 않다는 것이 이 자리의 특이점이다.** 잘못 묶이면 사람이 Q3
전수 검수에서 본다(FR-39). 덜 묶이면 임계에 영영 닿지 않아 **FAQ 가 만들어지지
않는데**, 그 침묵은 "물어본 사람이 없다"와 구분되지 않는다 — O37 이 경계한 그 자리다.
"""

MAX_VARIANTS = 5
"""프롬프트에 실을 표현의 수. 전부 실으면 반복이 많은 질문 하나가 프롬프트를 덮는다."""


def _akin(a: str, b: str) -> bool:
    """조사·어미 변형을 **앞 어간**으로 넘는다.

    검색의 `_matches`(양방향 부분 일치)를 쓰지 않는 이유가 있다. 저쪽은 한쪽이
    문서라 "한도"처럼 어간 그대로가 들어 있지만, **질문끼리 견줄 때는 양쪽 다 굴절돼
    있다** — "한도가"와 "한도는"은 서로를 품지 않아 부분 일치로는 못 넘는다.
    """
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        return True
    common = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        common += 1
    # 뒤 한 글자만 다른 것까지 받는다. 두 글자를 받으면 "한도"와 "한국"이 붙는다.
    return common >= 2 and common >= len(shorter) - 1


def _overlap(left: set[str], right: set[str]) -> int:
    return sum(1 for t in left if any(_akin(t, u) for u in right))


def similarity(left: set[str], right: set[str]) -> float:
    """짧은 쪽의 낱말이 얼마나 덮이는가. 낱말이 없으면 0 이다."""
    if not left or not right:
        return 0.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return _overlap(shorter, longer) / len(shorter)


@dataclass(frozen=True)
class Question:
    """질문 하나. **누가 물었는지는 들고 오지 않는다** (PO-3)."""

    id: str
    text: str
    created_at: str
    resolved: bool


@dataclass
class RepeatQuestion:
    """같은 것을 묻는 질문들의 묶음 — **FAQ 한 문항의 재료**."""

    questions: list[Question] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    """대표 질문의 낱말. **묶음이 커져도 넓어지지 않는다** — 넓히면 한 묶음이
    사슬처럼 번져 관계없는 질문까지 빨아들인다."""

    @property
    def count(self) -> int:
        return len(self.questions)

    @property
    def resolved_count(self) -> int:
        return sum(1 for q in self.questions if q.resolved)

    @property
    def representative(self) -> str:
        """**가장 최근 표현**이다. 이용자가 지금 그것을 부르는 말에 가깝다."""
        return self.questions[-1].text

    @property
    def variants(self) -> tuple[str, ...]:
        """프롬프트에 실을 표현들. 최근 것부터, 같은 말은 한 번만.

        여럿을 주는 이유는 **일반화의 재료**이기 때문이다 (PO-3) — 표현이 하나뿐이면
        모델이 그 한 사람의 사정을 그대로 옮겨 적는다.
        """
        seen: dict[str, None] = {}
        for q in reversed(self.questions):
            seen.setdefault(" ".join(q.text.split()), None)
        return tuple(seen)[:MAX_VARIANTS]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions)


def load_questions(
    conn: sqlite3.Connection, *, since: str | None = None
) -> list[Question]:
    """물어온 것 전부. **거르지 않는다** (§5.3).

    `since` 는 **발행물이 쓴다.** 칼럼은 회차라 그 회차가 다루는 기간이 있고, 관찰이
    "지난 30일 동안"이라는 말이 되려면 그 기간 안에서 세야 한다 (§7.6.2). FAQ 는
    살아있는 문서라 창을 두지 않는다 — 오래전에 반복된 것도 지금 자주 묻히면 여전히
    FAQ 다.

    원천이 둘이다 — 모 시스템에서 온 것과 담당자가 직접 등록한 것 (§1.4.3). 후자를
    빼면 **메신저로 오간 반복이 통계에서 사라지고**, 그것을 흡수하려고 수동 등록을
    만든 의미가 없어진다.

    해결 여부는 세되 **거르는 데 쓰지 않는다.** 반복되는데 아직 아무도 못 푼 질문이
    FAQ 후보에서 빠지면, 가장 답이 필요한 것이 가장 늦게 다뤄진다.
    """
    rows = conn.execute(
        """
        SELECT q.id AS id,
               TRIM(COALESCE(q.title, '') || ' ' || q.body) AS text,
               q.created_at AS created_at,
               CASE WHEN r.resolved = 1 OR i.resolution_grade IS NOT NULL
                    THEN 1 ELSE 0 END AS resolved
        FROM raw_question q
        LEFT JOIN raw_resolution r ON r.question_id = q.id
        LEFT JOIN qna_item i ON i.parent_question_id = q.id
        WHERE (? IS NULL OR q.created_at >= ?)
        UNION ALL
        SELECT m.qna_item_id AS id, m.question AS text, m.registered_at AS created_at,
               CASE WHEN i.resolution_grade IS NOT NULL THEN 1 ELSE 0 END AS resolved
        FROM manual_entry m
        JOIN qna_item i ON i.id = m.qna_item_id
        WHERE (? IS NULL OR created_at >= ?)
        ORDER BY created_at, id
        """,
        (since, since, since, since),
    ).fetchall()
    return [
        Question(
            id=row["id"],
            text=row["text"],
            created_at=row["created_at"],
            resolved=bool(row["resolved"]),
        )
        for row in rows
        if (row["text"] or "").strip()
    ]


def cluster(questions: list[Question]) -> list[RepeatQuestion]:
    """묶는다. **대표와만 견준다** — 사슬처럼 번지지 않게.

    단일 연결(single-linkage) 군집화를 쓰면 A~B 가 닮고 B~C 가 닮았다는 이유로
    A 와 C 가 한 묶음이 되는데, 그렇게 이어 붙이면 **큰 묶음 하나가 결국 전부를
    삼킨다.** 들어온 순서대로 훑으며 각 묶음의 대표하고만 견주면 그 일이 없다.

    순서가 결과를 정하므로 `load_questions` 가 시각·id 로 정렬해 온다 — 같은 입력이
    같은 묶음을 내야 화면의 후보가 볼 때마다 달라지지 않는다.
    """
    groups: list[RepeatQuestion] = []
    for question in questions:
        tokens = set(tokenize(question.text))
        if not tokens:
            continue
        for group in groups:
            if (
                _overlap(tokens, group.tokens) >= MIN_SHARED
                and similarity(tokens, group.tokens) >= SIMILARITY
            ):
                group.questions.append(question)
                break
        else:
            groups.append(RepeatQuestion(questions=[question], tokens=tokens))
    return groups


def detect(
    conn: sqlite3.Connection, *, since: str | None = None
) -> list[RepeatQuestion]:
    """읽고 묶는다. **한 주기에 한 번만 부른다** — 트리거와 재료가 같은 셈을 본다.

    두 번 세면 트리거가 본 분포와 실제로 쓴 분포가 어긋날 수 있고, 그때 "돌긴
    도는데 재료는 없는" 주기가 생긴다.
    """
    return cluster(load_questions(conn, since=since))


def peak(groups: list[RepeatQuestion]) -> int:
    """가장 많이 반복된 질문의 횟수. **임계 트리거가 보는 값이다.**

    후보 *건수*가 아니라 *반복 횟수*인 것이 중요하다 — O37 이 물은 것은 "몇 회
    반복부터 후보로 볼 것인가"이고, 그래서 선언의 한 숫자가 **트리거이자 후보
    자격**이다. 둘을 다른 숫자로 두면 임계의 뜻이 자리마다 달라진다.
    """
    return max((g.count for g in groups), default=0)


def candidates(groups: list[RepeatQuestion], *, minimum: int) -> list[RepeatQuestion]:
    """임계에 닿은 묶음만. **많이 물은 것부터.**

    `minimum` 은 타입 선언의 `threshold_value` 다 (O37). **이 규모에서는 2~3회도
    후보로 볼 근거가 있다** — 대규모 서비스의 감각으로 임계를 잡으면 FAQ 는 영영
    만들어지지 않는다 (§1.4.2). 실데이터로 다시 정할 값이라 코드가 아니라 선언에 있다.
    """
    found = [g for g in groups if g.count >= max(minimum, 1)]
    found.sort(key=lambda g: (-g.count, g.representative))
    return found


# --- 관찰 — 권고의 근거 (§7.6.2, FR-41) ---------------------------------------

OBSERVATION_MIN = 2
"""관찰로 셀 최소 반복. **두 번은 관찰이고 한 번은 일화다.**

권고가 조건부로 허용되는 근거는 "QnA 통계 자체가 사실"이라는 것인데(§7.6.2), 한 건은
분포가 아니라 사례다. 그것으로 조언을 쓰면 관찰을 밝힌 형식만 남고 §7.6.2 가 요구한
실질은 없다.
"""

MAX_OBSERVATIONS = 5
"""한 회차에 실을 관찰의 수. 많으면 칼럼이 통계 보고서가 된다."""


@dataclass(frozen=True)
class Observation:
    """관찰된 현상 하나. **이것이 권고의 근거다** (§7.6.2).

    §2 는 QnA 이력을 "관찰된 현상"으로 정의했다 — 무엇이 자주 묻히는지는 데이터다.
    관찰을 근거로 쓰는 것은 원칙의 예외가 아니라 **원칙 그대로**이며, 관찰을 생략하고
    조언만 남기면 그 순간 의견이 된다.
    """

    id: str
    question: str
    count: int
    resolved: int
    window_days: int
    cited: bool = True
    """본문이 이 관찰을 밝혔다고 신고했는가 (§7.6.2).

    **검수는 전부를 보고 나가는 글은 쓴 것만 싣는다.** 전부를 봐야 지어낸 관찰을
    가려낼 수 있고, 쓰지 않은 관찰이 근거로 붙으면 읽는 사람은 그 조언이 그것에
    기댄 줄로 읽는다. 옛 초안에는 이 표시가 없으므로 **기본은 실린 것**으로 본다 —
    그때는 전부가 실렸기 때문이다.
    """

    @property
    def text(self) -> str:
        """검수자가 대조할 문장. **숫자가 여기 있다** — 본문의 수치를 P1 이 이것과 견준다."""
        return (
            f'지난 {self.window_days}일 동안 "{self.question}" 형태의 문의가 '
            f"{self.count}건 있었다 (그중 해결 {self.resolved}건)."
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "count": self.count,
            "resolved": self.resolved,
            "window_days": self.window_days,
        }

    @classmethod
    def of(cls, payload: dict) -> Observation:
        return cls(
            id=str(payload.get("id") or ""),
            question=str(payload.get("question") or ""),
            count=int(payload.get("count") or 0),
            resolved=int(payload.get("resolved") or 0),
            window_days=int(payload.get("window_days") or 0),
            cited=bool(payload.get("cited", True)),
        )


def observations(
    groups: list[RepeatQuestion], *, window_days: int, limit: int = MAX_OBSERVATIONS
) -> tuple[Observation, ...]:
    """묶음에서 관찰을 만든다. **많이 물은 것부터.**

    **번호를 여기서 붙인다.** 초안에 박히는 것이 이 목록이고, 본문은 번호가 아니라
    문장으로 관찰을 밝히지만(§7.6.2) 검수는 이 목록과 대조해야 한다 — 그때 무엇을
    관찰했는지가 남아 있지 않으면 발행 뒤에는 다시 셀 수 없다.
    """
    picked = sorted(
        (g for g in groups if g.count >= OBSERVATION_MIN),
        key=lambda g: (-g.count, g.representative),
    )[:limit]
    return tuple(
        Observation(
            id=f"obs-{index}",
            question=" ".join(group.representative.split()),
            count=group.count,
            resolved=group.resolved_count,
            window_days=window_days,
        )
        for index, group in enumerate(picked, start=1)
    )
