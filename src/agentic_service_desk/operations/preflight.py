"""기동 전 확인 — **두 프로세스가 같은 것을 본다** (ADR-010).

온라인과 배치가 같은 SQLite 파일을 쓰므로(ADR-002) 스키마 확인도 한 벌이어야 한다.
따로 두면 한쪽만 고쳐지고, 그러면 **거부되지 않는 프로세스가 옛 스키마 위에서 돈다.**
"""

from __future__ import annotations

import sys

from agentic_service_desk.operations import migrations
from agentic_service_desk.operations.schema import connect


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
