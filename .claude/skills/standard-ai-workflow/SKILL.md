---
name: standard-ai-workflow
description: The standard AI workflow entry point for this repository. Use it when starting or resuming a session, registering/updating a task in the backlog, syncing affected documents after a change, or leaving a handoff at session close.
---

<!-- standard-ai-workflow-kit: v1.6.0 -->

# Standard AI Workflow

- **Role**: the entry skill that covers session start, backlog update, document sync, and session close in one place.
- **Location**: `.claude/skills/standard-ai-workflow/SKILL.md`
- **Invocation**: the model selects it automatically when the situation matches the `description` above. To invoke it directly,
  `/workflow-session-start`, `/workflow-backlog-update`, `/workflow-doc-sync`, `/workflow-session-end` slash command.
- Last updated: 2026-08-27

## 1. Session start — always read these first

1. `ai-workflow/memory/active/<branch>/state.json` — the current baseline
2. `ai-workflow/memory/active/<branch>/sessions` — the previous session's handoff
3. `ai-workflow/memory/active/<branch>/backlog` — the work backlog index
4. `docs/PROJECT_PROFILE.md` — project metadata
5. (if present) `ai-workflow/memory/active/PURPOSE.md` — directional intent

After reading, report in Korean only: **a one-line baseline summary, 3–5 next-task
candidates, and the recommended next action.** No intermediate reasoning, repeated
summaries, or self-explanation.

If `state.json` or `PURPOSE.md` is absent, do not treat it as a failure — *skip gracefully*
and offer to scaffold it.

## 2. Backlog update

Register today's work in `ai-workflow/memory/active/<branch>/backlog/<YYYY-MM-DD>.md` and
`./tasks/<TASK-ID>.md`. Use only the four status values `planned` / `in_progress` /
`blocked` / `done`. If it overlaps an excluded area in `PURPOSE.md` §3, leave a one-line
scope-creep warning.

## 3. Document sync (advisory)

Derive affected-document candidates from the changed files and *recommend* update points
against the `ai-workflow/wiki/index.md` anchors. Never apply them automatically.

## 4. Session close

Close the session so the next one resumes directly: update `session_handoff.md`, bring
today's backlog task statuses in line with the actual results, **regenerate** `state.json`
(never hand-edit it), and judge memory_index promotion candidates once. All of it lands in
the **same commit** as the work it describes — see the close order below.

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

- User-facing reports, status summaries, and document text are in Korean.
- Code, commands, file paths, configuration keys, and external product names stay verbatim.
- Keep only the facts the next session needs in the handoff and backlog.
- `ai-workflow/` is the workflow meta layer. Do not include it in the default project code/document search scope.
