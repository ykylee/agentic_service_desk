---
description: Standard AI workflow document sync — derive affected-document candidates from the changed files and propose wiki-index update points as advisory.
---

<!-- standard-ai-workflow-kit: v1.7.0 -->

# /workflow-doc-sync

> Claude Code slash command. The *doc-sync* entry point of the standard AI workflow.

## Role

After the work, identify the affected-document candidates and lay out the hub / index
update points under `ai-workflow/memory/active/`.

## Usage

This work goes **through the tools** — hand-editing the documents silently breaks the parsing contract (canonical §11).

```bash
wk doc-sync --help
```

## Procedure

1. Identify the current changed-file list and the affected-document candidates
2. Check the page catalog against the `ai-workflow/wiki/index.md` anchors
3. Emit *advisory* update points for the affected pages:
   - Candidate new concept / decision / pattern pages
   - Existing pages whose `last_touched` should be refreshed
4. When PURPOSE.md is absent: *advisory only* (no hard scope check)

## Output format

- The affected-document list (path + one-line summary)
- Recommended anchors / cross-references
- confidence (high / medium / low)

## Read next

- `ai-workflow/wiki/index.md`
- (if present) `ai-workflow/memory/active/PURPOSE.md`

## Language rules

- Update-point reports = Korean
- File paths, anchors, configuration keys = verbatim
