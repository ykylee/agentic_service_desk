"""WBS-4.2.4 — Ingest 에이전트 (FR-3·4·9·53, ADR-003).

**모델이 정하는 것과 코드가 정하는 것의 경계**를 시험한다.
출처는 코드가 붙이므로 모델이 무엇을 하든 출처 없는 항목은 나올 수 없어야 한다.
"""

from __future__ import annotations

import pytest

from agentic_service_desk.ingest.agent import (
    AgentOutputError,
    IngestAgent,
    ProposedItem,
    QnaMaterial,
    SourceMaterial,
    build_source_prompt,
    extract_json,
    parse_proposals,
    prepare_source_material,
    qna_provenance,
    source_provenance,
    to_knowledge_item,
)
from agentic_service_desk.ingest.config_paths import is_config_path
from conftest import FakeHarness

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)


ONE_ITEM = """
{"items": [{"id": null, "title": "결재 한도가 결정되는 규칙",
 "body": "부서 등급에 따라 정해진다.",
 "invalidation": {"kind": "linked", "refs": ["src/approval/limit.py"]},
 "used_paths": ["src/approval/limit.py"]}]}
"""


class TestConfigExclusion:
    """FR-9 — 설정값은 지식이 아니라 상태다."""

    def test_설정_경로가_원천에서_잘린다(self) -> None:
        material, dropped = prepare_source_material(
            "a1b2c3d",
            [],
            [("src/approval/limit.py", "code"), ("config/app.yaml", "limit: 3000000")],
        )
        assert material.paths == {"src/approval/limit.py"}
        assert dropped == ["config/app.yaml"]

    def test_잘린_것이_보고된다(self) -> None:
        # 조용히 빼면 경계가 잘못 잡혔을 때 아무도 모른다.
        _, dropped = prepare_source_material("a1b2c3d", [], [(".env", "K=V")])
        assert dropped == [".env"]

    def test_넉넉하게_자른다(self) -> None:
        # 아닌 것을 몇 개 놓치는 대가보다 설정값이 새는 대가가 크다.
        assert is_config_path("src/data/schema.json")
        assert not is_config_path("src/approval/limit.py")


class TestParsing:
    def test_코드_울타리를_벗겨낸다(self) -> None:
        assert extract_json('앞말\n```json\n{"items": []}\n```\n뒷말') == {"items": []}

    def test_울타리가_없어도_찾는다(self) -> None:
        assert extract_json('설명입니다. {"items": []} 끝') == {"items": []}

    def test_본문_안의_중괄호에서_끊기지_않는다(self) -> None:
        # 정규식으로 `{.*}` 를 잡으면 여기서 틀린다.
        text = '{"items": [{"title": "가", "body": "코드는 {x: 1} 이다", "id": null}]}'
        assert len(extract_json(text)["items"]) == 1

    def test_문자열_안의_중괄호도_센다(self) -> None:
        text = '{"items": [{"title": "가", "body": "닫는 괄호 } 하나", "id": null}]}'
        assert extract_json(text)["items"][0]["body"] == "닫는 괄호 } 하나"

    def test_JSON_이_없으면_실패한다(self) -> None:
        with pytest.raises(AgentOutputError):
            parse_proposals("만들 것이 없습니다.")

    def test_울타리가_여럿이면_읽히는_것을_고른다(self) -> None:
        # 실제 모델이 설명용 블록을 먼저 내놓는 일이 있다.
        text = '```\n설명입니다\n```\n```json\n{"items": []}\n```'
        assert parse_proposals(text) == []

    def test_실패_메시지가_받은_것을_싣는다(self) -> None:
        # 없으면 로그를 봐도 고칠 수가 없다 — 모델 출력은 재현이 어렵다.
        with pytest.raises(AgentOutputError, match="이상한 응답"):
            parse_proposals("이상한 응답이 왔다")

    def test_items_가_null_이면_만들_것이_없는_것이다(self) -> None:
        # 형식의 빗나감이다 — 뜻이 분명하므로 읽어 준다.
        assert parse_proposals('{"items": null}') == []

    def test_항목_하나를_배열로_안_감싸도_읽는다(self) -> None:
        text = '{"items": {"title": "가", "body": "나"}}'
        assert len(parse_proposals(text)) == 1

    def test_items_키가_아예_없으면_실패한다(self) -> None:
        # 형식의 어김이다. 조용히 "만들 것 없음"으로 넘기면 원천 하나가 소리 없이
        # 지식이 되지 못한 채 처리 완료로 표시된다.
        with pytest.raises(AgentOutputError, match="`items` 가 없다"):
            parse_proposals('{"result": "ok"}')

    def test_제목이나_본문이_비면_버린다(self) -> None:
        text = '{"items": [{"title": "", "body": "본문"}, {"title": "가", "body": ""}]}'
        assert parse_proposals(text) == []

    def test_갱신_대상을_읽는다(self) -> None:
        text = '{"items": [{"id": "k-abc", "title": "가", "body": "나"}]}'
        assert parse_proposals(text)[0].item_id == "k-abc"


class TestProvenanceIsAttachedByCode:
    """FR-4 — 출처는 모델이 아니라 코드가 붙인다."""

    def test_커밋이_출처가_된다(self) -> None:
        material = SourceMaterial(commit="a1b2c3d", files=(("src/a.py", "x"),))
        p = source_provenance(ProposedItem(title="가", body="나", used_paths=("src/a.py",)), material)
        assert p == [Provenance(commit="a1b2c3d", path="src/a.py")]

    def test_지어낸_경로는_인정하지_않는다(self) -> None:
        # 틀린 출처는 붙어 있다는 사실 때문에 오히려 그럴듯해진다 (§2.2.3).
        material = SourceMaterial(commit="a1b2c3d", files=(("src/a.py", "x"),))
        p = source_provenance(
            ProposedItem(title="가", body="나", used_paths=("src/없는파일.py",)), material
        )
        assert p == [Provenance(commit="a1b2c3d")]

    def test_경로가_하나도_없어도_커밋은_남는다(self) -> None:
        # 출처 없는 항목은 만들지 않는다 (D3).
        material = SourceMaterial(commit="a1b2c3d")
        assert source_provenance(ProposedItem(title="가", body="나"), material)[0].commit

    def test_QnA_는_질문_id_가_출처다(self) -> None:
        m = QnaMaterial(answer_id="A-1", question_id="Q-2", question="가", answer="나")
        assert qna_provenance(m) == [Provenance(qna="Q-2")]

    def test_출처_없이는_항목을_만들_수_없다(self) -> None:
        with pytest.raises(ValueError):
            to_knowledge_item(ProposedItem(title="가", body="나"), provenance=[])


class TestInvalidation:
    """§6.5.3 — 무효화 조건이 없으면 stale 판정을 영영 못 받는다."""

    def _item(self, proposal: ProposedItem) -> KnowledgeItem:
        return to_knowledge_item(proposal, provenance=[Provenance(commit="a1b2c3d")])

    def test_모델의_제안을_쓴다(self) -> None:
        item = self._item(
            ProposedItem(title="가", body="나", invalidation_kind="linked", refs=("src/a.py",))
        )
        assert item.invalidation.kind is InvalidationKind.LINKED
        assert item.invalidation.refs == ("src/a.py",)

    def test_linked_인데_대상이_없으면_대비값으로_간다(self) -> None:
        # 이 상태로 Invalidation 을 만들면 ValueError 다 — 항목이 통째로 사라진다.
        item = self._item(ProposedItem(title="가", body="나", invalidation_kind="linked"))
        assert item.invalidation.kind is InvalidationKind.PERIODIC

    def test_조건이_없으면_근거_경로에_묶는다(self) -> None:
        # 그 파일이 바뀌면 이 지식이 틀려질 수 있다는 뜻이다.
        item = self._item(ProposedItem(title="가", body="나", used_paths=("src/a.py",)))
        assert item.invalidation.kind is InvalidationKind.LINKED
        assert item.invalidation.refs == ("src/a.py",)

    def test_묶을_것이_전혀_없으면_주기형이_된다(self) -> None:
        item = self._item(ProposedItem(title="가", body="나"))
        assert item.invalidation.kind is InvalidationKind.PERIODIC
        assert item.invalidation.period_days


class TestUpdate:
    def test_갱신은_불변_id_를_물려받는다(self) -> None:
        base = KnowledgeItem(
            title="옛 제목",
            body="옛 본문",
            provenance=[Provenance(commit="old")],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=30),
        )
        item = to_knowledge_item(
            ProposedItem(title="새 제목", body="새 본문"),
            provenance=[Provenance(commit="new")],
            base=base,
        )
        assert item.id == base.id
        assert item.title == "새 제목"

    def test_갱신은_출처를_합친다(self) -> None:
        # 덮어쓰면 이 항목이 원래 어느 커밋에서 왔는지가 사라진다.
        base = KnowledgeItem(
            title="가",
            body="나",
            provenance=[Provenance(commit="old")],
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=30),
        )
        item = to_knowledge_item(
            ProposedItem(title="가", body="다"),
            provenance=[Provenance(commit="new")],
            base=base,
        )
        assert Provenance(commit="old") in item.provenance
        assert Provenance(commit="new") in item.provenance


class TestPrompt:
    def test_이미_있는_항목을_알려준다(self) -> None:
        # 없으면 같은 개념이 매번 새 항목으로 갈린다 (ingest 절차 2단계).
        prompt = build_source_prompt(
            SourceMaterial(commit="a1b2c3d"), [("k-1", "결재 한도가 결정되는 규칙")]
        )
        assert "k-1: 결재 한도가 결정되는 규칙" in prompt

    def test_커밋_메시지가_실린다(self) -> None:
        # 히스토리가 "왜 그렇게 정했는가"의 1차 출처다 (D16, §2.2.1).
        prompt = build_source_prompt(
            SourceMaterial(commit="a1b2c3d", messages=("한도 계산을 부서 등급으로 바꿈",)), []
        )
        assert "한도 계산을 부서 등급으로 바꿈" in prompt

    def test_설정값_금지가_지시에_있다(self) -> None:
        assert "설정값을 지식으로 쓰지 않는다" in build_source_prompt(
            SourceMaterial(commit="a"), []
        )

    def test_언어를_나누지_말라는_지시가_있다(self) -> None:
        # FR-53 — 항목은 원천의 언어를 따른다.
        assert "원천의 언어를 그대로 따른다" in build_source_prompt(
            SourceMaterial(commit="a"), []
        )


class TestAgent:
    def test_응답을_제안으로_바꾼다(self) -> None:
        harness = FakeHarness(ONE_ITEM)
        proposals = IngestAgent(harness).from_source(SourceMaterial(commit="a1b2c3d"), [])
        assert len(proposals) == 1
        assert proposals[0].title == "결재 한도가 결정되는 규칙"

    def test_QnA_원천도_같은_경로를_쓴다(self) -> None:
        harness = FakeHarness(ONE_ITEM)
        m = QnaMaterial(answer_id="A-1", question_id="Q-2", question="가", answer="나")
        assert IngestAgent(harness).from_qna(m, [])
        assert "질문: 가" in harness.prompts[0]
