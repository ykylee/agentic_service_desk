---
description: Standard AI workflow session start — restore the current baseline from state.json + session_handoff.md + backlog and report the next candidate tasks.
---

<!-- standard-ai-workflow-kit: v1.6.0 -->

# /workflow-session-start

> Claude Code slash command. The *session-start* entry point of the standard AI workflow.

## Role

This command restores the *current baseline* from `ai-workflow/memory/active/`:

1. Read `state.json` — `latest_backlog_path` + `in_progress_items` + `recent_done_items`
2. Read `session_handoff.md` — what the previous session handed over
3. Read `work_backlog.md` — the anchor for the current task list
4. Read `PROJECT_PROFILE.md` — project metadata
5. Read `PURPOSE.md` if present — directional intent one-liner + body excerpt ≤200 tokens

## Usage

This work goes **through the tools** — hand-editing the documents silently breaks the parsing contract (canonical §11).

```bash
wk session-start --help
```

## Procedure

1. Read `ai-workflow/memory/active/<branch>/state.json` first and summarize the current baseline
2. Pick 3–7 candidate follow-up tasks from the anchors in `session_handoff.md` + `work_backlog.md`
3. Report, in Korean: a one-line summary, 3–5 next-task candidates, and the recommended next action
4. When the tool output carries `roadmap_context` (ADR-027 — the project has
   `ai-workflow/memory/active/roadmap/`), fold the current milestone, SDLC phase, and
   next WBS candidates into the report; no roadmap → skip silently
4. **No intermediate reasoning, repeated summaries, or self-explanation** — give the user the *conclusion* only

## Language and context rules

- User-facing reports are in Korean
- Code, commands, file paths, and configuration keys stay verbatim
- Keep only the *facts the next session actually needs* in the handoff / backlog, to minimize context buildup

## next step

After reporting the summary and candidates, once the user confirms:
- `/workflow-backlog-update` to register today's work
- or `/workflow-doc-sync` to sync affected documents

## Related documents

- `ai-workflow/memory/active/<branch>/state.json`
- `ai-workflow/memory/active/<branch>/sessions`
- `ai-workflow/memory/active/<branch>/backlog`
- `docs/PROJECT_PROFILE.md`
- (if present) `ai-workflow/memory/active/PURPOSE.md`
