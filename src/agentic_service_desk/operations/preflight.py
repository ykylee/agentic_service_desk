"""기동 전 확인 — **두 프로세스가 같은 것을 본다** (ADR-010).

온라인과 배치가 같은 SQLite 파일을 쓰므로(ADR-002) 스키마 확인도 한 벌이어야 한다.
따로 두면 한쪽만 고쳐지고, 그러면 **거부되지 않는 프로세스가 옛 스키마 위에서 돈다.**

여기가 확인하는 것은 둘이다 — **스키마**(ADR-010)와 **실운영 조합**(WBS-5.2.1).
둘 다 "지금도 결국 터지지만 터지는 자리가 나쁘다"는 같은 이유로 기동에 있다.
"""

from __future__ import annotations

import sys

from agentic_service_desk.operations import migrations
from agentic_service_desk.operations.schema import connect
from agentic_service_desk.pipeline.gating import PUBLISHING_STAGES

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
"""루프백으로 볼 주소. `0.0.0.0` 은 여기 없다 — 이름과 달리 모든 인터페이스다."""


def check_schema(cfg) -> bool:  # noqa: ANN001
    """스키마가 코드와 맞는가. **안 맞으면 기동하지 않는다** (ADR-010).

    지금도 결국 터지지만 터지는 자리가 배치 한복판이라, 그때까지 처리한 것과 못 한
    것이 섞이고 로그를 뒤져야 원인을 안다. 기동에서 드러내면 아무 일도 시작되지 않은
    상태에서 알게 된다.

    DB 파일이 없으면 통과시킨다 — 아직 아무것도 돌지 않은 것이고, 첫 연결이 현재
    스키마로 만든다.
    """
    if not cfg.operations_db.exists():
        return True
    conn = connect(cfg.operations_db)
    try:
        migrations.require_current(conn)
    except migrations.SchemaProblem as exc:
        print(f"거부 — {exc}", file=sys.stderr)
        return False
    finally:
        conn.close()
    return True


def _live(cfg) -> bool:  # noqa: ANN001
    """실제 결과가 나가는 구성인가.

    두 조건 중 **하나만 맞아도** 실운영으로 본다. 어댑터가 mock 이 아니면 읽는
    것이 진짜 질문이고, 게재 단계면 쓰는 것이 진짜 답변이다 — 어느 쪽이든
    "연습이니까"가 성립하지 않는다.
    """
    return cfg.parent_adapter != "mock" or cfg.stage in PUBLISHING_STAGES


def check_live_exposure(cfg, *, binds_socket: bool = False) -> bool:  # noqa: ANN001
    """실운영 구성에서 위험한 조합이면 **기동하지 않는다** (WBS-5.2.1).

    이 검사가 기동에 있는 이유는 ADR-010 과 같다 — 나중에 드러나면 그때는 이미
    무언가 나간 뒤다. 다만 스키마와 달리 **여기서 막는 것은 사람의 실수**이고,
    그 실수는 조용하다: 시뮬레이션 선언을 켠 채 진짜 저장소를 넣어도 아무것도
    터지지 않고, 인증 없는 화면이 사설망에 열려도 아무 소리가 나지 않는다.

    막는 조합은 둘이다.

    **(1) 시뮬레이션 선언 + 실운영.** `simulated_source` 는 "이 저장소는 모
    시스템이 아니다"라는 선언이고, 그 선언이 NFR-1 의 소스 조건을 푼다. 실제
    모 시스템을 붙이는 날 이 줄이 남아 있으면 **조건이 풀린 채 원격 LLM 이
    열린다** — 정확히 이 정책이 막으려던 조합이다. 코드는 붙은 저장소가 모
    시스템인지 알 수 없지만(그것은 사람만 안다), **선언과 구성이 서로
    모순된다는 것**은 알 수 있다.

    **(2) 인증 없는 화면 + 루프백 밖 + 실운영.** 이 대시보드에는 인증이 없고
    승인·게재·모순 해결·국면 전진을 **POST 로 실행한다**. S0 + mock 에서는
    눌러도 나가지 않지만 실운영에서는 붙을 수 있는 사람이 곧 누를 수 있는
    사람이다. `binds_socket` 로 나눈 이유는 **배치는 소켓을 열지 않기**
    때문이다 — 같은 설정을 보고 배치까지 거부하면 고칠 이유가 없는 것을
    고치게 만든다.

    통과 조건을 넓게 잡지 않은 것이 요점이다. 인증이 생기면 (2)는 그 사실을
    보고 풀려야 하며, 그때 이 함수가 고쳐질 자리다.
    """
    if not _live(cfg):
        return True

    why = "어댑터가 mock 이 아니다" if cfg.parent_adapter != "mock" else f"게재 단계다({cfg.stage})"
    problems: list[str] = []
    if cfg.simulated_source:
        problems.append(
            "`ASD_SIMULATED_SOURCE` 가 켜져 있다 — 이 선언은 '붙은 저장소가 모 "
            "시스템이 아니다'라는 뜻이라 NFR-1 의 소스 조건을 푼다. 실운영이면 "
            "지운다"
        )
    if binds_socket and cfg.web_host not in LOOPBACK:
        problems.append(
            f"대시보드가 루프백 밖에 열린다({cfg.web_host}:{cfg.web_port}) — "
            "이 화면에는 인증이 없고 승인·게재를 POST 로 실행한다. "
            "`ASD_WEB_HOST` 를 지우면 루프백으로 돌아간다"
        )
    if not problems:
        return True

    print(f"거부 — 실운영 구성이다({why}). 다음을 고치기 전에는 기동하지 않는다:", file=sys.stderr)
    for problem in problems:
        print(f"  · {problem}", file=sys.stderr)
    return False
