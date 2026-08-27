<!-- standard-ai-workflow-kit: v1.6.0 -->

# Standard AI Workflow Kit

- Purpose: describe the bootstrap result so the `Agentic Service Desk` repository can adopt the standard AI workflow document set.
- Scope: where the shared core documents live, the project state document set, follow-up per adoption mode
- Audience: developer, operator, AI agent, project onboarding owner
- Status: draft
- Last updated: 2026-08-27
- Related: `docs/PROJECT_PROFILE.md`, `ai-workflow/memory/active/<branch>/state.json`, `ai-workflow/memory/active/<branch>/sessions`, `ai-workflow/memory/active/<branch>/backlog`

## 1. Adoption mode

- Selected adoption mode: `new`
- Summary:
- Generated the default document set for a new project.

## 2. Generated files

- [docs/PROJECT_PROFILE.md](../docs/PROJECT_PROFILE.md)
- [ai-workflow/memory/active/<branch>/state.json](./memory/active/state.json)
- [ai-workflow/memory/active/<branch>/sessions](./memory/active/session_handoff.md)
- [ai-workflow/memory/active/<branch>/backlog](./memory/active/work_backlog.md)
- [ai-workflow/memory/active/<branch>/backlog/2026-08-27.md](./memory/active/backlog/2026-08-27.md)


## 3. Core documents

- Core documents can be copied along with `--copy-core-docs`.

## 4. Harness overlays

- Generated overlay files for the `claude-code` harness

## 5. What to do right after adoption

1. Fill `PROJECT_PROFILE.md` with the project's real purpose, commands, and verification rules.
2. Update `state.json`, `session_handoff.md`, and today's backlog to match the work actually in progress.
3. In existing-project mode, check the inferred values in `repository_assessment.md` against the real repository rules and correct them.
4. If a harness was selected, review the generated overlay files against that harness's execution paths.
5. Decide how far to adopt the standard skills/MCP from the `core/` documents.

## 6. Language and context principles

- Write user-facing work reports, status summaries, and handoff/backlog updates in Korean by default.
- Keep code, commands, file paths, configuration keys, and external product names verbatim.
- Handle internal reasoning and intermediate classification however is most efficient, and give the user only the conclusion.
- Keep only the facts the next session needs in the handoff and backlog, so context does not pile up.

## 7. Configured project document paths

- Documentation home: `README.md`
- Operations docs: `ai-workflow/memory/active/`
- Backlog location: `ai-workflow/memory/active/backlog/`
- Session handoff: `ai-workflow/memory/active/session_handoff.md`
- Environment records: `ai-workflow/memory/active/environments/`

## Read next

- Project profile: [../docs/PROJECT_PROFILE.md](../docs/PROJECT_PROFILE.md)
- Quick state summary: [./memory/active/state.json](./memory/active/state.json)
- Session handoff: [./memory/active/session_handoff.md](./memory/active/session_handoff.md)
- Work backlog index: [./memory/active/work_backlog.md](./memory/active/work_backlog.md)
