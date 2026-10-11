# Project context

## 2026-10-11 — Model collaboration

- User requested automatic per-person branches and conflict-free merges into the shared main branch. Setup detects the authenticated GitHub login; no hardcoded roster is required.
- `tools/TEAM_WORKFLOW.md` is the source of truth for onboarding and collaboration. `tools/MODEL_SYNC.md` covers the Windows task and logs.
- The merge workflow accepts only the two model paths, validates the merged result, regenerates the manifest on main, and never force-pushes. Contributors do not update generated checksums.
- Files in ChatGPT Library are accessed through browser automation at `/space/d/...`; the Pages connector does not list these library folders.
- Local merge integration tests cover successful merge, conflict preservation, dangling references, and rejection of non-model changes. Validate deployment through the Model collaboration Actions run after pushing workflow updates.

## 2026-10-11 — Safe incoming updates

- Incoming main updates must preserve unsaved editor work. The watcher cannot inspect in-memory StarUML documents, so all incoming replacements wait until editors close; no forced save, close, or reload.
- `tools/receive_models.py` fast-forwards only when local commits are already included in main, validates incoming models, backs up external sources, and journals replacement before touching the checkout. Pending transactions suspend outgoing sync across restarts.
- Process detection fails closed and also waits for javaw.exe (Visual Paradigm's possible Java launcher). Tests use disposable repositories and simulated editor state; actual editor process detection is checked on the configured PC.
