"""WBS-4.6.1 — 콘텐츠 타입 레지스트리 (FR-42, §7.5).

**새 타입을 더하는 일이 코드를 짜는 일이 아니어야 한다.** 그렇지 않으면 타입마다
별도 구현이 쌓여 유지가 불가능해진다 (§7.5).

여기서 지키는 것은 넷.

    1. **다섯 선언으로 타입이 추가된다** — 코드를 고치지 않고 (FR-42 검증)
    2. **성격이 자리를 정한다** (§7.3 → §7.7) — 어긋난 선언은 등록되지 않는다
    3. **발행물의 검수를 낮출 수 없다** — 회수할 수 없는 것이기 때문이다
    4. **모르는 열쇠를 무시하지 않는다** — 오타는 조용히 기본값이 된다
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_service_desk.content import registry
from agentic_service_desk.content.registry import (
    InvalidDeclaration,
    Nature,
    Place,
    Scope,
)

MINIMAL = """
[release_note]
title = "릴리스 노트"
input = "knowledge"
nature = "issued"
trigger = { period_days = 30 }
destination = { place = "publication" }
review = { scope = "full", final_check = true }
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "types.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestBuiltins:
    """§7.2 의 넷이 선언으로 온다."""

    def test_넷이_등록되어_있다(self) -> None:
        assert {t.id for t in registry.load().all()} == {
            "faq",
            "guide",
            "column",
            "newsletter",
        }

    def test_성격이_타입을_가른다(self) -> None:
        # §7.3 — 가장 중요한 축이다. 이 구분이 없으면 "이미 발행한 뉴스레터를
        # 갱신한다" 같은 성립하지 않는 요구가 생긴다.
        reg = registry.load()
        assert {t.id for t in reg.living()} == {"faq", "guide"}
        assert {t.id for t in reg.issued()} == {"column", "newsletter"}

    def test_자리가_곧_연산이다(self) -> None:
        # §7.7.1 — 타입은 넷이어도 API 는 upsert 와 create 둘이면 된다.
        reg = registry.load()
        assert reg.get("guide").destination.place.operation == "upsert"
        assert reg.get("newsletter").destination.place.operation == "create"

    def test_칼럼에만_P6_P8_이_붙는다(self) -> None:
        # §7.6.4 — P1~P5 는 **사실 진술을 전제**하므로 칼럼에 그대로 쓰이지 않는다.
        reg = registry.load()
        assert reg.get("column").review.extra_rejections == ("P6", "P7", "P8")
        assert reg.get("faq").review.extra_rejections == ()

    def test_검수자는_타입이_고르지_않는다(self, tmp_path) -> None:
        # FR-39 — 콘텐츠는 **국면과 무관하게 전수 사람 승인**이다. 타입이 고를 수
        # 있게 두면 그 요구가 선언 한 줄로 꺼진다.
        assert all(t.review.reviewer == "human" for t in registry.load().all())
        text = MINIMAL.replace("final_check = true", 'final_check = true, reviewer = "agent"')
        with pytest.raises(InvalidDeclaration, match="모르는 열쇠"):
            registry.load(_write(tmp_path, text))


class TestExtension:
    """FR-42 — **새 타입 추가에 코드 변경이 필요 없다.**"""

    def test_선언만으로_타입이_추가된다(self, tmp_path) -> None:
        reg = registry.load(_write(tmp_path, MINIMAL))
        added = reg.get("release_note")

        assert added.title == "릴리스 노트"
        assert added.nature is Nature.ISSUED
        assert added.destination.place is Place.PUBLICATION
        assert len(reg.all()) == 5  # 기본 넷에 **더해진다**

    def test_기본_타입을_덮지_못한다(self, tmp_path) -> None:
        # 덮게 두면 발행물의 검수 강도가 설정 한 줄로 변경분 검수가 되고, 그
        # 사실이 어디에도 남지 않는다.
        text = MINIMAL.replace("[release_note]", "[newsletter]")
        with pytest.raises(InvalidDeclaration, match="이미 있다"):
            registry.load(_write(tmp_path, text))

    def test_없는_타입을_물으면_말해_준다(self) -> None:
        with pytest.raises(InvalidDeclaration, match="등록되지 않았다"):
            registry.load().get("podcast")


class TestDeclarationIsChecked:
    """선언만으로 돈다는 것은 **틀린 선언이 조용히 다르게 도는 것**과 종이 한 장 차이다."""

    def test_성격과_자리가_어긋나면_거부한다(self, tmp_path) -> None:
        # 발행물을 문서 면에 올리면 **지난 회차를 덮어쓴다** — 회수 불가인 것을
        # 회수한 척하게 된다 (§7.3·§7.7).
        text = MINIMAL.replace(
            'destination = { place = "publication" }',
            'destination = { place = "document", path = "notes/index" }',
        )
        with pytest.raises(InvalidDeclaration, match="자리는"):
            registry.load(_write(tmp_path, text))

    def test_살아있는_문서를_발행_면에_두지_못한다(self, tmp_path) -> None:
        # 갱신이 회차 누적이 되어 **같은 문서가 여러 벌**이 된다.
        text = MINIMAL.replace('nature = "issued"', 'nature = "living"')
        with pytest.raises(InvalidDeclaration, match="자리는"):
            registry.load(_write(tmp_path, text))

    def test_발행물의_검수를_낮출_수_없다(self, tmp_path) -> None:
        # 발행물은 W3(게재 후 진실 변화)를 **해소할 수 없다** (§7.3) — 이미 읽힌
        # 회차는 되돌아오지 않는다.
        text = MINIMAL.replace(
            'review = { scope = "full", final_check = true }',
            'review = { scope = "diff" }',
        )
        with pytest.raises(InvalidDeclaration, match="발행물은 전문 검수"):
            registry.load(_write(tmp_path, text))

    def test_최종_확인을_빠뜨리면_거부한다(self, tmp_path) -> None:
        text = MINIMAL.replace(", final_check = true", "")
        with pytest.raises(InvalidDeclaration, match="발행물은 전문 검수"):
            registry.load(_write(tmp_path, text))

    def test_모르는_열쇠를_무시하지_않는다(self, tmp_path) -> None:
        # `final_chek` 하나로 발행물의 최종 확인이 꺼지고, 그 사실은 **회수할 수
        # 없는 글이 나간 뒤에야** 드러난다.
        text = MINIMAL.replace("final_check = true", "final_chek = true")
        with pytest.raises(InvalidDeclaration, match="모르는 열쇠"):
            registry.load(_write(tmp_path, text))

    def test_다섯이_다_있어야_한다(self, tmp_path) -> None:
        text = MINIMAL.replace('input = "knowledge"\n', "")
        with pytest.raises(InvalidDeclaration, match="다섯을 모두 선언"):
            registry.load(_write(tmp_path, text))

    def test_트리거가_없으면_영영_돌지_않는다(self, tmp_path) -> None:
        text = MINIMAL.replace("trigger = { period_days = 30 }", "trigger = {}")
        with pytest.raises(InvalidDeclaration, match="영영 돌지 않는다"):
            registry.load(_write(tmp_path, text))

    def test_임계_이름만_있고_값이_없으면_거부한다(self, tmp_path) -> None:
        text = MINIMAL.replace(
            "trigger = { period_days = 30 }", 'trigger = { threshold = "repeat" }'
        )
        with pytest.raises(InvalidDeclaration, match="무엇이 차야"):
            registry.load(_write(tmp_path, text))

    def test_문서_면에는_경로가_필요하다(self, tmp_path) -> None:
        text = (
            MINIMAL.replace('nature = "issued"', 'nature = "living"')
            .replace(
                'destination = { place = "publication" }',
                'destination = { place = "document" }',
            )
            .replace('review = { scope = "full", final_check = true }', 'review = { scope = "diff" }')
        )
        with pytest.raises(InvalidDeclaration, match="경로가 필요하다"):
            registry.load(_write(tmp_path, text))

    def test_발행_면에는_경로가_없다(self, tmp_path) -> None:
        # 회차는 경로를 갖지 않는다 — 있으면 무엇을 덮어쓸 셈인지 묻게 된다.
        text = MINIMAL.replace(
            'destination = { place = "publication" }',
            'destination = { place = "publication", path = "notes/index" }',
        )
        with pytest.raises(InvalidDeclaration, match="회차는 경로를"):
            registry.load(_write(tmp_path, text))

    def test_없는_값은_무엇이_가능한지_말해_준다(self, tmp_path) -> None:
        text = MINIMAL.replace('nature = "issued"', 'nature = "영구"')
        with pytest.raises(InvalidDeclaration, match="living"):
            registry.load(_write(tmp_path, text))

    def test_읽을_수_없는_파일은_기동에서_터진다(self, tmp_path) -> None:
        with pytest.raises(InvalidDeclaration, match="선언 파일이 없다"):
            registry.load(tmp_path / "없다.toml")

    def test_기본_선언은_모두_통과한다(self) -> None:
        # 검사를 나중에 붙이면 기본 넷이 그 검사를 통과하지 못하는 일이 생긴다.
        assert registry.load().all()  # 예외 없이 읽힌다
        assert registry.load().get("column").review.scope is Scope.FULL
