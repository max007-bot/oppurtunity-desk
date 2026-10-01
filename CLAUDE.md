# CLAUDE.md

Claude Code does not automatically load another tool's instruction file, so read these
explicitly, in this order, before editing anything:

1. **`AGENTS.md`** — the shared repository instructions, the layout, and the rules that are
   easy to break by accident. Treat it as binding.
2. **`docs/BUILD_MANUAL.md`** — the implementation authority. When this file and the manual
   disagree, the manual wins.
3. **`docs/STATUS.md`** — what is actually built, what was tested, and what is deliberately
   not implemented. Read it before resuming work; update it before finishing.

`docs/OUTBOUND_PLAYBOOK.md` is commercial background. It is **not** a licence to ingest every
source it names.

## Before you edit

- Inspect the current files rather than assuming the state described in any chat history.
- Use one assistant as the active editor at a time.

## Before you claim anything is done

- `.\.venv\Scripts\python.exe -m pytest` passes.
- No required workflow is still a button that does nothing.
- `docs/STATUS.md` reflects reality, including the limitations.

## Things not to do in this repository

- Do not add a live connector, a scheduler, an automation service, or outbound messaging.
- Do not import the saved marketplace HTML in the parent folder as a substitute for an
  approved feed.
- Do not soften the language in the UI or the docs to make the prototype sound more capable
  than it is. The honesty of the output is a feature, and tests assert it.
