"""WBS-4.8.1 — 국면 판정과 전환 (FR-49 · NFR-8, §1.3.3).

국면은 검수 강도(FR-57)와 자동 승격 범위(§6.8.4-b)를 함께 정한다. 그 값이 지금까지는
**손으로 적은 설정 하나**였다. 여기서 지키는 것은 여섯.

    1. **국면의 SSOT 는 DB 다** — 설정은 처음 한 번의 씨앗이다
    2. **전진은 제안까지**다 — 임계가 없으면 제안하지 않고, 제안 없이는 올릴 수 없다
    3. **후퇴는 자동**이다 — 승인을 기다리지 않고 한 계단 내린다
    4. **역행이 전진보다 먼저**다 — 둘 다 성립하는 창에서 올리지 않는다
    5. **같은 증거로 두 번 내리지 않는다** — 기준선이 결정 시점에서 잘린다
    6. **표본이 얇으면 판정하지 않는다** (§1.3.1) — 국면이 잡음을 따라가지 않게
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentic_service_desk.config import Settings
from agentic_service_desk.operations import phase
from agentic_service_desk.operations.schema import connect, initialize
from agentic_service_desk.web.app import create_app


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    c = connect(tmp_path / "ops.sqlite3")
    initialize(c)
    return c


def _thresholds(tmp_path, body: str):  # noqa: ANN001, ANN202
    path = tmp_path / "phase.toml"
    path.write_text(body, encoding="utf-8")
    return phase.load_thresholds(path)


FULL = """
[advance.2]
coverage            = 0.5
explicit_resolution = 0.4
rejection           = 0.3
repetition          = 0.2

[advance.3]
coverage            = 0.6
explicit_resolution = 0.5
rejection           = 0.2
repetition          = 0.3
agreement           = 0.8
"""


def _observation(day: str, **values: float | None) -> phase.Observation:
    """관측 하나를 손으로 짓는다. **없는 축은 없는 채로 둔다.**"""
    readings = {}
    for metric in phase.METRICS:
        if metric in values and values[metric] is not None:
            readings[metric] = phase.Reading(metric, values[metric], denominator=50)
        else:
            readings[metric] = phase.Reading(
                metric, None, denominator=0, unavailable="표본이 얇다"
            )
    return phase.Observation(observed_on=day, window_days=30, readings=readings)


def _passing(day: str = "2026-08-29") -> phase.Observation:
    return _observation(
        day,
        coverage=0.6,
        explicit_resolution=0.5,
        rejection=0.1,
        repetition=0.3,
        stale=0.1,
        novelty=0.1,
        agreement=0.9,
    )


class TestSeed:
    """국면의 SSOT 를 설정에서 DB 로 옮긴다."""

    def test_설정은_처음_한_번의_씨앗이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert phase.current(conn, seed=2) == 2
        # 설정을 바꿔도 따라가지 않는다 — 그러지 않으면 시스템이 내린 국면을
        # 설정이 매 기동마다 되돌린다.
        assert phase.current(conn, seed=3) == 2
        conn.close()

    def test_씨앗도_이력에_남는다(self, tmp_path) -> None:
        """첫 전진의 `from_phase` 가 무엇이었는지 남아야 한다."""
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        [decision] = phase.history(conn)
        assert decision.decided_by == phase.SEED
        assert decision.from_phase is None
        assert decision.to_phase == 1
        conn.close()

    def test_모르는_국면은_받지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        assert phase.current(conn, seed=9) == phase.COLD_START
        conn.close()


class TestAdvanceIsProposalOnly:
    """전진은 **지표가 제안하고 운영자가 승인한다** (§1.3.3-c)."""

    def test_임계가_비어_있으면_제안하지_않는다(self, tmp_path) -> None:
        """**"조건 미달"이 아니라 "임계 미정"이다** (O8) — 다른 상태다."""
        judgment = phase.judge(
            _passing(), None, current=1, thresholds=phase.load_thresholds()
        )
        assert judgment.proposal is None
        assert "임계가 아직 정해지지 않았다" in judgment.undecidable

    def test_기본_선언의_전진_임계는_비어_있다(self) -> None:
        # 지어낸 숫자로 검수를 느슨하게 하지 않는다. 비어 있는 것이 결정이다.
        thresholds = phase.load_thresholds()
        assert not thresholds.advance[2].declared
        assert not thresholds.advance[3].declared

    def test_세_축이_함께_오르면_제안한다(self, tmp_path) -> None:
        judgment = phase.judge(
            _passing(), None, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal == 2
        assert not judgment.unmet

    def test_한_축이라도_미달이면_제안하지_않는다(self, tmp_path) -> None:
        """커버리지만 높고 반려율도 높다면 **2국면이 아니다** (§1.3.3-a)."""
        observation = _observation(
            "2026-08-29",
            coverage=0.9,
            explicit_resolution=0.5,
            rejection=0.9,  # 반려율은 상한이다
            repetition=0.3,
            stale=0.1,
            novelty=0.1,
        )
        judgment = phase.judge(
            observation, None, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal is None
        assert any("rejection" in u for u in judgment.unmet)
        assert not judgment.undecidable  # 잴 수 없는 것이 아니라 미달이다

    def test_제안만으로는_올라가지_않는다(self, tmp_path) -> None:
        """**승인 전에는 아무 일도 일어나지 않는다.**"""
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        judgment = phase.judge(
            _passing(), None, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal == 2
        assert phase.current(conn) == 1
        conn.close()

    def test_승인이_국면을_올리고_근거를_남긴다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        judgment = phase.judge(
            _passing(), None, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        decision = phase.advance(conn, to=2, judgment=judgment)
        assert phase.current(conn) == 2
        assert decision.decided_by == phase.OPERATOR
        assert "coverage" in decision.reason  # 어느 축이 어떻게 올랐는지
        conn.close()

    def test_제안되지_않은_전진은_거부된다(self, tmp_path) -> None:
        """버튼이 **국면 다이얼**이 되면 승인에 근거가 남지 않는다."""
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        judgment = phase.judge(
            _passing(), None, current=1, thresholds=phase.load_thresholds()
        )
        with pytest.raises(phase.NotProposed):
            phase.advance(conn, to=2, judgment=judgment)
        with pytest.raises(phase.NotProposed):
            phase.advance(conn, to=3, judgment=judgment)
        assert phase.current(conn) == 1
        conn.close()

    def test_2에서_3으로는_일치율_축이_붙는다(self, tmp_path) -> None:
        """2국면은 **자동 검수의 신뢰도를 측정하는 구간**이다 (§1.3.3-b)."""
        thresholds = _thresholds(tmp_path, FULL)
        low = _passing()
        low.readings[phase.AGREEMENT] = phase.Reading(phase.AGREEMENT, 0.5, denominator=50)
        assert phase.judge(low, None, current=2, thresholds=thresholds).proposal is None
        assert phase.judge(_passing(), None, current=2, thresholds=thresholds).proposal == 3

    def test_일치율이_없으면_3국면을_판정하지_않는다(self, tmp_path) -> None:
        # **잴 수 없는 것과 미달은 다르다** — 없으면 "미달"이 아니라 "판정 불가"다.
        observation = _passing()
        observation.readings[phase.AGREEMENT] = phase.Reading(
            phase.AGREEMENT, None, unavailable="표본이 얇다"
        )
        judgment = phase.judge(
            observation, None, current=2, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal is None
        assert "판정할 수 없는 축" in judgment.undecidable

    def test_3국면_위는_없다(self, tmp_path) -> None:
        judgment = phase.judge(
            _passing(), None, current=3, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal is None
        assert "올라갈 곳이 없다" in judgment.undecidable


class TestRegressionIsAutomatic:
    """후퇴는 **시스템이 내린다.** 안전한 방향을 지체할 이유가 없다."""

    def test_역행_임계에는_기본값이_있다(self) -> None:
        """비워 두면 후퇴가 영영 일어나지 않는다 — §0 이 꼽은 실패 방식이다."""
        rule = phase.load_thresholds().regression
        assert rule.coverage_drop > 0
        assert rule.stale_rise > 0
        assert rule.novelty_rise > 0
        assert rule.rejection_rise > 0

    @pytest.mark.parametrize(
        ("metric", "before", "after", "말"),
        [
            (phase.COVERAGE, 0.6, 0.3, "새 영역"),
            (phase.STALE, 0.1, 0.4, "대규모 코드 변경"),
            (phase.NOVELTY, 0.1, 0.5, "이용자 구성"),
            (phase.REJECTION, 0.1, 0.4, "지식이 낡았거나"),
        ],
    )
    def test_네_신호가_각각_국면을_내린다(self, tmp_path, metric, before, after, 말) -> None:  # noqa: ANN001
        baseline = _passing("2026-08-01")
        baseline.readings[metric] = phase.Reading(metric, before, denominator=50)
        now = _passing("2026-08-29")
        now.readings[metric] = phase.Reading(metric, after, denominator=50)

        judgment = phase.judge(
            now, baseline, current=3, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.regression == 2
        assert 말 in " ".join(judgment.signals)

    def test_역행이_전진보다_먼저다(self, tmp_path) -> None:
        """세 축은 올랐지만 stale 이 급등한 창 — **올리지 않는다.**"""
        baseline = _passing("2026-08-01")
        now = _passing("2026-08-29")
        now.readings[phase.STALE] = phase.Reading(phase.STALE, 0.5, denominator=50)

        judgment = phase.judge(
            now, baseline, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal is None
        assert judgment.regression is None  # 1국면 아래는 없다
        assert judgment.signals
        assert "더 내려갈 곳이 없다" in judgment.undecidable

    def test_한_계단씩_내린다(self, tmp_path) -> None:
        baseline = _passing("2026-08-01")
        now = _passing("2026-08-29")
        now.readings[phase.COVERAGE] = phase.Reading(phase.COVERAGE, 0.0, denominator=50)
        judgment = phase.judge(
            now, baseline, current=3, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.regression == 2

    def test_기준선이_없으면_신호도_없다(self, tmp_path) -> None:
        """견줄 것이 없는데 잡아내면 그것은 감지가 아니라 첫 관측에 대한 오판이다."""
        now = _passing()
        now.readings[phase.STALE] = phase.Reading(phase.STALE, 0.9, denominator=50)
        judgment = phase.judge(now, None, current=3, thresholds=_thresholds(tmp_path, FULL))
        assert judgment.regression is None
        assert not judgment.signals

    def test_후퇴는_승인을_기다리지_않는다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        phase.current(conn, seed=3)
        decision = phase.regress(conn, to=2, signals=("stale 비율 급등",))
        assert phase.current(conn) == 2
        assert decision.decided_by == phase.SYSTEM
        assert "역행 신호" in decision.reason
        conn.close()


class TestBaseline:
    """**같은 증거로 두 번 내리지 않는다.**"""

    def test_기준선은_마지막_결정_이후로_잘린다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        phase.current(conn, seed=3)
        for day in ("2026-08-01", "2026-08-10", "2026-08-29"):
            phase.save(conn, _passing(day))

        # 충분히 묵은 것 **중 가장 최근**이다. 가장 오래된 것을 잡으면 견주는
        # 간격이 시간이 갈수록 벌어져 "지난 기준선 대비"가 무엇을 뜻하는지 흐려진다.
        assert phase.baseline(conn, before="2026-08-29", lookback_days=14).observed_on == (
            "2026-08-10"
        )

        # 국면이 내려가면 그 시점이 새 바닥이 된다 — 8-01 은 더는 기준선이 아니다.
        phase.regress(conn, to=2, signals=("근거 확보율 급락",))
        assert phase.baseline(conn, before="2026-08-29", lookback_days=14) is None
        conn.close()

    def test_너무_젊은_관측은_기준선이_아니다(self, tmp_path) -> None:
        """어제와 오늘을 견주면 잡음이 신호가 된다."""
        conn = _conn(tmp_path)
        phase.current(conn, seed=3)
        phase.save(conn, _passing("2026-08-28"))
        phase.save(conn, _passing("2026-08-29"))
        assert phase.baseline(conn, before="2026-08-29", lookback_days=14) is None
        conn.close()


class TestThinSample:
    """§1.3.1 — 이 규모의 문의는 **일 단위로 소수**다."""

    def test_표본이_얇으면_값이_아니라_없음이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        _qna(conn, "q-1", opened="2026-08-20", answered=True)
        observation = phase.observe(
            conn, window_days=30, min_sample=10, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        reading = observation.get(phase.COVERAGE)
        assert reading.value is None
        assert "표본이 얇다" in reading.unavailable
        conn.close()

    def test_얇은_축이_있으면_전진을_판정하지_않는다(self, tmp_path) -> None:
        """**미달이 아니라 판정 불가**다 — 셋을 함께 보기로 한 결정이 그것이다."""
        observation = _observation("2026-08-29", coverage=0.9, explicit_resolution=0.9)
        judgment = phase.judge(
            observation, None, current=1, thresholds=_thresholds(tmp_path, FULL)
        )
        assert judgment.proposal is None
        assert "판정할 수 없는 축" in judgment.undecidable

    def test_얇은_축은_역행_신호도_되지_않는다(self, tmp_path) -> None:
        baseline = _passing("2026-08-01")
        now = _passing("2026-08-29")
        now.readings[phase.STALE] = phase.Reading(
            phase.STALE, None, unavailable="지식 항목이 없다"
        )
        judgment = phase.judge(
            now, baseline, current=3, thresholds=_thresholds(tmp_path, FULL)
        )
        assert not judgment.signals


class TestObserve:
    """세는 것 자체."""

    def test_커버리지는_초안까지_간_비율이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        for i in range(10):
            _qna(conn, f"q-{i}", opened="2026-08-20", answered=i < 6)
        observation = phase.observe(
            conn, window_days=30, min_sample=5, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        assert observation.get(phase.COVERAGE).value == pytest.approx(0.6)
        conn.close()

    def test_창_밖의_건은_세지_않는다(self, tmp_path) -> None:
        """누적으로 재면 **반년 전 콜드 스타트가 오늘의 커버리지를 끌어내린다.**"""
        conn = _conn(tmp_path)
        for i in range(10):
            _qna(conn, f"old-{i}", opened="2026-01-01", answered=False)
        for i in range(10):
            _qna(conn, f"new-{i}", opened="2026-08-20", answered=True)
        observation = phase.observe(
            conn, window_days=30, min_sample=5, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        assert observation.get(phase.COVERAGE).value == pytest.approx(1.0)
        conn.close()

    def test_명시적_해결률의_분모는_닫힌_건이다(self, tmp_path) -> None:
        """해결 표시는 답변보다 늦게 온다 — 열린 기준으로 재면 언제나 낮게 나온다."""
        conn = _conn(tmp_path)
        for i in range(10):
            _qna(
                conn,
                f"q-{i}",
                opened="2026-08-10",
                closed="2026-08-20" if i < 8 else None,
                grade="explicit" if i < 4 else "implicit",
            )
        observation = phase.observe(
            conn, window_days=30, min_sample=5, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        assert observation.get(phase.EXPLICIT).value == pytest.approx(0.5)  # 4 / 8
        conn.close()

    def test_반복성과_신규_유형은_같은_묶기에서_나온다(self, tmp_path) -> None:
        """다른 기준을 쓰면 같은 질문이 한쪽에선 반복이고 한쪽에선 새것이 된다."""
        conn = _conn(tmp_path)
        for i in range(6):
            _question(conn, f"old-{i}", "결재 한도는 어떻게 정해지나요", "2026-07-10")
        for i in range(3):
            _question(conn, f"new-{i}", "결재 한도가 어떻게 정해지는지 알고 싶습니다", "2026-08-20")
        for i in range(3):
            _question(conn, f"vpn-{i}", "사내 VPN 접속 오류가 계속 납니다", "2026-08-20")

        observation = phase.observe(
            conn, window_days=30, min_sample=5, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        # 여섯 건 모두 각자 묶음을 이뤘다 — 반복성은 1.0
        assert observation.get(phase.REPETITION).value == pytest.approx(1.0)
        # VPN 셋만 이전 기간에 없던 유형이다
        assert observation.get(phase.NOVELTY).value == pytest.approx(0.5)
        conn.close()

    def test_이전_기간이_없으면_신규_유형은_없음이다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        for i in range(6):
            _question(conn, f"q-{i}", "결재 한도는 어떻게 정해지나요", "2026-08-20")
        observation = phase.observe(
            conn, window_days=30, min_sample=5, now=datetime(2026, 8, 29, tzinfo=UTC)
        )
        assert observation.get(phase.NOVELTY).value is None
        assert "견줄 이전 기간이 없다" in observation.get(phase.NOVELTY).unavailable
        conn.close()


class TestStore:
    """관측의 보관."""

    def test_하루에_한_벌이고_다시_돌면_덮는다(self, tmp_path) -> None:
        """매번 쌓으면 추이의 간격이 배치 주기를 따라가 "지난달 대비"가 흐려진다."""
        conn = _conn(tmp_path)
        phase.save(conn, _passing("2026-08-29"))
        again = _passing("2026-08-29")
        again.readings[phase.COVERAGE] = phase.Reading(phase.COVERAGE, 0.99, denominator=50)
        phase.save(conn, again)

        trend = phase.trend(conn)
        assert len(trend) == 1
        assert trend[0].get(phase.COVERAGE).value == pytest.approx(0.99)
        conn.close()

    def test_없는_이유가_함께_남는다(self, tmp_path) -> None:
        """빈 값과 "아직 만들지 않았다"는 다르다 — 되읽어도 그 구분이 살아 있어야 한다."""
        conn = _conn(tmp_path)
        phase.save(conn, _observation("2026-08-29", coverage=0.5))
        [stored] = phase.trend(conn)
        assert stored.get(phase.AGREEMENT).value is None
        assert stored.get(phase.AGREEMENT).unavailable
        conn.close()


class TestDeclaration:
    """선언은 검사받는다 — 오타가 조용히 판정을 꺼뜨리지 않게."""

    def test_모르는_열쇠를_거부한다(self, tmp_path) -> None:
        with pytest.raises(phase.InvalidThresholds):
            _thresholds(tmp_path, "[advance.2]\ncoverge = 0.5\n")

    def test_일치율은_2국면_전진에_붙지_않는다(self, tmp_path) -> None:
        """1국면에는 자동 검수가 없어 견줄 판정이 없다 (§1.3.3-b)."""
        with pytest.raises(phase.InvalidThresholds):
            _thresholds(tmp_path, "[advance.2]\nagreement = 0.8\n")

    def test_모르는_절을_거부한다(self, tmp_path) -> None:
        with pytest.raises(phase.InvalidThresholds):
            _thresholds(tmp_path, "[advance.4]\ncoverage = 0.5\n")

    def test_일부만_채우면_판정하지_않는다(self, tmp_path) -> None:
        """채운 축만 보고 올라가면 **세 축을 함께 보기로 한 결정**이 무너진다."""
        thresholds = _thresholds(tmp_path, "[advance.2]\ncoverage = 0.5\n")
        assert not thresholds.advance[2].declared
        judgment = phase.judge(_passing(), None, current=1, thresholds=thresholds)
        assert judgment.proposal is None
        assert "임계가 아직 정해지지 않았다" in judgment.undecidable


class TestScreen:
    """현황 화면 — **전진 제안은 대기열이 아니라 알림이다** (§8.3)."""

    def test_임계가_없으면_화면이_그렇게_말한다(self, tmp_path) -> None:
        client = TestClient(create_app(_settings(tmp_path)))
        body = client.get("/status").text
        assert "아직 정해지지 않았다" in body
        assert "국면 상태" in body

    def test_제안이_뜨고_버튼이_그것을_승인한다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        phase.save(conn, _passing("2026-08-29"))
        conn.close()

        thresholds = tmp_path / "phase.toml"
        thresholds.write_text(FULL, encoding="utf-8")
        client = TestClient(
            create_app(_settings(tmp_path, phase_thresholds_path=thresholds))
        )
        assert "전진이 제안됐다" in client.get("/status").text

        client.post("/phase/advance", data={"to": 2}, follow_redirects=False)
        conn = _conn(tmp_path)
        assert phase.current(conn) == 2
        conn.close()

    def test_제안이_없으면_버튼이_거부된다(self, tmp_path) -> None:
        """화면을 거치지 않고 눌러도 국면은 올라가지 않는다."""
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        phase.save(conn, _passing("2026-08-29"))
        conn.close()

        client = TestClient(create_app(_settings(tmp_path)))  # 임계 미정
        client.post("/phase/advance", data={"to": 2}, follow_redirects=False)
        conn = _conn(tmp_path)
        assert phase.current(conn) == 1
        conn.close()

    def test_설정과_DB_가_어긋나면_말해_준다(self, tmp_path) -> None:
        conn = _conn(tmp_path)
        phase.current(conn, seed=1)
        phase.regress(conn, to=1, signals=("stale 비율 급등",))
        conn.close()
        client = TestClient(create_app(_settings(tmp_path, phase=3)))
        assert "SSOT 는 DB 다" in client.get("/status").text


# --- 자료 짓기 ---------------------------------------------------------------


def _settings(tmp_path, **over):  # noqa: ANN001, ANN202
    base = dict(
        operations_db=tmp_path / "ops.sqlite3",
        knowledge_dir=tmp_path / "knowledge",
        stage="S3",
    )
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def _qna(  # noqa: ANN202
    conn,  # noqa: ANN001
    qid: str,
    *,
    opened: str,
    closed: str | None = None,
    grade: str | None = None,
    answered: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO qna_item (id, parent_question_id, origin, state, resolution_grade, "
        "opened_at, closed_at) VALUES (?, ?, 'parent', '게재됨', ?, ?, ?)",
        (qid, f"Q-{qid}", grade, opened, closed),
    )
    if answered:
        conn.execute(
            "INSERT INTO answer_draft (id, qna_item_id, question, statements, grounding, "
            "unanswered, state, created_at) "
            "VALUES (?, ?, '물음', '[]', '[]', '[]', 'pending', ?)",
            (f"d-{qid}", qid, opened),
        )
    conn.commit()


def _question(conn, qid: str, text: str, created: str) -> None:  # noqa: ANN001
    """Raw Layer 의 질문 원문. **반복 탐지는 여기를 읽는다** (§7.2)."""
    conn.execute(
        "INSERT INTO raw_question (id, title, body, asker_account, created_at, collected_at) "
        "VALUES (?, '', ?, 'user', ?, ?)",
        (qid, text, created, created),
    )
    conn.commit()
