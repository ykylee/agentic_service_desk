"""WBS-4.2.4 — 지식 항목 ↔ 파일 (FR-5, FR-55).

파일 왕복이 **불변식을 잃지 않는가**를 본다. 특히 불변 `id` — 그것이 왕복에서
새로 생기면 답변 이력의 근거 링크가 파일을 한 번 읽고 쓸 때마다 끊긴다 (ADR-002).
"""

from __future__ import annotations

import pytest

from agentic_service_desk.knowledge.item import (
    Invalidation,
    InvalidationKind,
    KnowledgeItem,
    Provenance,
)
from agentic_service_desk.knowledge.serialize import (
    MalformedItem,
    from_markdown,
    slugify,
    to_markdown,
)


def _item(**over) -> KnowledgeItem:  # noqa: ANN003
    base = dict(
        title="결재 한도가 결정되는 규칙",
        body="부서 등급에 따라 정해진다.\n\n두 번째 문단.",
        provenance=[Provenance(commit="a1b2c3d", path="src/approval/limit.py")],
        invalidation=Invalidation(
            kind=InvalidationKind.LINKED, refs=("src/approval/limit.py",)
        ),
    )
    base.update(over)
    return KnowledgeItem(**base)  # type: ignore[arg-type]


class TestRoundTrip:
    def test_왕복해도_같다(self) -> None:
        item = _item()
        back = from_markdown(to_markdown(item))
        assert back.title == item.title
        assert back.body == item.body
        assert back.provenance == item.provenance
        assert back.invalidation == item.invalidation

    def test_불변_id_가_유지된다(self) -> None:
        # 왕복에서 새 id 가 생기면 답변 이력의 근거 링크가 끊긴다 (ADR-002).
        item = _item()
        assert from_markdown(to_markdown(item)).id == item.id

    def test_사람_편집_표시가_유지된다(self) -> None:
        # 이것이 사라지면 다음 ingest 가 사람의 수정을 덮어쓴다 (D38).
        item = _item(edited_by_human=True)
        assert from_markdown(to_markdown(item)).edited_by_human is True

    def test_주기형_무효화_조건도_왕복한다(self) -> None:
        item = _item(
            invalidation=Invalidation(kind=InvalidationKind.PERIODIC, period_days=180)
        )
        back = from_markdown(to_markdown(item))
        assert back.invalidation.kind is InvalidationKind.PERIODIC
        assert back.invalidation.period_days == 180

    def test_한국어가_이스케이프되지_않는다(self) -> None:
        # 사람이 읽고 고칠 수 있어야 한다 (D37).
        assert "결재 한도가 결정되는 규칙" in to_markdown(_item())

    def test_OKF_필수_필드가_있다(self) -> None:
        assert to_markdown(_item()).startswith("---\ntype: knowledge\n")


class TestInvariantsSurviveTheFile:
    """파일은 사람이 고칠 수 있다. **고쳐진 파일도 불변식을 지켜야 한다.**"""

    def test_출처가_없으면_거부한다(self) -> None:
        text = "---\ntype: knowledge\ntitle: 무엇\ninvalidation:\n  kind: periodic\n  period_days: 30\n---\n\n본문\n"
        with pytest.raises(MalformedItem):
            from_markdown(text)

    def test_무효화_조건이_없으면_거부한다(self) -> None:
        text = "---\ntype: knowledge\ntitle: 무엇\nprovenance:\n  - commit: a1b2c3d\n---\n\n본문\n"
        with pytest.raises(MalformedItem):
            from_markdown(text)

    def test_frontmatter_가_없으면_거부한다(self) -> None:
        with pytest.raises(MalformedItem):
            from_markdown("# 그냥 마크다운\n")

    def test_깨진_yaml_은_거부한다(self) -> None:
        with pytest.raises(MalformedItem):
            from_markdown("---\ntitle: [열고 안 닫음\n---\n\n본문\n")


class TestSlug:
    def test_한글을_그대로_둔다(self) -> None:
        # 식별은 불변 id 가 한다. 이름은 사람이 읽기 위한 것이다.
        assert slugify("결재 한도가 결정되는 규칙") == "결재-한도가-결정되는-규칙"

    def test_빈_제목도_이름을_얻는다(self) -> None:
        assert slugify("   ") == "untitled"
