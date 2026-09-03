"""WBS-4.4.2 — 파이프라인 1~3단계 (FR-16·17·18·19, D6·D17).

**답을 만들지 못할 자유가 없으면 4단계 검수는 형식이 된다.** 그래서 여기서 지키는
것의 절반은 "만들지 않는 것"이다.

    1. 단계를 건너뛰지 않고 **기록이 남는다** (FR-16)
    2. **질문 언어를 판정**하고 그 언어로 쓴다 — 근거는 원문 인용 (FR-17)
    3. **근거 0건이면 초안이 생성되지 않는다** (FR-18)
    4. 기본값은 **부분 답변 + 경계 명시** — 억지 완성도 과잉 침묵도 아니다 (FR-19)
"""

from __future__ import annotations

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import Search
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.pipeline.answer import (
    EN,
    KO,
    Confidence,
    AnswerPipeline,
    Halt,
    Stage,
    build_prompt,
    detect_language,
    find_similar_questions,
    parse_draft,
)

from conftest import FakeHarness

ANSWER = """
{"answerable": true,
 "statements": [
   {"text": "결재 한도는 부서 등급으로 결정됩니다.", "confidence": "확인됨", "grounding": ["%s"]},
   {"text": "등급이 바뀌면 한도도 함께 바뀝니다.", "confidence": "추론", "grounding": ["%s"]}
 ],
 "unanswered": ["현재 귀하 부서의 실제 한도 값은 조회 대상이 아닙니다"]}
"""

OLD_FORMAT = """
{"answerable": true,
 "body": "결재 한도는 부서 등급으로 결정됩니다.",
 "grounding": ["%s"]}
"""


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _item(repo, title="결재 한도 결정 규칙", body="부서 등급으로 정해진다.", **over):  # noqa: ANN001, ANN003
    base = dict(
        title=title,
        body=body,
        provenance=[Provenance(commit="a" * 40)],
        invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=90),
    )
    base.update(over)
    item = KnowledgeItem(**base)  # type: ignore[arg-type]
    repo.save(item)
    return item


def _pipeline(tmp_path, harness=None):  # noqa: ANN001, ANN202
    repo, conn = _repo(tmp_path), _conn(tmp_path)
    return AnswerPipeline(
        search=Search(repo=repo, conn=conn), conn=conn, harness=harness
    ), repo, conn


class TestLanguage:
    """FR-17 — 1단계에서 판정하고 3단계가 그 언어로 쓴다."""

    def test_한국어를_판정한다(self) -> None:
        assert detect_language("결재 한도가 왜 이런가요?") == KO

    def test_영어를_판정한다(self) -> None:
        assert detect_language("How is the approval limit decided?") == EN

    def test_영문_식별자가_섞여도_한국어다(self) -> None:
        # 사내 질문의 흔한 형태다. 식별자 개수로 다수결하면 코드 용어가 많은 질문이
        # 영어로 뒤집힌다.
        assert detect_language("approval_limit 이 왜 300만인가요") == KO

    def test_판정이_결정적이다(self) -> None:
        # 모델에 묻지 않는다 — 같은 질문이 늘 같은 언어로 답해져야 한다.
        q = "결재 한도"
        assert detect_language(q) == detect_language(q) == KO

    def test_프롬프트가_그_언어로_쓰라고_한다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        conn = _conn(tmp_path)
        hits = Search(repo=repo, conn=conn).find("결재")

        assert "한국어 로 쓴다" in build_prompt("결재", hits, KO)
        assert "영어 로 쓴다" in build_prompt("approval", hits, EN)

    def test_프롬프트가_근거는_원문_인용하라고_한다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        hits = Search(repo=repo, conn=conn).find("결재")
        assert "원문 그대로" in build_prompt("결재", hits, KO)


class TestSimilarQuestions:
    def test_기존_질문에서_겹치는_것을_찾는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        manual_entry.register(conn, question="결재 한도가 왜 이런가요?", answer="등급입니다")
        assert find_similar_questions(conn, "결재 한도 문의") 

    def test_안_겹치면_비어_있다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        manual_entry.register(conn, question="휴가 신청 방법", answer="설정에서")
        assert find_similar_questions(conn, "네트워크 방화벽") == ()


class TestNoGroundingNoAnswer:
    """FR-18 — 근거가 없으면 답을 만들지 않는다."""

    def test_근거가_없으면_초안이_없다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness(ANSWER % ("k-x", "k-x")))
        _item(repo)

        outcome = pipeline.run("네트워크 방화벽 정책이 궁금합니다")
        assert outcome.halted is Halt.NO_GROUNDING
        assert not outcome.produced
        assert outcome.to_human

    def test_생성_단계까지_가지_않는다(self, tmp_path) -> None:
        # 지어낼 기회 자체를 주지 않는다.
        harness = FakeHarness(ANSWER % ("k-x", "k-x"))
        pipeline, repo, _ = _pipeline(tmp_path, harness)
        _item(repo)

        pipeline.run("전혀 다른 주제")
        assert harness.prompts == []

    def test_멈춤이_기록에_남는다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness())
        _item(repo)

        outcome = pipeline.run("전혀 다른 주제")
        assert [r.stage for r in outcome.stages] == [Stage.ANALYZE, Stage.RETRIEVE]
        assert outcome.stages[-1].halted


class TestGeneration:
    def test_단계를_건너뛰지_않는다(self, tmp_path) -> None:
        # FR-16 — 두 산출물이 같은 단계 기록을 남긴다.
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % (item.id, item.id)))

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert [r.stage for r in outcome.stages] == [
            Stage.ANALYZE, Stage.RETRIEVE, Stage.GENERATE, Stage.RENDER
        ]

    def test_초안과_근거가_나온다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % (item.id, item.id)))

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert outcome.produced
        assert outcome.draft.grounding == (item.id,)

    def test_생성기가_없으면_멈춘다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, None)
        _item(repo)
        assert pipeline.run("결재 한도").halted is Halt.GENERATION_FAILED

    def test_응답이_깨지면_멈춘다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness("JSON 이 아니다"))
        _item(repo)
        assert pipeline.run("결재 한도").halted is Halt.GENERATION_FAILED


class TestBoundaryNotFabrication:
    """FR-19, §5.4.2 — 억지 완성과 과잉 침묵 사이."""

    def test_모르는_경계가_남는다(self, tmp_path) -> None:
        # "한도가 결정되는 규칙은 답하고, 현재 값은 조회 대상이 아님을 밝힌다"
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % (item.id, item.id)))

        outcome = pipeline.run("제 결재 한도가 왜 300만원인가요")
        assert outcome.draft.unanswered

    def test_프롬프트가_부분_답변을_기본값으로_말한다(self, tmp_path) -> None:
        """라이브에서 잡은 것 — 프롬프트가 침묵 쪽으로 밀고 있었다.

        "제 결재 한도가 왜 300만원인가요?"에 모델이 `answerable=false` 를 냈는데,
        §5.4.2 는 바로 그 질문을 **부분 답변의 예시**로 든다 — 규칙은 답하고 값은
        모른다고 밝히는 것. 과잉 침묵도 실패다.
        """
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        hits = Search(repo=repo, conn=conn).find("결재")
        prompt = build_prompt("결재 한도", hits, KO)

        assert "기본값은 부분 답변이다" in prompt
        assert "값을 물었다는 이유로" in prompt  # answerable 을 false 로 만들지 않는다
        assert "300만원" in prompt  # §5.4.2 의 예시를 그대로 준다

    def test_핵심을_모르면_답을_만들지_않는다(self, tmp_path) -> None:
        # §5.4.2 규칙 3 — 경계만 밝힌 답변이 답이 되지 않을 때다.
        repo = _repo(tmp_path)
        _item(repo)
        pipeline, _, _ = _pipeline(
            tmp_path, FakeHarness('{"answerable": false, "body": ""}')
        )

        outcome = pipeline.run("결재 한도")
        assert outcome.halted is Halt.CORE_UNKNOWN
        assert not outcome.produced

    def test_근거를_안_가리키면_초안으로_받지_않는다(self, tmp_path) -> None:
        # 답이 근거에서 나온 것인지 알 수 없다 (D3). 검수 이전에 형식으로 걸러 낸다.
        text = '{"answerable": true, "statements": [{"text": "답", "confidence": "확인됨"}]}'
        assert parse_draft(text, {"k-1"}) is None

    def test_지어낸_근거_id_는_버린다(self, tmp_path) -> None:
        # 없는 것을 가리키는 근거는 Lint 의 끊어진 링크가 되고, 그때는 이미 답이 나간 뒤다.
        text = (
            '{"answerable": true, "statements": '
            '[{"text": "답", "confidence": "확인됨", "grounding": ["k-지어냄"]}]}'
        )
        assert parse_draft(text, {"k-1"}) is None

    def test_실재하는_것만_남긴다(self) -> None:
        text = (
            '{"answerable": true, "statements": '
            '[{"text": "답", "confidence": "추론", "grounding": ["k-1", "k-없음"]}]}'
        )
        assert parse_draft(text, {"k-1"}).grounding == ("k-1",)

    def test_본문이_비면_초안이_아니다(self) -> None:
        text = '{"answerable": true, "statements": [{"text": "  ", "confidence": "확인됨"}]}'
        assert parse_draft(text, {"k-1"}) is None


class TestStaleIsFlaggedToTheModel:
    def test_낡은_항목에_표시가_붙는다(self, tmp_path) -> None:
        # P4(낡은 지식을 현재형으로 단정)를 검수 전에 줄이는 장치다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, stale=True)
        hits = Search(repo=repo, conn=conn).find("결재")

        assert "낡은 항목" in build_prompt("결재", hits, KO)


class TestOutcome:
    def test_요약이_지나온_단계를_말한다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % (item.id, item.id)))

        # 정제(FR-61)가 생성 다음이다 — **검수 앞**이라야 검수가 본 글이 나가는 글이다.
        assert pipeline.run("결재 한도").summary() == "분석 → 조회 → 생성 → 정제"

    def test_멈춘_이유가_요약에_보인다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness())
        _item(repo)
        assert "근거 없음" in pipeline.run("전혀 다른 주제").summary()


class TestUncertainty:
    """WBS-4.4.3 — 초안이 자기 불확실성을 표시한다 (FR-23, ADR-007 결정 3, §5.6.5).

    사람이 답할 질문은 하나다 — **어디를 봐야 하는가.** 그 답이 되려면 표시가
    정직해야 하고, 정직함의 일부는 **모델이 제 확신을 부풀리지 못하게 하는 것**이다.
    """

    #: 근거 원문. `확인됨` 은 이 안에 낱말이 있어야 성립한다.
    SOURCE = {
        "k-1": "결재 한도는 부서 등급으로 정해진다. 규칙은 등급이다.",
        "k-2": "등급이 바뀌면 한도도 바뀐다. 따라서 이럴 것이다.",
    }

    def _draft(self, text: str, allowed=None, stale=None, source=None):  # noqa: ANN001, ANN202
        return parse_draft(
            text,
            allowed or {"k-1", "k-2"},
            stale or set(),
            self.SOURCE if source is None else source,
        )

    def test_세_단계뿐이다(self) -> None:
        # 잘게 나눌수록 다시 전부 읽게 되어 목적을 잃는다.
        assert {str(c) for c in Confidence} == {"확인됨", "추론", "근거 얇음"}

    def test_진술_단위로_붙는다(self) -> None:
        # 문단 단위면 어디가 약한지 알 수 없고, 단어 단위면 화면이 시끄럽다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다.", "confidence": "확인됨", "grounding": ["k-1"]},'
            '{"text": "따라서 이럴 것이다.", "confidence": "추론", "grounding": ["k-1","k-2"]}]}'
        )
        assert [s.confidence for s in draft.statements] == [
            Confidence.CONFIRMED, Confidence.INFERRED
        ]

    def test_약한_지점만_따로_나온다(self) -> None:
        # FR-23 검증 — 약한 근거 지점이 표시된다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "확인됨", "grounding": ["k-1"]},'
            '{"text": "아마 그럴 것이다", "confidence": "근거 얇음", "grounding": ["k-2"]}]}'
        )
        assert [s.text for s in draft.weak_points] == ["아마 그럴 것이다"]
        assert not draft.all_confirmed

    def test_본문은_진술을_이어_붙인_것이다(self) -> None:
        # 강도 표시는 운영자 화면에만 붙고 이용자에게는 가지 않는다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "확인됨", "grounding": ["k-1"]},'
            '{"text": "나", "confidence": "추론", "grounding": ["k-1"]}]}'
        )
        assert draft.body == "규칙은 등급이다\n\n나"
        assert "확인됨" not in draft.body

    def test_근거_없는_진술은_확인됨일_수_없다(self) -> None:
        # "근거 원문에 그대로 있다"는 주장인데 가리키는 원문이 없다.
        # 형식으로 확인 가능한 거짓말이므로 여기서 막는다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "확인됨"},'
            '{"text": "규칙은 등급이다", "confidence": "확인됨", "grounding": ["k-1"]}]}'
        )
        assert draft.statements[0].confidence is Confidence.THIN
        assert draft.statements[1].confidence is Confidence.CONFIRMED

    def test_낡은_근거에_기댄_진술은_확인됨일_수_없다(self) -> None:
        # 인용은 정확해도 그 원문이 지금도 맞는지는 모른다 — 낡은 지식을 현재형으로
        # 단정하는 것이 P4 반려 사유다. "넘어가도 되는 칸"에 놓이면 안 된다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "확인됨", "grounding": ["k-1"]}]}',
            stale={"k-1"},
        )
        assert draft.statements[0].confidence is Confidence.INFERRED

    def test_스스로_낮게_매긴_것은_올리지_않는다(self) -> None:
        # 자기 불확실성을 표시하라고 시켜 놓고 그 표시를 우리가 뒤집으면 의미가 없다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "가", "confidence": "근거 얇음", "grounding": ["k-1", "k-2"]}]}'
        )
        assert draft.statements[0].confidence is Confidence.THIN

    def test_모르는_값은_안전한_쪽으로_떨어진다(self) -> None:
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "매우 확실", "grounding": ["k-1"]}]}'
        )
        assert draft.statements[0].confidence is Confidence.THIN

    def test_강도가_없으면_통째로_보게_둔다(self) -> None:
        # 어디가 강하고 약한지 알 수 없는데 강하다고 매기면 표시가 거짓이 된다.
        draft = parse_draft(OLD_FORMAT % "k-1", {"k-1"})
        assert len(draft.statements) == 1
        assert draft.statements[0].confidence is Confidence.THIN

    def test_원문에_없는_말로_이뤄지면_확인됨이_아니다(self) -> None:
        """라이브에서 잡은 것 — **모델이 제 확신을 부풀린다.**

        모든 진술을 `확인됨` 으로 매겨 약한 지점이 0 이 됐는데, 그러면 이 표시가
        아무것도 가리키지 못해 없는 것과 같아진다 (§5.6.5). 다행히 이 등급의
        정의("근거 원문에 그대로 있다")는 셀 수 있다.
        """
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "인사이동 절차는 별도 신청서를 요구하며 승인 단계가 셋이다",'
            ' "confidence": "확인됨", "grounding": ["k-1"]}]}'
        )
        assert draft.statements[0].confidence is Confidence.INFERRED

    def test_원문을_안_주면_확인할_수_없다(self) -> None:
        # 확인할 수 없는 것을 확인됐다고 두지 않는다.
        draft = self._draft(
            '{"answerable": true, "statements": ['
            '{"text": "규칙은 등급이다", "confidence": "확인됨", "grounding": ["k-1"]}]}',
            source={},
        )
        assert draft.statements[0].confidence is not Confidence.CONFIRMED

    def test_프롬프트가_확신_부풀리기를_막는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo)
        prompt = build_prompt("결재", Search(repo=repo, conn=conn).find("결재"), KO)

        assert "자기 확신을 부풀리지 않는다" in prompt
        for label in ("확인됨", "추론", "근거 얇음"):
            assert label in prompt

    def test_생성_기록에_약한_지점_수가_남는다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % (item.id, item.id)))

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        # **생성 기록을 콕 집어 본다** — 뒤에 정제가 붙어 마지막이 아니다.
        generate = next(r for r in outcome.stages if r.stage is Stage.GENERATE)
        assert "약한 지점 1" in generate.detail


RENDERED = '{"answer": "부서 등급에 따라 정해집니다. 등급이 바뀌면 한도도 함께 바뀌는 것으로 보입니다."}'


class TestRender:
    """4단계 정제 — 나갈 글로 다시 쓴다 (FR-61).

    **정제하지 않으면 내부 구현이 그대로 나간다.** 초안은 지식의 말로 쓰이므로 파일
    경로와 코드 식별자가 들어가는데 `Draft.body` 가 곧 게재 본문이다.

    **검수 앞이라야 한다.** 뒤에 두면 검수가 본 글과 나가는 글이 달라져 FR-20 이
    무너진다 — 검수는 `body` 를 보고 그 값이 이 단계에서 정해진다.
    """

    def test_정제된_글이_게재_본문이_된다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(
            tmp_path, FakeHarness(ANSWER % (item.id, item.id), RENDERED)
        )

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert outcome.draft.body.startswith("부서 등급에 따라 정해집니다.")
        # 원본은 남는다 — 강도 판정의 기준이고 운영자가 대조할 것이다.
        assert outcome.draft.statements[0].text == "결재 한도는 부서 등급으로 결정됩니다."

    def test_강도는_원본에_남는다(self, tmp_path) -> None:
        # **나가는 글에는 강도를 붙이지 않는다** — 원래 규약이다(운영자 화면 전용).
        # 정제는 판정을 다시 하지 않으므로 약한 지점은 원본 진술로 센다.
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(
            tmp_path, FakeHarness(ANSWER % (item.id, item.id), RENDERED)
        )

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert len(outcome.draft.weak_points) == 1
        assert outcome.draft.rendered

    def test_정제가_비면_원본을_둔다(self, tmp_path) -> None:
        # 다듬지 못했다고 답이 없어지면 안 된다 — 투박한 답이 없는 답보다 낫다.
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(
            tmp_path, FakeHarness(ANSWER % (item.id, item.id), '{"answer": "  "}')
        )

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert outcome.draft.rendered == ""
        assert "결재 한도는 부서 등급으로 결정됩니다." in outcome.draft.body
        render = next(r for r in outcome.stages if r.stage is Stage.RENDER)
        assert "원본을 둔다" in render.detail

    def test_정제가_터져도_답이_남는다(self, tmp_path) -> None:
        # 다듬기가 답을 무너뜨리지 않는다 — 투박한 답이 없는 답보다 낫다.
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(
            tmp_path, FakeHarness(ANSWER % (item.id, item.id), "JSON 이 아니다")
        )

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert outcome.draft is not None
        assert outcome.draft.rendered == ""

    def test_생성기가_없으면_원본을_그대로_둔다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        _item(repo)
        pipeline, _, _ = _pipeline(tmp_path)

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert outcome.draft is None  # 생성부터 못 한다

    def test_멈춘_건은_정제하지_않는다(self, tmp_path) -> None:
        # 초안이 없는데 다듬을 것이 없다.
        repo = _repo(tmp_path)
        _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness('{"answerable": false}'))

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert Stage.RENDER not in [r.stage for r in outcome.stages]
