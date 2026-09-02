---
description: Standard AI workflow session end — update the handoff and backlog, regenerate state.json, and leave the state so the next session resumes directly.
---

<!-- standard-ai-workflow-kit: v1.9.0 -->

# /workflow-session-end

> Claude Code slash command. The *session-end* entry point of the standard AI workflow.

## Role

Close the session, leaving the state so the next session can pick it up directly.

## Order

Close a session in the order **update memory → commit → push**. Do not split the memory update into a separate turn after the commit, so that pushed commits always carry the memory update with them (collaboration consistency).

## Procedure

1. Update `session_handoff.md` — current baseline, in-progress / blocked / recently-done lists.
2. Bring the task statuses in today's backlog in line with the actual results
   (`planned` / `in_progress` / `blocked` / `done`).
3. **Regenerate** `state.json` — never hand-edit it (see the parsing contract below).
4. Judge memory_index promotion candidates once (advisory, writes nothing).
5. Make sure the updates from 1–4 land in the **same commit**, then push.

## Usage

This work goes **through the tools** — hand-editing the documents silently breaks the parsing contract (canonical §11).

```bash
wk refresh-state --help
```

If the CLI is missing, do not skip silently — report the installation guidance and stop
(`INSTALLATION_AND_USAGE.md` §3). A hand-written `state.json` that was never regenerated
diverges from its input documents.

## Language and context rules

- User-facing reports are in Korean
- Code, commands, file paths, and configuration keys stay verbatim
- Keep only the *facts the next session actually needs* in the handoff / backlog

## Related documents

- `ai-workflow/memory/active/<branch>/session_handoff.md`
- `ai-workflow/memory/active/<branch>/backlog`
- `ai-workflow/memory/active/<branch>/state.json`
