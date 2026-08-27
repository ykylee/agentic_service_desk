<!-- standard-ai-workflow-kit: v1.6.0 -->

# CLAUDE.md (Claude Code entry point)

- Purpose: the *directional intent* of the standard AI workflow, plus the entry rules Claude Code needs every session
- Scope: session restore, the order to consult workflow state docs, working principles, session close order
- Audience: Claude Code, repository maintainer, workflow designer
- Status: beta
- Last updated: 2026-08-27
- Related: `ai-workflow/memory/active/<branch>/state.json`, `docs/PROJECT_PROFILE.md`

## What this file is for

- **Role**: the entry-point document Claude Code *reads automatically at session start* in this repository.
- **Location**: `./CLAUDE.md` (or `./.claude/CLAUDE.md`) — both are read automatically.
- **Relationship to AGENTS.md**: Claude Code does *not* read `AGENTS.md` directly. If this
  project already has one, pull it in from `CLAUDE.md` with an `@AGENTS.md` import or a symlink:

  ```bash
  # import (add a single @AGENTS.md line inside CLAUDE.md)
  @AGENTS.md

  # or symlink (prefer the import for cross-platform setups)
  ln -s AGENTS.md CLAUDE.md
  ```

## Read these first

> `<branch>` is the current git branch name (`main` when this is not a git repository). Splitting per branch keeps concurrent work from overwriting itself.

- `ai-workflow/memory/active/<branch>/state.json`
- `ai-workflow/memory/active/<branch>/sessions`
- `ai-workflow/memory/active/<branch>/backlog`
- `docs/PROJECT_PROFILE.md`
- `ai-workflow/wiki/index.md` — R4 anchor based; load this first when an AI agent queries
- (if present) `ai-workflow/memory/active/PURPOSE.md` — directional intent one-liner + body excerpt

`ai-workflow/` is a meta layer for session restore and workflow state. Do not include it in
the default search scope when exploring project code or project documents — reference it only
when updating the workflow documents themselves or restoring the current session state.

## Entry slash commands (additive)

- `/workflow-session-start` — restore the `state.json` + `session_handoff.md` + `work_backlog.md` baseline
- `/workflow-backlog-update` — register/update a task + scope-creep warning
- `/workflow-doc-sync` — sync affected documents (advisory)
- `/workflow-session-end` — update handoff + backlog and regenerate `state.json` at session close

## Working Principles

<!-- generated-from: core/global_workflow_standard.md §1 · §3 · §8 · §11 — do not edit this block directly; edit the standard document and regenerate. -->

- Start every session by reading the current state summary documents first.
- Before starting work, briefly state its purpose, scope, expected deliverables, and affected documents.
- Record work in the state documents; track progress as exactly one of `planned`, `in_progress`, `blocked`, `done`.
- Never mark an unverified result as done.
- Before ending a session, summarize the current state so the next session can pick it up directly.
- Multiple agents may work together: sync with the remote before starting, check what other agents are doing, and pick work that does not overlap.
- Never decide irreversible actions alone — deleting or overwriting another agent's work requires confirmation from the user.
- Keep the shared standard thin; put project-specific differences in the project profile.

## Session Close Order

Close a session in the order **update memory → commit → push**. Do not split the memory update into a separate turn after the commit, so that pushed commits always carry the memory update with them (collaboration consistency).

- Update before closing: `state.json`, `session_handoff.md`, the latest backlog

## Memory Update Paths

- Restore session-start baseline: `wk session-start`
- Register / update a task: `wk backlog-update`
- Sync affected documents (advisory): `wk doc-sync`
- Regenerate state.json at session close: `wk refresh-state`
- Roll off handoff §1 baselines when over cap: `wk rollover-baselines`
- Propose memory_index promotion candidates at close (advisory, no write): `wk suggest-memory-entries`

- When the handoff's `in_progress` / `blocked` lists are empty, leave an **empty bullet `-`**. Prose there is parsed as a work item.
- Entries in the handoff's recently-completed list start with `TASK-` and never exceed 10.
- A backlog task's `status` is one of `planned` / `in_progress` / `blocked` / `done`.
- `state.json` is a **generated artifact** — never hand-edit it. The SSOT is `backlog/tasks/` plus `session_handoff.md`; regenerate with `wk refresh-state` at session close.
- Handoff §1 baseline lines have a cap. When it is exceeded, **move** the excess with `wk rollover-baselines` — never delete them by hand. That prose exists nowhere else, unlike the recently-done list whose SSOT is `backlog/tasks/`.
- `session_handoff.md` and the backlog are **inputs to the state.json generator** — writing outside the format silently corrupts state.json.

## Language and context principles

- Write user-facing work reports, status summaries, and document updates in Korean by default.
- Keep code, commands, file paths, configuration keys, and external product names verbatim.
- Handle internal reasoning and scratch classification however is most efficient, but give
  the user only the conclusion and the next action.
- Avoid long intermediate reasoning, repeated summaries, and unnecessary self-explanation.
- Keep only the facts the next session needs in the handoff and backlog, so context does not pile up.

## self-bootstrap (when PURPOSE.md / state.json are absent)

When `state.json` or `PURPOSE.md` is absent, the session-start skill *skips gracefully*.
When the user invokes `/workflow-session-start` (or on automatic read), it attempts a
*minimum-effort* baseline restore:

1. `ai-workflow/memory/active/<branch>/state.json` missing → offer to scaffold it
2. `PURPOSE.md` missing → 4-element placeholder + suggest a light `init` call
3. `work_backlog.md` missing → empty index + guidance for registering the first task

## Project run defaults

- **install**: TODO: 설치 명령 입력
- **run**: TODO: 로컬 실행 명령 입력
- **quick test**: TODO: 빠른 테스트 명령 입력
- **isolated test**: TODO: 격리 테스트 명령 입력
- **smoke check**: TODO: 실행 확인 명령 입력

These commands are inferred. Correct them to the project's real commands before committing.

## Read next

- `ai-workflow/README.md` (kit overview)
- `docs/PROJECT_PROFILE.md` (project metadata)
- `ai-workflow/memory/active/<branch>/sessions` (current session handoff)
- `harnesses/claude-code/apply_guide.md` (Claude Code apply procedure)
