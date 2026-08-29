---
description: Standard AI workflow backlog update — register/update a task in today's backlog and warn about scope creep when it overlaps PURPOSE.md's excluded areas.
---

<!-- standard-ai-workflow-kit: v1.7.0 -->

# /workflow-backlog-update

> Claude Code slash command. The *backlog-update* entry point of the standard AI workflow.

## Role

Register or update today's work item in `ai-workflow/memory/active/<branch>/backlog/<YYYY-MM-DD>.md`.

## Usage

This work goes **through the tools** — hand-editing the documents silently breaks the parsing contract (canonical §11).

```bash
wk backlog-update --help
```

## Procedure

1. Check the index anchor in `ai-workflow/memory/active/<branch>/backlog`
2. Today's `backlog/YYYY-MM-DD.md` file:
   - create it if absent
   - append to the existing entries if present
3. **in-scope check** (match against PURPOSE.md §3 Research Scope *excluded areas*):
   - `task_brief` + `affected_documents` vs excluded areas, by substring / first-2-token match
   - on a match, emit one `scope_creep_warnings` line (hard warning)
4. Task status: one of `planned` / `in_progress` / `blocked` / `done`
5. State priority, owner, and acceptance criteria
6. **roadmap gate** (ADR-027 §6) — when `ai-workflow/memory/active/roadmap/` exists,
   creating a task requires `--wbs M-NNN/WBS-N.N`; off-roadmap work is declared with
   `--wbs exempt --wbs-exempt-reason "<why>"`. Projects without a roadmap are unaffected.

## When PURPOSE.md is absent

`scope_creep_warnings = []` (graceful skip). No body reference is possible — advisory only.

## Read next

- `ai-workflow/memory/active/<branch>/backlog`
- (if present) `ai-workflow/memory/active/PURPOSE.md`
- The documents that will be affected

## Language rules

- Work reports, status summaries, update text = Korean
- Code, file paths, external product names = verbatim
