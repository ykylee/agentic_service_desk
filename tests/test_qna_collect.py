"""WBS-4.2.2 — QnA 수집 (FR-52, NFR-7).

여기서 지키는 것은 넷이다.

    1. **두 필드가 남는가** — 답변의 작성자 계정(D7)과 해결의 등급(D8).
       둘 중 하나만 빠져도 §5.3 되먹임 차단이 성립하지 않는다
    2. **걸러내지 않는가** — 봇이 쓴 미해결 답변까지 Raw Layer 에 들어와야 한다.
       거르는 자리는 ingest 입구 하나뿐이다 (NFR-4)
    3. **새 질문만 보지 않는가** — 어제 질문에 오늘 달린 후속과 해결 표시를 잡아야 한다
    4. **등급이 상향만 되는가** (§5.3.2)
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from agentic_service_desk.adapters.contract import (
    Answer,
    Followup,
    Resolution,
    ResolutionMethod,
)
from agentic_service_desk.adapters.mock import BOT_ACCOUNT, MockParentSystem
from agentic_service_desk.ingest.qna import (
    GradeDowngradeRejected,
    QnaCollector,
    QnaStore,
    ResolutionGrade,
    grade_of,
)
from agentic_service_desk.operations.checkpoint import QNA, get_cursor
from agentic_service_desk.operations.schema import connect, initialize


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TestGradeMapping:
    """모 시스템의 해결 표시 → 등급 (§5.3.1)."""

    def test_미해결에는_등급이_없다(self) -> None:
        assert grade_of(Resolution(question_id="Q-1", resolved=False)) is None

    def test_사람이_표시한_해결은_명시적이다(self) -> None:
        # 이용자의 해결 표시 — 명시적 해결의 1차 신호다 (D35, §5.3.1-1).
        r = Resolution(
            question_id="Q-1", resolved=True, method=ResolutionMethod.USER_MARKED
        )
        assert grade_of(r) is ResolutionGrade.EXPLICIT

    def test_방법이_비면_암묵으로_민다(self) -> None:
        # 누가 어떻게 확인했는지 모르는 해결을 명시적이라 부를 수 없다.
        # 애매한 쪽을 암묵으로 두는 것이 안전한 방향이다 — 상향은 나중에 되지만
        # 잘못 부여된 명시적 해결은 틀린 지식을 만든 뒤에야 드러난다.
        r = Resolution(question_id="Q-1", resolved=True)
        assert grade_of(r) is ResolutionGrade.IMPLICIT


class TestCollect:
    """mock 시드 다섯 분기를 그대로 수집한다."""

    def setup_method(self) -> None:
        self.parent = MockParentSystem()

    def test_질문_답변_후속_해결이_모두_적재된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        report = QnaCollector(self.parent, conn).collect()

        assert report.new_questions == 5
        assert conn.execute("SELECT COUNT(*) FROM raw_question").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM raw_answer").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM raw_followup").fetchone()[0] == 1

    def test_답변에_작성자_계정이_남는다(self, tmp_path) -> None:
        # D7 — 대안이 없는 유일한 필드다. 없으면 봇/사람을 가릴 수 없다.
        conn = _conn(tmp_path)
        QnaCollector(self.parent, conn).collect()
        store = QnaStore(conn)

        bot = store.answers_for("Q-2")[0]
        human = store.answers_for("Q-4")[0]
        assert bot["author_account"] == BOT_ACCOUNT
        assert human["author_account"] == "emp-999"
        assert bot["author_account"] != human["author_account"]

    def test_해결_등급이_함께_보관된다(self, tmp_path) -> None:
        # D8 — ingest 자격이 이 값에서 갈린다.
        conn = _conn(tmp_path)
        QnaCollector(self.parent, conn).collect()
        store = QnaStore(conn)

        resolved = store.resolution_of("Q-2")
        assert resolved["resolved"] == 1
        assert resolved["grade"] == ResolutionGrade.EXPLICIT
        assert resolved["method"] == ResolutionMethod.USER_MARKED

        unresolved = store.resolution_of("Q-3")
        assert unresolved["resolved"] == 0
        assert unresolved["grade"] is None

    def test_미해결_봇_답변도_적재된다(self, tmp_path) -> None:
        # **걸러내지 않는다.** 지식으로 삼지 않는 것과 기록하지 않는 것은 다르다 —
        # 여기서 버리면 통계와 FAQ 후보까지 함께 사라진다 (§5.3).
        conn = _conn(tmp_path)
        QnaCollector(self.parent, conn).collect()

        answers = QnaStore(conn).answers_for("Q-3")
        assert len(answers) == 1
        assert answers[0]["author_account"] == BOT_ACCOUNT

    def test_질문자_계정도_남는다(self, tmp_path) -> None:
        # Raw Layer 에는 남고, 지식·콘텐츠로는 넘어가지 않는다 (PO-3).
        conn = _conn(tmp_path)
        QnaCollector(self.parent, conn).collect()
        row = conn.execute("SELECT asker_account FROM raw_question WHERE id='Q-1'").fetchone()
        assert row["asker_account"] == "emp-100"


class TestIdempotence:
    """폴링은 같은 것을 몇 번이고 다시 가져온다 (NFR-7)."""

    def test_다시_돌려도_늘지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)

        collector.collect()
        second = collector.collect()

        assert second.new_questions == 0
        assert second.answers == 0
        assert conn.execute("SELECT COUNT(*) FROM raw_question").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM raw_answer").fetchone()[0] == 4

    def test_커서는_적재_뒤에_옮겨진다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()

        assert get_cursor(conn, QNA) is None  # None 이 곧 "전체 수집" 신호다
        report = QnaCollector(parent, conn).collect()

        cursor = get_cursor(conn, QNA)
        assert cursor == report.cursor
        # 마지막으로 본 질문의 생성 시각이다.
        newest = max(q.created_at for q in parent.list_questions())
        assert cursor == newest

    def test_커서_이후에_들어온_질문만_새로_센다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)
        collector.collect()

        parent._add_question("Q-6", "정산 마감일이 언제인가요?", "emp-105")
        report = collector.collect()

        assert report.new_questions == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_question").fetchone()[0] == 6


class TestTracking:
    """새 질문만 보면 §6.2 추적이 성립하지 않는다."""

    def test_저장된_질문에_새로_달린_후속을_잡는다(self, tmp_path) -> None:
        # 어제 들어온 질문에 오늘 후속이 달린다. 커서 이후의 질문만 보면 못 본다 —
        # 그러면 파이프라인 재실행 트리거(D9)가 영영 오지 않는다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)
        collector.collect()

        parent._followups.setdefault("Q-1", []).append(
            Followup(
                id="F-99",
                question_id="Q-1",
                body="아직도 반려되는데요?",
                author_account="emp-100",
                created_at=_now(),
            )
        )
        report = collector.collect()

        assert report.new_questions == 0
        assert report.followups == 1
        assert QnaStore(conn).followups_for("Q-1")[0]["id"] == "F-99"

    def test_나중에_눌린_해결_표시가_상향으로_잡힌다(self, tmp_path) -> None:
        # **이 순간에 봇 답변의 ingest 자격이 발생한다** (§5.3.2). 이것을 놓치면
        # 지식이 자라야 할 때 자라지 않는다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)
        collector.collect()
        assert QnaStore(conn).resolution_of("Q-3")["grade"] is None

        parent._resolutions["Q-3"] = Resolution(
            question_id="Q-3",
            resolved=True,
            method=ResolutionMethod.USER_MARKED,
            resolved_by="emp-102",
            resolved_at=_now(),
        )
        report = collector.collect()

        assert report.upgraded == 1
        assert QnaStore(conn).resolution_of("Q-3")["grade"] == ResolutionGrade.EXPLICIT

    def test_명시적으로_해결된_건은_다시_훑지_않는다(self, tmp_path) -> None:
        # 등급은 상향만 되고(§5.3.2) 명시가 종점이므로 더 볼 이유가 없다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)
        collector.collect()

        assert "Q-2" not in QnaStore(conn).unsettled_question_ids()
        assert "Q-3" in QnaStore(conn).unsettled_question_ids()


class TestGradeIsUpgradeOnly:
    """강등은 없다 — 한 번 사람이 확인한 사실은 시간이 지나도 확인된 사실이다."""

    def _explicit(self, store: QnaStore) -> None:
        store.record_resolution(
            Resolution(
                question_id="Q-2",
                resolved=True,
                method=ResolutionMethod.USER_MARKED,
            ),
            collected_at=_now(),
        )

    def test_명시를_미해결로_되돌릴_수_없다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        QnaCollector(parent, conn).collect()
        store = QnaStore(conn)
        self._explicit(store)

        with pytest.raises(GradeDowngradeRejected):
            store.record_resolution(
                Resolution(question_id="Q-2", resolved=False), collected_at=_now()
            )

    def test_상향은_된다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        QnaCollector(parent, conn).collect()
        store = QnaStore(conn)

        store.record_resolution(
            Resolution(question_id="Q-3", resolved=True), collected_at=_now()
        )
        assert store.resolution_of("Q-3")["grade"] == ResolutionGrade.IMPLICIT

        upgraded = store.record_resolution(
            Resolution(
                question_id="Q-3", resolved=True, method=ResolutionMethod.OPERATOR_CLOSED
            ),
            collected_at=_now(),
        )
        assert upgraded is True
        assert store.resolution_of("Q-3")["grade"] == ResolutionGrade.EXPLICIT

    def test_모_시스템이_해결을_취소해도_등급이_남는다(self, tmp_path) -> None:
        # 강등 시도가 수집 주기에서 터지지는 **않는다** — 명시적 해결분은 애초에
        # 다시 훑는 범위에서 빠지기 때문이다. 그래서 모 시스템 쪽에서 해결 표시가
        # 사라져도 등급은 그대로 남고, 수집은 아무 일 없이 계속된다.
        conn = _conn(tmp_path)
        parent = MockParentSystem()
        collector = QnaCollector(parent, conn)
        collector.collect()

        parent._resolutions["Q-2"] = Resolution(question_id="Q-2", resolved=False)
        parent._add_question("Q-7", "출장비 정산 항목이 궁금합니다.", "emp-106")
        report = collector.collect()

        assert report.new_questions == 1  # 나머지는 그대로 수집됐다
        assert QnaStore(conn).resolution_of("Q-2")["grade"] == ResolutionGrade.EXPLICIT


class TestAuthorAccountIsRequired:
    def test_작성자_계정이_없는_답변은_거부한다(self, tmp_path) -> None:
        # 빈 문자열은 NOT NULL 을 통과한다. 통과시키면 그 뒤로 봇/사람 판정이
        # 조용히 틀리므로 적재 자체를 막는다 (D7).
        conn = _conn(tmp_path)
        QnaCollector(MockParentSystem(), conn).collect()

        with pytest.raises(ValueError):
            QnaStore(conn).upsert_answer(
                Answer(
                    id="A-99",
                    question_id="Q-1",
                    body="계정 없는 답변",
                    author_account="",
                    created_at=_now(),
                ),
                collected_at=_now(),
            )


class TestWorkerWiring:
    """배치 루프가 실제로 QnA 를 가져오는가 (NFR-7)."""

    def _settings(self, tmp_path, **over):  # noqa: ANN001, ANN202
        from agentic_service_desk.config import Settings

        base = dict(
            parent_adapter="mock",
            operations_db=tmp_path / "ops.sqlite3",
            parent_repo_url="",
            # **작업 디렉터리에 기대지 않는다.** 기본 `knowledge_dir` 은 상대 경로라,
            # 개발자가 실제로 배치를 한 번 돌려 `var/knowledge` 가 생기면 Lint 가
            # 그것을 보고 돌아 이 시험의 "아무것도 하지 않는다"가 무너진다.
            knowledge_dir=tmp_path / "knowledge",
        )
        base.update(over)
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]

    def test_폴링_주기는_분_단위다(self, tmp_path) -> None:
        # NFR-7 — 주기가 곧 유입 감지 지연이고, 그것이 답변 지연에 그대로 더해진다.
        assert self._settings(tmp_path).poll_interval_seconds == 60

    def test_한_주기가_Raw_Layer_를_채운다(self, tmp_path) -> None:
        from agentic_service_desk.worker.runner import BatchRunner

        with pytest.warns(RuntimeWarning):  # mock 어댑터 경고 (ADR-008)
            BatchRunner(self._settings(tmp_path))._tick()

        conn = connect(tmp_path / "ops.sqlite3")
        assert conn.execute("SELECT COUNT(*) FROM raw_question").fetchone()[0] == 5
        assert get_cursor(conn, QNA) is not None

    def test_연동이_비어_있으면_아무것도_하지_않는다(self, tmp_path) -> None:
        # 기본은 실제 연동이고 주소가 비어 있다 — 조용히 빈 결과를 만들지 않는다.
        from agentic_service_desk.worker.runner import BatchRunner

        cfg = self._settings(tmp_path, parent_adapter="http", parent_api_base_url="")
        BatchRunner(cfg)._tick()
        assert not (tmp_path / "ops.sqlite3").exists()
