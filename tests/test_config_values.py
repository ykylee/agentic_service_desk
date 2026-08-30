"""설정 **값** 배제 — FR-9 의 두 번째 집행 지점 (§2.2.2).

경로 필터가 잡지 못하는 길을 여기서 막는다. 지키는 것은 **지식과 현재 상태의
구분**이다: "한도는 부서 등급으로 결정된다"는 지식이고 "지금 한도는 5000 이다"는
상태다. 후자를 굳히면 굳는 순간 stale 이 된다.

가장 중요한 시험은 **값만 스쳐도 걸리지 않는다**는 것이다. 그것까지 막으면 멀쩡한
서술이 통째로 사라지고, 그러면 이 장치는 꺼지게 된다.
"""

from __future__ import annotations

from agentic_service_desk.ingest.config_values import declared_values, leaked_pairs

SOURCE = """
COST_LIMIT_PAPER_DEFAULT_KRW: int = 5_000  # 타입 주석이 붙은 실제 모양
COST_LIMIT_LIVE_DEFAULT_KRW = 3_000
MAX_RETRIES: Final[int] = 7
TIMEOUT_SECONDS: float | None = 12.5
RETRY_COUNT = 3
DEFAULT_REGION = "ap-northeast-2"

def enforce(usage):
    if usage > COST_LIMIT_PAPER_DEFAULT_KRW:
        downgrade()
"""


class TestDeclarations:
    def test_상수_선언을_모양으로_잡는다(self) -> None:
        found = declared_values(SOURCE)
        assert "COST_LIMIT_PAPER_DEFAULT_KRW" in found
        assert "DEFAULT_REGION" in found

    def test_밑줄_구분자를_뗀_모양도_함께_든다(self) -> None:
        # 선언은 `5_000` 인데 본문에는 `5000` 으로 실린다.
        assert {"5_000", "5000"} <= declared_values(SOURCE)["COST_LIMIT_PAPER_DEFAULT_KRW"]

    def test_따옴표를_벗긴_모양도_함께_든다(self) -> None:
        assert "ap-northeast-2" in declared_values(SOURCE)["DEFAULT_REGION"]

    def test_타입_주석이_붙은_대입도_잡는다(self) -> None:
        # **2026-08-30 실저장소에서 놓쳤던 모양이다.** `: int =` 를 건너뛰지 못해
        # 값이 그대로 지식이 됐다 — 모양만 보는 판정은 하나를 놓치면 조용하다.
        assert "5_000" in declared_values(SOURCE)["COST_LIMIT_PAPER_DEFAULT_KRW"]

    def test_제네릭_타입_주석도_건너뛴다(self) -> None:
        assert "7" in declared_values(SOURCE)["MAX_RETRIES"]

    def test_합집합_타입_주석도_건너뛴다(self) -> None:
        assert "12.5" in declared_values(SOURCE)["TIMEOUT_SECONDS"]

    def test_소문자_변수는_상수가_아니다(self) -> None:
        # 지역 변수 대입까지 잡으면 코드 전체가 설정 선언이 된다.
        assert declared_values("threshold = 42") == {}

    def test_한_낱말_대문자는_잡지_않는다(self) -> None:
        # `PORT = 8080` 같은 것도 상수지만, 밑줄 없는 대문자 한 낱말은 열거형
        # 상수나 타입 이름과 구별되지 않는다. 넉넉하게 잡되 여기서는 멈춘다.
        assert declared_values("STATUS = 1") == {}


class TestLeak:
    def test_이름과_값이_같은_줄에_있으면_걸린다(self) -> None:
        body = "- `COST_LIMIT_PAPER_DEFAULT_KRW`: `5_000`"
        assert leaked_pairs(body, declared_values(SOURCE)) == [
            "COST_LIMIT_PAPER_DEFAULT_KRW=5_000"
        ]

    def test_라이브에서_샜던_본문이_걸린다(self) -> None:
        # 2026-08-30 실저장소 검증에서 실제로 지식이 된 문장이다. 회귀 방지.
        body = (
            "§23 결정에 따라 `COST_LIMIT_PAPER_DEFAULT_KRW = 5_000`, "
            "`COST_LIMIT_LIVE_DEFAULT_KRW = 3_000` 이 운영 기본값이다."
        )
        leaks = leaked_pairs(body, declared_values(SOURCE))
        assert "COST_LIMIT_PAPER_DEFAULT_KRW=5_000" in leaks
        assert "COST_LIMIT_LIVE_DEFAULT_KRW=3_000" in leaks

    def test_선언과_다른_표기로_적어도_걸린다(self) -> None:
        body = "COST_LIMIT_PAPER_DEFAULT_KRW 의 기본값은 5000 이다."
        assert leaked_pairs(body, declared_values(SOURCE))

    def test_이름만_말하는_서술은_걸리지_않는다(self) -> None:
        # **이것이 이 판정의 요점이다.** 규칙을 설명하는 지식은 상수 이름을 부른다 —
        # 그것까지 막으면 코드를 설명하는 항목이 통째로 사라진다.
        body = "사용량이 COST_LIMIT_PAPER_DEFAULT_KRW 를 넘으면 강제로 낮춘다."
        assert leaked_pairs(body, declared_values(SOURCE)) == []

    def test_값만_스치는_서술은_걸리지_않는다(self) -> None:
        body = "재시도는 3 회까지 하고 그 뒤에는 포기한다."
        assert leaked_pairs(body, declared_values(SOURCE)) == []

    def test_줄이_다르면_걸리지_않는다(self) -> None:
        # 같은 줄에 나란히 적는 것이 곧 현재 상태를 옮겨 적는 행위다.
        body = "COST_LIMIT_PAPER_DEFAULT_KRW 가 한도를 정한다.\n일반적으로 5_000 단위로 잡는다."
        assert leaked_pairs(body, declared_values(SOURCE)) == []

    def test_선언이_없으면_아무것도_걸리지_않는다(self) -> None:
        assert leaked_pairs("COST_LIMIT_PAPER_DEFAULT_KRW: 5_000", {}) == []

    def test_같은_짝을_두_번_세지_않는다(self) -> None:
        body = "A: `COST_LIMIT_PAPER_DEFAULT_KRW`=`5_000`\nB: `COST_LIMIT_PAPER_DEFAULT_KRW`=`5_000`"
        assert len(leaked_pairs(body, declared_values(SOURCE))) == 1
