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
 "body": "결재 한도는 부서 등급으로 결정됩니다.",
 "grounding": ["%s"],
 "unanswered": ["현재 귀하 부서의 실제 한도 값은 조회 대상이 아닙니다"]}
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
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness(ANSWER % "k-x"))
        _item(repo)

        outcome = pipeline.run("네트워크 방화벽 정책이 궁금합니다")
        assert outcome.halted is Halt.NO_GROUNDING
        assert not outcome.produced
        assert outcome.to_human

    def test_생성_단계까지_가지_않는다(self, tmp_path) -> None:
        # 지어낼 기회 자체를 주지 않는다.
        harness = FakeHarness(ANSWER % "k-x")
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
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % item.id))

        outcome = pipeline.run("결재 한도가 어떻게 정해지나요")
        assert [r.stage for r in outcome.stages] == [
            Stage.ANALYZE, Stage.RETRIEVE, Stage.GENERATE
        ]

    def test_초안과_근거가_나온다(self, tmp_path) -> None:
        repo = _repo(tmp_path)
        item = _item(repo)
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % item.id))

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
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % item.id))

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
        assert parse_draft('{"answerable": true, "body": "답", "grounding": []}', {"k-1"}) is None

    def test_지어낸_근거_id_는_버린다(self, tmp_path) -> None:
        # 없는 것을 가리키는 근거는 Lint 의 끊어진 링크가 되고, 그때는 이미 답이 나간 뒤다.
        assert parse_draft(
            '{"answerable": true, "body": "답", "grounding": ["k-지어냄"]}', {"k-1"}
        ) is None

    def test_실재하는_것만_남긴다(self) -> None:
        draft = parse_draft(
            '{"answerable": true, "body": "답", "grounding": ["k-1", "k-없음"]}', {"k-1"}
        )
        assert draft.grounding == ("k-1",)

    def test_본문이_비면_초안이_아니다(self) -> None:
        assert parse_draft('{"answerable": true, "body": "  ", "grounding": ["k-1"]}', {"k-1"}) is None


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
        pipeline, _, _ = _pipeline(tmp_path, FakeHarness(ANSWER % item.id))

        assert pipeline.run("결재 한도").summary() == "분석 → 조회 → 생성"

    def test_멈춘_이유가_요약에_보인다(self, tmp_path) -> None:
        pipeline, repo, _ = _pipeline(tmp_path, FakeHarness())
        _item(repo)
        assert "근거 없음" in pipeline.run("전혀 다른 주제").summary()
