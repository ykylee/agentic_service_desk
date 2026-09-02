"""WBS-4.4.1 — 지식베이스 조회 (ADR-004, O31, §2.5.4).

**검색 품질이 곧 커버리지다.** 근거를 못 찾으면 답변이 만들어지지 않고 티켓으로
가므로(FR-18), 못 찾는 것과 없는 것이 결과적으로 같아진다.

여기서 지키는 것은 넷.

    1. **언어별 도구를 쓰지 않고** 조사 변형을 넘는다 (ADR-003 제약 1)
    2. **표현 사전이 어휘 격차를 메운다** — 이용자는 "결재", 코드는 approval (§2.5.4)
    3. 임베딩·LLM 이 **없어도 돈다** — 개발 환경에서 임베딩이 막혀 있다 (O57)
    4. **못 찾으면 빈 목록이다** — 억지로 채우면 FR-18 이 무력해진다
"""

from __future__ import annotations

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.knowledge.search import (
    Search,
    VocabularyIndex,
    cosine,
    rebuild_embedding_index,
    rerank,
    stored_vectors,
    tokenize,
)
from agentic_service_desk.llm.gateway import EmbeddingPurpose
from agentic_service_desk.operations import manual_entry
from agentic_service_desk.operations.schema import connect, initialize

from conftest import FakeHarness


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _repo(tmp_path) -> KnowledgeRepository:
    repo = KnowledgeRepository(tmp_path / "knowledge")
    repo.ensure_initialized()
    return repo


def _item(repo, title, body="본문이다.", **over):  # noqa: ANN001, ANN003
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


class FakeEmbeddings:
    """정해진 벡터를 돌려준다."""

    def __init__(self, by_text: dict[str, list[float]], fail: bool = False) -> None:
        self._by_text = by_text
        self._fail = fail
        self.purposes: list[EmbeddingPurpose] = []

    def embed(self, texts, purpose):  # noqa: ANN001, ANN201
        if self._fail:
            raise RuntimeError("레이트 리밋")
        self.purposes.append(purpose)
        return [self._by_text.get(t, [0.0, 0.0, 1.0]) for t in texts]


class TestTokenize:
    def test_한국어와_식별자를_함께_잡는다(self) -> None:
        assert tokenize("결재 한도 approval_limit 300") == [
            "결재", "한도", "approval_limit", "300"
        ]

    def test_한_글자는_버린다(self) -> None:
        # 어디에나 맞아 순위를 무너뜨린다.
        assert "가" not in tokenize("가 결재")


class TestKeyword:
    def test_제목이_본문보다_무겁다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙", body="관계없는 본문")
        _item(repo, "관계없는 제목", body="결재 한도가 나온다")

        hits = Search(repo=repo, conn=conn).find("결재 한도")
        assert hits[0].item.title == "결재 한도 규칙"

    def test_조사가_붙어도_찾는다(self, tmp_path) -> None:
        # 형태소 분석기를 들이면 언어 수만큼 도구가 늘어난다 (ADR-003 제약 1).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")

        assert Search(repo=repo, conn=conn).find("결재를 어떻게 하나요")

    def test_반대_방향도_찾는다(self, tmp_path) -> None:
        # 질의가 짧고 문서가 긴 경우다. 한쪽만 보면 절반을 놓친다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재를 반려하는 조건")

        assert Search(repo=repo, conn=conn).find("결재 반려")

    def test_식별자로도_찾는다(self, tmp_path) -> None:
        # 개발자가 코드 용어로 물을 때 — 임베딩만 쓰면 놓치는 쪽이다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "한도 계산", body="approval_limit(grade) 가 계산한다")

        assert Search(repo=repo, conn=conn).find("approval_limit")

    def test_왜_걸렸는지가_남는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        assert Search(repo=repo, conn=conn).find("결재")[0].matched_by == {"keyword"}

    def test_뒤에서_겹치는_것은_같은_낱말이_아니다(self, tmp_path) -> None:
        # 조사·어미는 **뒤에** 붙으므로 어간이 앞에 온다. 그 가정을 넘어 아무
        # 자리나 받으면 "백테스트"가 "테스트"에 걸린다 — 그리고 그것이 제목이면
        # 우연한 겹침이 개념 이름의 일치와 같은 무게(3.0)를 받는다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "테스트 인프라 사전 검증")

        assert Search(repo=repo, conn=conn).find("백테스트") == []

    def test_앞에서_겹치면_여전히_찾는다(self, tmp_path) -> None:
        # 좁힌 것이 너무 넓게 잡지 않는지 — 넘으려던 변형은 그대로 넘어야 한다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "백테스트 결과 읽는 법")

        assert Search(repo=repo, conn=conn).find("백테스트를 어떻게 보나요")

    def test_단일_낱말_질의는_여전히_근거를_얻는다(self, tmp_path) -> None:
        # 낱말 하나만 맞은 것을 버리는 규칙(`MIN_VOCAB_MATCHES`)을 키워드 다리에
        # 얹지 않은 이유다 — 개발자는 식별자 하나로 묻는다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "가드 auto_transition 실행 단계")

        assert Search(repo=repo, conn=conn).find("가드")


class TestNothingFound:
    """FR-18 의 입력 — 억지로 채우지 않는다."""

    def test_안_맞으면_빈_목록이다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        assert Search(repo=repo, conn=conn).find("전혀 다른 주제 휴가") == []

    def test_지식이_없으면_빈_목록이다(self, tmp_path) -> None:
        assert Search(repo=_repo(tmp_path), conn=_conn(tmp_path)).find("결재") == []

    def test_질의가_비면_빈_목록이다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        assert Search(repo=repo, conn=conn).find("!!!") == []


class TestVocabulary:
    """§2.5.4 — 어휘 격차를 메우는 것은 QnA 원천의 고유 기여다."""

    def _with_qna(self, tmp_path):  # noqa: ANN001, ANN202
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        r = manual_entry.register(
            conn,
            question="결재 반려 사유를 어디서 보나요?",
            answer="상세 화면에서 확인합니다.",
        )
        # 그 QnA 에서 온 지식 항목 — 제목은 코드 용어로 되어 있다
        _item(
            repo,
            "approval rejection reason lookup",
            body="rejection reason is shown in detail view",
            provenance=[Provenance(qna=r.qna_item_id)],
        )
        return repo, conn

    def test_이용자_표현으로_코드_용어_항목을_찾는다(self, tmp_path) -> None:
        # 키워드만으로는 "결재 반려" 가 영어 제목 항목에 닿지 않는다.
        repo, conn = self._with_qna(tmp_path)
        hits = Search(repo=repo, conn=conn).find("결재 반려")

        assert hits
        assert "vocabulary" in hits[0].matched_by

    def test_한_낱말만_맞으면_걸리지_않는다(self, tmp_path) -> None:
        """라이브에서 잡은 것 — 질문의 틀이 개념을 가리키는 척한다.

        "VPN 접속이 안 되는데 어떻게 하나요"가 "결재 한도 결정 규칙"에 걸렸고,
        걸린 이유가 **"어떻게" 하나**였다. 한 낱말이 맞은 것은 개념이 같다는
        증거가 아니다.
        """
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        r = manual_entry.register(
            conn, question="승인 한도는 어떻게 정해지나요?", answer="등급입니다."
        )
        _item(repo, "approval limit", provenance=[Provenance(qna=r.qna_item_id)])

        assert Search(repo=repo, conn=conn).find("VPN 이 안 되는데 어떻게 하나요") == []

    def test_여러_낱말이_맞으면_걸린다(self, tmp_path) -> None:
        # 진짜 어휘 격차 질의는 여러 낱말이 함께 맞는다.
        repo, conn = self._with_qna(tmp_path)
        assert Search(repo=repo, conn=conn).find("결재 반려 사유")

    def test_여러_항목을_함께_가리키는_말은_버린다(self, tmp_path) -> None:
        # 불용어 목록을 쓰지 않는다 — 언어마다 따로 만들어야 한다 (ADR-003 제약 1).
        # 대신 몇 개 항목을 가리키는가로 판정한다. QnA 가 쌓일수록 정확해진다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        for q, title in [
            ("결재 반려 사유는 어떻게 확인하나요?", "approval rejection"),
            ("휴가 신청은 어떻게 취소하나요?", "leave cancellation"),
            ("권한 요청은 어떻게 하나요?", "permission request"),
        ]:
            r = manual_entry.register(conn, question=q, answer="답입니다.")
            _item(repo, title, provenance=[Provenance(qna=r.qna_item_id)])

        index = VocabularyIndex.build(conn, repo.scan()[0])
        assert not any("어떻게" in terms for terms in index._by_item.values())
        assert any("결재" in terms for terms in index._by_item.values())

    def test_QnA_링크가_없으면_사전도_비어_있다(self, tmp_path) -> None:
        # 1국면에는 비어 있고 그것이 정상이다 (D14).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        assert len(VocabularyIndex.build(conn, repo.scan()[0])) == 0

    def test_수동_등록_질문도_사전에_들어간다(self, tmp_path) -> None:
        # 담당자가 옮겨 적은 것도 **이용자가 쓴 말**이다.
        repo, conn = self._with_qna(tmp_path)
        index = VocabularyIndex.build(conn, repo.scan()[0])
        assert "결재" in next(iter(index._by_item.values()))


class TestEmbeddingLeg:
    """ADR-004 — 있으면 쓰고 없으면 건너뛴다."""

    def test_제공자가_없으면_그냥_돈다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        assert Search(repo=repo, conn=conn, embeddings=None).find("결재")

    def test_인덱스가_비면_그냥_돈다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        hits = Search(repo=repo, conn=conn, embeddings=FakeEmbeddings({})).find("결재")
        assert hits and "embedding" not in hits[0].matched_by

    def test_제공자가_터져도_검색이_서지_않는다(self, tmp_path) -> None:
        # 개발 환경에서 임베딩이 레이트 리밋으로 막혀 있다 (O57).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo, "결재 한도 규칙")
        rebuild_embedding_index(conn, repo, FakeEmbeddings({}), "m")

        hits = Search(
            repo=repo, conn=conn, embeddings=FakeEmbeddings({}, fail=True)
        ).find("결재")
        assert hits and hits[0].item.id == item.id

    def test_표현이_달라도_임베딩이_잡는다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        item = _item(repo, "approval limit rule", body="determined by grade")
        text = f"{item.title}\n\n{item.body}"
        provider = FakeEmbeddings({text: [1.0, 0.0, 0.0], "결재 한도": [1.0, 0.0, 0.0]})
        rebuild_embedding_index(conn, repo, provider, "m")

        hits = Search(repo=repo, conn=conn, embeddings=provider).find("결재 한도")
        assert hits and "embedding" in hits[0].matched_by

    def test_색인과_질의의_용도가_다르다(self, tmp_path) -> None:
        # 지식 항목을 색인하는 것과 질문으로 질의하는 것은 다른 일이다 (ADR-004).
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        provider = FakeEmbeddings({})
        rebuild_embedding_index(conn, repo, provider, "m")
        Search(repo=repo, conn=conn, embeddings=provider).find("결재")

        assert provider.purposes == [EmbeddingPurpose.INDEX, EmbeddingPurpose.QUERY]

    def test_인덱스를_통째로_다시_만든다(self, tmp_path) -> None:
        # 증분 인덱싱의 복잡도를 지금 떠안지 않는다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "첫 항목")
        rebuild_embedding_index(conn, repo, FakeEmbeddings({}), "m")
        assert len(stored_vectors(conn)) == 1

        _item(repo, "둘째 항목")
        assert rebuild_embedding_index(conn, repo, FakeEmbeddings({}), "m") == 2
        assert len(stored_vectors(conn)) == 2

    def test_코사인이_방향만_본다(self) -> None:
        assert cosine([1.0, 0.0], [2.0, 0.0]) == 1.0
        assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
        assert cosine([], [1.0]) == 0.0


class TestRerank:
    """ADR-004 3단계 — 순위를 좋게 하는 장치이지 결과를 만드는 장치가 아니다."""

    def _hits(self, tmp_path):  # noqa: ANN001, ANN202
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙")
        _item(repo, "결재 반려 사유")
        return Search(repo=repo, conn=conn).find("결재")

    def test_없으면_원래_순서다(self, tmp_path) -> None:
        hits = self._hits(tmp_path)
        assert rerank(hits, "결재", None) == hits

    def test_모델이_고른_순서를_따른다(self, tmp_path) -> None:
        hits = self._hits(tmp_path)
        wanted = hits[1].item.id
        out = rerank(hits, "결재", FakeHarness(f'{{"ids": ["{wanted}"]}}'))
        assert out[0].item.id == wanted

    def test_모델이_빠뜨린_것을_버리지_않는다(self, tmp_path) -> None:
        # 버리면 근거가 있는데 없다고 판정되어 FR-18 이 잘못 발동한다.
        hits = self._hits(tmp_path)
        out = rerank(hits, "결재", FakeHarness(f'{{"ids": ["{hits[0].item.id}"]}}'))
        assert len(out) == len(hits)

    def test_지어낸_id_는_무시한다(self, tmp_path) -> None:
        hits = self._hits(tmp_path)
        out = rerank(hits, "결재", FakeHarness('{"ids": ["k-지어냄"]}'))
        assert len(out) == len(hits)
        assert all(h.item.id != "k-지어냄" for h in out)

    def test_응답이_깨져도_원래_순서를_준다(self, tmp_path) -> None:
        hits = self._hits(tmp_path)
        assert rerank(hits, "결재", FakeHarness("JSON 이 아니다")) == hits


class TestStale:
    def test_기본은_stale_도_준다(self, tmp_path) -> None:
        # 낡은 것과 틀린 것은 다르다. 거르는 판단은 호출부가 한다.
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙", stale=True)
        assert Search(repo=repo, conn=conn).find("결재")

    def test_뺄_수도_있다(self, tmp_path) -> None:
        repo, conn = _repo(tmp_path), _conn(tmp_path)
        _item(repo, "결재 한도 규칙", stale=True)
        assert Search(repo=repo, conn=conn).find("결재", include_stale=False) == []
