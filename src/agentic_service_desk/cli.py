"""운영 명령.

    sync-harness  pi 하네스 설정 생성 (ADR-009). 배포 시 `.env` 를 바꾸고 이것을
                  돌리면 **애플리케이션과 pi 가 함께** 움직인다
    verify-edit   지식베이스의 사람 편집 세 조건 검사 (FR-54). **지식 저장소의
                  `pre-commit` 훅이 부른다** — 사람이 직접 칠 일은 드물다
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from agentic_service_desk.config import load_settings
from agentic_service_desk.knowledge.repository import KnowledgeRepository
from agentic_service_desk.llm.harness import render_models_json, write_models_json
from agentic_service_desk.llm.policy import RemoteEndpointRejected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asd", description="Agentic Service Desk 운영 명령")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync-harness", help="pi 하네스 제공자 설정을 .env 에서 생성한다")
    p.add_argument("--dry-run", action="store_true", help="쓰지 않고 내용만 보여준다")

    v = sub.add_parser(
        "verify-edit", help="지식 편집의 세 조건을 검사한다 (pre-commit 훅이 부른다)"
    )
    v.add_argument("--root", help="지식 저장소 경로. 없으면 설정값을 쓴다")
    v.add_argument(
        "--message-file", required=True, help="커밋 메시지 파일. commit-msg 훅이 넘긴다"
    )

    args = parser.parse_args(argv)
    cfg = load_settings()

    if args.command == "verify-edit":
        repo = KnowledgeRepository(Path(args.root) if args.root else cfg.knowledge_dir)
        message = repo.read_commit_message(Path(args.message_file))
        problems = repo.verify_staged_edits(message)
        if not problems:
            return 0
        print("커밋을 멈춘다 — 사람 편집의 세 조건 (FR-54, §8.5.3)\n", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "\n작업 트리의 수정은 그대로 있다. 빠진 것을 채워 다시 커밋한다.",
            file=sys.stderr,
        )
        return 1

    if args.command == "sync-harness":
        try:
            payload = render_models_json(cfg)
        except RemoteEndpointRejected as exc:
            print(f"거부: {exc}", file=sys.stderr)
            print(
                "\npi 는 애플리케이션의 NFR-1 정책을 지나지 않는다. "
                "설정을 만드는 이 시점이 유일한 검문소다 (ADR-009).",
                file=sys.stderr,
            )
            return 1
        except ValueError as exc:
            print(f"설정 부족: {exc}", file=sys.stderr)
            return 1

        if args.dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        target = write_models_json(cfg)
        print(f"생성했다: {target}")
        print(f"  baseUrl : {cfg.llm_base_url}")
        print(f"  model   : {cfg.llm_model}")
        print("  apiKey  : $ASD_LLM_API_KEY (파일에 키를 박지 않는다)")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
