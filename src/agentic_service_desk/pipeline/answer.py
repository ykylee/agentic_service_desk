"""답변 산출 — 파이프라인 1~3단계 (FR-16·17·18·19, D6·D17).

    ① 분석 → ② 지식베이스 조회 → ③ 생성 → (④ 검수 → ⑤ 게재)

**단계를 건너뛰지 않는다.** 각 단계가 무엇을 했는지 기록을 남기며, 답변과 콘텐츠가
같은 형태의 기록을 남기는 것이 FR-16 의 검증 기준이다.

## 실패할 자유

각 단계의 실패는 사람의 대기열로 수렴한다 — 답변은 티켓으로 간다. **답을 만들지
못할 자유가 없으면 4단계 검수는 형식이 된다.** 그래서 이 모듈은 "답을 못 만들었다"를
오류가 아니라 정상 결과로 돌려준다.

## 억지 완성과 과잉 침묵 사이

둘 다 실패다 (§5.4.1).

| | **억지 완성** | **과잉 침묵** |
|---|---|---|
| 왜 나쁜가 | **근거가 붙어 있어 더 믿게 된다** | 커버리지가 무너지고 사람이 시스템을 우회한다 |
| 겉보기 | 유능해 보인다 | 안전해 보인다 |

**억지 완성이 더 위험하다** — 이 시스템은 모든 답변에 근거를 붙이므로(D3), 근거가
붙은 채 섞여 들어간 추정은 검증된 사실처럼 읽힌다.

그래서 기본값은 **부분 답변 + 경계 명시**다 (§5.4.2). "제 승인 한도가 왜 이렇게
나오나요"라면 **한도가 결정되는 규칙은 답하고, 현재 값은 조회 대상이 아님을 밝힌다** —
규칙까지 침묵하지 않고 값을 지어내지도 않는다. 모르는 부분이 **질문의 핵심**일 때만
답변을 만들지 않는다.
"""

from __future__ import annotations

import enum
import re
import sqlite3
from dataclasses import dataclass, field

from agentic_service_desk.ingest.agent import AgentOutputError, Harness, extract_json
from agentic_service_desk.knowledge.search import Hit, Search, _matches, rerank, tokenize


class Stage(enum.StrEnum):
    """5단계 (§5.1). **건너뛰지 않는다.**"""

    ANALYZE = "분석"
    RETRIEVE = "조회"
    GENERATE = "생성"
    REVIEW = "검수"
    PUBLISH = "게재"


class Halt(enum.StrEnum):
    """왜 멈췄는가. **멈춤은 오류가 아니다** — 사람의 대기열로 가는 정상 결과다."""

    NO_GROUNDING = "근거 없음"
    """조회가 아무것도 못 찾았다 (FR-18). 지어내는 대신 티켓으로 보낸다."""

    CORE_UNKNOWN = "핵심을 모른다"
    """근거는 있으나 **질문의 핵심**이 그 밖이다 (§5.4.2 규칙 3).

    부분 답변으로 넘길 수 없는 경우다 — 규칙을 물은 게 아니라 현재 값을 물었는데
    값이 조회 대상이 아니라면, 경계만 밝힌 답변은 답이 아니다.
    """

    GENERATION_FAILED = "생성 실패"


@dataclass(frozen=True)
class StageRecord:
    """한 단계가 무엇을 했는가. **답변과 콘텐츠가 같은 형태를 남긴다** (FR-16)."""

    stage: Stage
    detail: str
    halted: bool = False


@dataclass(frozen=True)
class Analysis:
    """1단계 산출 — 대상 정의."""

    language: str
    """질문 언어 (FR-17). **3단계가 이 언어로 쓴다.**"""

    similar_questions: tuple[str, ...] = ()
    """기존 QnA 와의 중복·유사 후보. 반복 질문 탐지와 FAQ 승격의 재료다."""


CONFIRMED_COVERAGE = 0.6
"""`확인됨` 이 성립하려면 진술의 낱말이 근거 원문에 이만큼은 있어야 한다.

**모델은 제 확신을 부풀린다.** 라이브에서 모든 진술을 `확인됨` 으로 매겨 약한 지점이
0 이 됐는데, 그러면 이 표시가 아무것도 가리키지 못해 **없는 것과 같아진다** (§5.6.5).

다행히 `확인됨` 의 정의("근거 원문에 그대로 있다")는 **셀 수 있다.** 문장은 바꿔 쓴
것이므로 글자 그대로 같을 수는 없지만, 원문에 없던 낱말이 절반을 넘으면 그것은
"그대로 있는" 문장이 아니다.

이 값은 **실데이터로 다시 맞춰야 한다** — 표본 둘로 고른 값이다.
"""


class Confidence(enum.StrEnum):
    """근거 강도 — **세 단계뿐이다** (ADR-007 결정 3, §5.6.5).

    사람이 답할 질문은 하나다: **어디를 봐야 하는가.** 잘게 나눌수록 다시 전부 읽게
    되어 목적을 잃으므로 셋에서 멈춘다.
    """

    CONFIRMED = "확인됨"
    """근거 원문에 그대로 있다. **넘어가도 된다.**"""

    INFERRED = "추론"
    """여러 근거를 엮어 도출했다. **여기를 본다.**"""

    THIN = "근거 얇음"
    """단일·간접 근거뿐이다. **여기를 먼저 본다.**"""

    @property
    def needs_review(self) -> bool:
        return self is not Confidence.CONFIRMED


@dataclass(frozen=True)
class Statement:
    """진술 하나와 그 근거 강도.

    **진술 단위로 붙인다** (ADR-007 결정 3). 문단 단위면 어디가 약한지 알 수 없고,
    단어 단위면 화면이 시끄러워 결국 안 보게 된다.
    """

    text: str
    confidence: Confidence
    grounding: tuple[str, ...] = ()
    """이 진술이 기댄 항목. **`확인됨` 은 하나라도 있어야 한다.**"""


@dataclass(frozen=True)
class Draft:
    """3단계 산출 — 초안과 근거."""

    statements: tuple[Statement, ...]
    """진술과 그 강도. 본문은 이것을 이어 붙인 것이다."""

    grounding: tuple[str, ...]
    """근거로 쓴 지식 항목 id. **비어 있으면 초안이 아니다** (D3)."""

    unanswered: tuple[str, ...] = ()
    """**모른다고 밝힌 부분** (FR-19). 비어 있는 것이 늘 좋은 것은 아니다 —
    질문이 여러 갈래인데 전부 답했다면 억지 완성을 의심해야 한다."""

    @property
    def body(self) -> str:
        """게재될 본문. 강도 표시는 **운영자 화면에만** 붙고 이용자에게는 가지 않는다."""
        return "\n\n".join(s.text for s in self.statements)

    @property
    def weak_points(self) -> tuple[Statement, ...]:
        """사람이 볼 곳 (FR-23 검증 — 약한 근거 지점이 표시된다).

        **매번 다른 자리가 표시되므로 화면을 눈으로 훑어 넘기기 어렵다** —
        습관화를 깨는 것이 이 표시의 부수 효과다 (§5.6.5).
        """
        return tuple(s for s in self.statements if s.confidence.needs_review)

    @property
    def all_confirmed(self) -> bool:
        """전부 확인됨인가. **참이라고 안심할 일은 아니다** — 초안이 자기 확신을
        과장했을 수 있고, 그것을 보는 것이 §5.6.6 의 관측 지표다."""
        return not self.weak_points


@dataclass
class Outcome:
    """한 번의 산출."""

    stages: list[StageRecord] = field(default_factory=list)
    analysis: Analysis | None = None
    hits: list[Hit] = field(default_factory=list)
    draft: Draft | None = None
    halted: Halt | None = None

    @property
    def produced(self) -> bool:
        return self.draft is not None

    @property
    def to_human(self) -> bool:
        """사람의 대기열로 가야 하는가. **멈춤은 정상 결과다.**"""
        return self.halted is not None

    def summary(self) -> str:
        done = " → ".join(str(r.stage) for r in self.stages)
        return f"{done}" + (f" (멈춤: {self.halted})" if self.halted else "")


# --- 1단계 분석 -------------------------------------------------------------

_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")

KO, EN = "ko", "en"


def detect_language(text: str) -> str:
    """질문 언어를 판정한다 (FR-17, §2.5.2).

    **글자만 센다.** 사내 시스템 질문은 한국어에 영문 식별자가 섞이는 형태가 흔한데
    (`approval_limit` 이 왜 300만인가요), 그때도 질문의 언어는 한국어다. 한글이
    하나라도 있으면 한국어로 보는 이유가 그것이다 — 식별자 개수로 다수결을 하면
    코드 용어가 많은 질문이 영어로 뒤집힌다.

    모델에 묻지 않는다. 판정이 결정적이어야 같은 질문이 같은 언어로 답해지고,
    이 정도 구분에 LLM 호출을 붙이면 지연만 는다.
    """
    if _HANGUL.search(text):
        return KO
    return EN if _LATIN.search(text) else KO


def find_similar_questions(
    conn: sqlite3.Connection, question: str, *, limit: int = 3
) -> tuple[str, ...]:
    """기존 QnA 중 겹치는 것 (§5.1 1단계).

    **기계적으로 센다.** 중복 판정은 결정적이어야 하고, 여기서 LLM 을 부르면 모든
    질문에 호출이 하나 더 붙는다. 정밀도가 낮아도 이 단계의 쓰임(반복 질문 탐지)에는
    충분하다.
    """
    tokens = set(tokenize(question))
    if not tokens:
        return ()
    scored: list[tuple[int, str]] = []
    for row in conn.execute("SELECT body FROM raw_question"):
        overlap = len(tokens & set(tokenize(row["body"])))
        if overlap:
            scored.append((overlap, row["body"]))
    for row in conn.execute("SELECT question FROM manual_entry"):
        overlap = len(tokens & set(tokenize(row["question"])))
        if overlap:
            scored.append((overlap, row["question"]))
    scored.sort(key=lambda x: -x[0])
    return tuple(text for _, text in scored[:limit])


# --- 3단계 생성 -------------------------------------------------------------

_RULES = """당신은 사내 시스템의 질문에 답한다. **근거로 준 지식 항목만 쓴다.**

**기본값은 부분 답변이다.** 모르는 것이 있다고 침묵하지 말고, 아는 부분을 답한 뒤
모르는 부분의 경계를 밝힌다. 답을 아예 안 만드는 것은 **아는 부분이 하나도 없을
때만**이다.

예: "제 결재 한도가 왜 300만원인가요?"
→ 한도가 **결정되는 규칙**은 근거가 있으니 답한다. **지금 그 사람의 값이 얼마인지**는
   조회 대상이 아니므로 모른다고 밝힌다. 규칙까지 침묵하지 않고, 값을 지어내지도 않는다.
   이때 `answerable` 은 **true** 다.

규칙:
1. **아는 만큼 답하고 모르는 부분은 모른다고 밝힌다.** 추정·일반론·유사 사례로 채우지
   않는다. 근거가 붙어 있으면 이용자는 그것을 검증된 사실로 읽으므로, 지어낸 문장은
   근거가 없는 문장보다 해롭다.
2. **현재 값은 답하지 않는다.** "지금 이 부서의 한도는 얼마인가" 같은 것은 규칙이
   아니라 상태이고 조회 대상이 아니다. 규칙은 답하고 값은 모른다고 밝힌다.
   **값을 물었다는 이유로 `answerable` 을 false 로 만들지 않는다.**
3. `answerable` 을 false 로 두는 것은 **근거로 답할 수 있는 부분이 전혀 없을 때**다.
   질문이 근거와 다른 주제일 때가 그렇다. 조금이라도 답할 것이 있으면 true 다.
4. **{language} 로 쓴다.** 다만 근거를 인용할 때는 **원문 그대로** 옮긴다.
5. 근거로 실제로 쓴 항목의 id 만 `grounding` 에 넣는다. 목록에 없는 id 를 만들지 않는다.
6. **답을 진술 단위로 쪼개고 각각에 근거 강도를 붙인다.** 사람이 답할 질문은 하나다 —
   어디를 봐야 하는가. 셋 중 하나로 고른다.

   - `확인됨` — 근거 원문에 **그대로 있다.** 읽는 사람이 넘어가도 되는 문장이다.
   - `추론` — 여러 근거를 **엮어 도출**했다. 근거는 있지만 그 문장 그대로는 없다.
   - `근거 얇음` — 단일·간접 근거뿐이다.

   **자기 확신을 부풀리지 않는다.** 전부 `확인됨` 으로 매기면 이 표시가 아무것도
   가리키지 못해 없는 것과 같아진다. 근거 원문을 다시 보고 정직하게 고른다.
   진술마다 그 문장이 실제로 기댄 항목 id 를 `grounding` 에 적는다.

출력은 **JSON 하나만** 낸다. 설명이나 사고 과정을 붙이지 않는다.

{
  "answerable": true,
  "statements": [
    {"text": "한 문장 또는 한 진술", "confidence": "확인됨", "grounding": ["k-..."]}
  ],
  "unanswered": ["모른다고 밝힌 부분"]
}"""


def build_prompt(question: str, hits: list[Hit], language: str) -> str:
    blocks = []
    for h in hits:
        mark = " [낡은 항목 — 현재형으로 단정하지 않는다]" if h.is_stale else ""
        blocks.append(f"### {h.item.id} — {h.item.title}{mark}\n{h.item.body}")
    return "\n".join(
        [
            _RULES.replace("{language}", "한국어" if language == KO else "영어"),
            "",
            f"질문:\n{question}",
            "",
            "근거로 쓸 수 있는 지식 항목:",
            *blocks,
        ]
    )


def parse_draft(
    text: str,
    allowed_ids: set[str],
    stale_ids: set[str] | None = None,
    source_text: dict[str, str] | None = None,
) -> Draft | None:
    """응답을 초안으로 바꾼다. `None` 이면 **답을 만들지 않겠다는 결정**이다.

    지어낸 근거 id 는 버린다 — 없는 것을 가리키는 근거는 Lint 의 끊어진 링크가 되고,
    그때는 이미 답이 나간 뒤다.

    `source_text` 가 없으면 **`확인됨` 을 확인할 수 없다.** 확인할 수 없는 것을
    확인됐다고 두지 않으므로 모든 진술이 `추론` 이하로 내려간다 — 파이프라인은 늘
    원문을 함께 넘긴다.
    """
    payload = extract_json(text)
    if not payload.get("answerable", True):
        return None

    statements = _statements(
        payload, allowed_ids, stale_ids or set(), source_text or {}
    )
    if not statements:
        return None

    grounding = tuple(dict.fromkeys(g for s in statements for g in s.grounding))
    if not grounding:
        # 근거를 하나도 안 가리켰다. 답이 근거에서 나온 것인지 알 수 없으므로
        # 초안으로 받지 않는다 (D3) — 검수 이전에 형식으로 걸러 낸다.
        return None
    return Draft(
        statements=statements,
        grounding=grounding,
        unanswered=tuple(
            str(u).strip() for u in (payload.get("unanswered") or []) if str(u).strip()
        ),
    )


def _statements(
    payload: dict,
    allowed_ids: set[str],
    stale_ids: set[str],
    source_text: dict[str, str],
) -> tuple[Statement, ...]:
    raw = payload.get("statements")
    if not isinstance(raw, list) or not raw:
        return _fallback_statement(payload, allowed_ids)

    out: list[Statement] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        grounding = tuple(
            str(g) for g in (item.get("grounding") or []) if str(g) in allowed_ids
        )
        out.append(
            Statement(
                text=text,
                confidence=_confidence_of(
                    item.get("confidence"), text, grounding, stale_ids, source_text
                ),
                grounding=grounding,
            )
        )
    return tuple(out)


def _confidence_of(
    raw: object,
    text: str,
    grounding: tuple[str, ...],
    stale_ids: set[str],
    source_text: dict[str, str],
) -> Confidence:
    """모델이 매긴 강도를 받되 **세 경우에는 내린다.**

    ① **근거를 못 댔으면 `확인됨` 일 수 없다.** "근거 원문에 그대로 있다"는 주장인데
       가리키는 원문이 없다. 형식으로 확인 가능한 거짓말이므로 여기서 막는다.

    ② **낡은 근거에 기댄 진술도 `확인됨` 일 수 없다.** 인용 자체는 정확하더라도
       그 원문이 지금도 맞는지는 모른다 — 낡은 지식을 현재형으로 단정하는 것이
       P4 반려 사유다. 이 표시가 답하는 질문은 "어디를 봐야 하는가" 이므로,
       stale 에 기댄 문장이 **넘어가도 되는 칸에 놓이면 안 된다.**

    ③ **원문에 없는 말로 이뤄진 진술도 `확인됨` 일 수 없다.** 모델은 제 확신을
       부풀리므로 자기 신고를 그대로 믿지 않는다 — 다행히 이 등급의 정의는 셀 수
       있다 (`CONFIRMED_COVERAGE`).

    올리지는 않는다. 모델이 스스로 낮게 매긴 것은 그대로 둔다 — 자기 불확실성을
    표시하라고 시켰는데 그 표시를 우리가 뒤집으면 시킨 의미가 없다.
    """
    try:
        confidence = Confidence(str(raw).strip())
    except ValueError:
        # 모르는 값이면 안전한 쪽으로 — 읽는 사람이 보게 둔다.
        return Confidence.THIN

    if confidence is not Confidence.CONFIRMED:
        return confidence
    if not grounding:
        return Confidence.THIN
    if any(g in stale_ids for g in grounding):
        return Confidence.INFERRED
    if _coverage(text, grounding, source_text) < CONFIRMED_COVERAGE:
        return Confidence.INFERRED
    return Confidence.CONFIRMED


def _coverage(text: str, grounding: tuple[str, ...], source_text: dict[str, str]) -> float:
    """진술의 낱말 중 근거 원문에 있는 비율.

    `_matches` 를 그대로 쓴다 — 검색이 조사 변형을 넘는 방식과 같아야 "원문에 있다"의
    뜻이 두 곳에서 갈리지 않는다.
    """
    tokens = set(tokenize(text))
    if not tokens:
        return 1.0
    source = set()
    for g in grounding:
        source |= set(tokenize(source_text.get(g, "")))
    if not source:
        return 0.0
    return sum(1 for t in tokens if _matches(t, source)) / len(tokens)


def _fallback_statement(payload: dict, allowed_ids: set[str]) -> tuple[Statement, ...]:
    """`statements` 없이 `body` 만 온 경우.

    통째로 **`근거 얇음`** 한 진술로 받는다. 어디가 강하고 약한지 알 수 없는데
    강하다고 매기면 표시가 거짓이 되므로, 모를 때는 **보게 두는 쪽**으로 틀린다.
    """
    body = str(payload.get("body") or "").strip()
    if not body:
        return ()
    grounding = tuple(
        str(g) for g in (payload.get("grounding") or []) if str(g) in allowed_ids
    )
    return (Statement(text=body, confidence=Confidence.THIN, grounding=grounding),)


# --- 실행기 -----------------------------------------------------------------


class AnswerPipeline:
    """질문 하나를 1~3단계에 태운다.

    4·5 단계(검수·게재)는 아직 붙지 않았다 — 여기서 나온 초안은 **아직 나갈 수 없다.**
    """

    def __init__(
        self,
        *,
        search: Search,
        conn: sqlite3.Connection,
        harness: Harness | None = None,
        limit: int = 5,
    ) -> None:
        self._search = search
        self._conn = conn
        self._harness = harness
        self._limit = limit

    def run(self, question: str) -> Outcome:
        outcome = Outcome()

        analysis = self._analyze(question, outcome)
        hits = self._retrieve(question, analysis, outcome)
        if outcome.halted:
            return outcome
        self._generate(question, hits, analysis, outcome)
        return outcome

    def _analyze(self, question: str, outcome: Outcome) -> Analysis:
        analysis = Analysis(
            language=detect_language(question),
            similar_questions=find_similar_questions(self._conn, question),
        )
        outcome.analysis = analysis
        outcome.stages.append(
            StageRecord(
                stage=Stage.ANALYZE,
                detail=f"언어 {analysis.language} · 유사 질문 {len(analysis.similar_questions)}건",
            )
        )
        return analysis

    def _retrieve(
        self, question: str, analysis: Analysis, outcome: Outcome
    ) -> list[Hit]:
        hits = self._search.find(question, limit=self._limit)
        hits = rerank(hits, question, self._harness)
        outcome.hits = hits

        if not hits:
            # **지어내지 않는다** (FR-18). 근거가 없으면 답을 만들지 않고 티켓으로.
            outcome.halted = Halt.NO_GROUNDING
            outcome.stages.append(
                StageRecord(
                    stage=Stage.RETRIEVE,
                    detail="근거 후보 0건 — 답을 만들지 않고 사람에게 넘긴다",
                    halted=True,
                )
            )
            return hits

        outcome.stages.append(
            StageRecord(stage=Stage.RETRIEVE, detail=f"근거 후보 {len(hits)}건")
        )
        return hits

    def _generate(
        self, question: str, hits: list[Hit], analysis: Analysis, outcome: Outcome
    ) -> None:
        if self._harness is None:
            outcome.halted = Halt.GENERATION_FAILED
            outcome.stages.append(
                StageRecord(stage=Stage.GENERATE, detail="생성기가 없다", halted=True)
            )
            return

        prompt = build_prompt(question, hits, analysis.language)
        allowed = {h.item.id for h in hits}
        stale = {h.item.id for h in hits if h.is_stale}
        source_text = {h.item.id: f"{h.item.title} {h.item.body}" for h in hits}
        try:
            draft = parse_draft(
                self._harness.run(prompt).text, allowed, stale, source_text
            )
        except (AgentOutputError, RuntimeError) as exc:
            outcome.halted = Halt.GENERATION_FAILED
            outcome.stages.append(
                StageRecord(stage=Stage.GENERATE, detail=str(exc), halted=True)
            )
            return

        if draft is None:
            # 모델이 "핵심을 모른다"고 판단했거나 근거를 가리키지 못했다.
            # **이것은 실패가 아니라 §5.4.2 규칙 3 의 정상 동작이다.**
            outcome.halted = Halt.CORE_UNKNOWN
            outcome.stages.append(
                StageRecord(
                    stage=Stage.GENERATE,
                    detail="질문의 핵심이 근거 밖이다 — 티켓으로 넘긴다",
                    halted=True,
                )
            )
            return

        outcome.draft = draft
        outcome.stages.append(
            StageRecord(
                stage=Stage.GENERATE,
                detail=(
                    f"진술 {len(draft.statements)}개 (약한 지점 {len(draft.weak_points)}) · "
                    f"근거 {len(draft.grounding)}건 · 경계 {len(draft.unanswered)}건"
                ),
            )
        )
