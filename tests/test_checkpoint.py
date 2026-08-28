"""배치 진행 지점 (ADR-005 · ADR-006).

배치는 중단 가능해야 하고 중단돼도 이어서 해야 한다. 여기서 지키는 것은
**커서가 없을 때 최초 1회 전체 수집의 신호가 되는가**(FR-2)다.
"""

from __future__ import annotations

from agentic_service_desk.operations.checkpoint import QNA, SOURCE, get_cursor, set_cursor
from agentic_service_desk.operations.schema import connect, initialize


def _conn(tmp_path):  # noqa: ANN001, ANN202
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


class TestCursor:
    def test_처음에는_없다(self, tmp_path) -> None:
        # None 이 곧 "전체 수집" 신호다 (FR-2).
        assert get_cursor(_conn(tmp_path), SOURCE) is None

    def test_적고_읽는다(self, tmp_path) -> None:
        c = _conn(tmp_path)
        set_cursor(c, SOURCE, "a1b2c3d")
        assert get_cursor(c, SOURCE) == "a1b2c3d"

    def test_옮길_수_있다(self, tmp_path) -> None:
        c = _conn(tmp_path)
        set_cursor(c, SOURCE, "old")
        set_cursor(c, SOURCE, "new")
        assert get_cursor(c, SOURCE) == "new"

    def test_종류별로_따로_간다(self, tmp_path) -> None:
        # 소스는 커밋 해시, QnA 는 시각이다 — 진행 속도가 다르다.
        c = _conn(tmp_path)
        set_cursor(c, SOURCE, "a1b2c3d")
        set_cursor(c, QNA, "2026-08-28T00:00:00Z")
        assert get_cursor(c, SOURCE) == "a1b2c3d"
        assert get_cursor(c, QNA) == "2026-08-28T00:00:00Z"

    def test_다시_연결해도_남아_있다(self, tmp_path) -> None:
        # 배치가 죽었다 살아나도 이어서 해야 한다.
        set_cursor(_conn(tmp_path), SOURCE, "a1b2c3d")
        assert get_cursor(_conn(tmp_path), SOURCE) == "a1b2c3d"
