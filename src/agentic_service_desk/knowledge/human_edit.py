"""사람의 지식 편집 — 세 조건 (FR-54, D37, §8.5.3).

사람이 지식 항목을 직접 고칠 수 있다. 대시보드 안에 에디터를 만들지 않는다(§8.5.5) —
운영자가 곧 개발자이고, 개발자는 자기 에디터로 마크다운을 고치는 편이 빠르다.

대신 셋을 지킨다.

    1. **왜 고쳤는지를 남긴다** — 커밋 메시지가 그 자리다
    2. **무효화 조건을 갱신한다** — 무엇이 바뀌면 이 수정이 다시 틀려지는가
    3. **사람이 고쳤다는 표시를 남긴다** — 이것이 없으면 다음 ingest 가 자기가 쓴
       것으로 착각하고 그냥 덮는다 (§8.5.4)

**검사가 커밋 시점에 있는 이유.** 요구사항은 "세 가지 없이는 편집이 *반영되지
않는다*" 이다. 커밋을 막으면 그 말이 그대로 이뤄지면서 **아무것도 잃지 않는다** —
작업 트리의 수정은 그대로 남아 있고, 사람은 빠진 것을 채워 다시 커밋하면 된다.
나중에 걸러내는 방식은 이미 반영된 것을 되돌려야 해서 파괴적이다.

조건 1 이 커밋 메시지인 것도 여기서 값을 한다 — 검사할 자리와 조건이 있는 자리가
같다.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_service_desk.knowledge.item import KnowledgeItem

INGEST_COMMIT_PREFIX = "ingest:"
"""에이전트가 만든 커밋. 이 검사를 지나지 않는다.

우리 코드가 항목을 만들 때 불변식을 이미 지키고 있고, 애초에 **사람의 편집이
아니므로** 사람 편집의 조건을 물을 대상이 아니다.
"""

_WEAK_MESSAGES = frozenset({"수정", "고침", "update", "fix", "wip", "edit", ".", "변경"})
"""사유라고 볼 수 없는 커밋 메시지.

빈 메시지만 막으면 조건 1 은 `-m "수정"` 한 번으로 무력해진다. 그러면 **몇 달 뒤
왜 고쳤는지 아무도 모른다** — 그 답을 남기려고 둔 조건이다.
"""

MIN_REASON_CHARS = 10


@dataclass(frozen=True)
class EditVerdict:
    """검사 결과."""

    violations: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.violations

    def __bool__(self) -> bool:
        return self.accepted


def is_ingest_commit(message: str) -> bool:
    return message.strip().startswith(INGEST_COMMIT_PREFIX)


def verify_edit(
    *, after: KnowledgeItem, before: KnowledgeItem | None, commit_message: str
) -> EditVerdict:
    """이 편집이 세 조건을 지켰는가.

    `before` 가 없으면 사람이 **새 항목을 손으로 만든** 것이다. 그때는 무효화 조건이
    *갱신*될 수 없으므로 존재만 본다 — 파일을 읽는 시점에 이미 강제되지만(직렬화가
    거부한다), 여기서 한 번 더 보는 편이 메시지가 친절하다.
    """
    violations: list[str] = []

    reason = commit_message.strip()
    if len(reason) < MIN_REASON_CHARS or reason.lower() in _WEAK_MESSAGES:
        violations.append(
            f"조건 1 — 왜 고쳤는지를 커밋 메시지에 남긴다 (지금: {reason or '(비어 있음)'!r}). "
            f"{MIN_REASON_CHARS}자 이상으로, 몇 달 뒤에도 읽히게 쓴다"
        )

    if before is not None and after.invalidation == before.invalidation:
        violations.append(
            "조건 2 — 무효화 조건을 갱신한다. 내용이 바뀌었으면 "
            "**무엇이 바뀌면 이 수정이 다시 틀려지는지**도 바뀐다 (§6.5.3)"
        )

    if not after.edited_by_human:
        violations.append(
            "조건 3 — `edited_by_human: true` 를 남긴다. 이 표시가 없으면 "
            "다음 ingest 가 자기가 쓴 것으로 착각하고 그냥 덮는다 (§8.5.4)"
        )

    return EditVerdict(violations=tuple(violations))


def format_violations(path: str, verdict: EditVerdict) -> str:
    """사람이 읽을 거부 메시지."""
    lines = [f"{path} — 사람 편집의 세 조건을 지키지 않았다 (FR-54, §8.5.3)"]
    lines += [f"  · {v}" for v in verdict.violations]
    return "\n".join(lines)
