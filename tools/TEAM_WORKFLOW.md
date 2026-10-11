# StarUML team collaboration

## How it works

1. One-time setup detects your authenticated GitHub username and creates `contributors/<username>`.
2. Save your working StarUML model. The Windows watcher waits for the save to settle, validates the file, and commits it on your contributor branch.
3. The watcher pushes your branch automatically, normally within one minute.
4. [Model collaboration on GitHub Actions](https://github.com/Kirichovka/integration2/actions/workflows/model-collaboration.yml) checks all contributor branches. It merges model-only changes into `main` when Git can merge them cleanly and the resulting models are valid.
5. The automation regenerates model checksums in `MANIFEST.csv` on `main`. Contributor branches intentionally do not update the manifest, avoiding checksum conflicts between people.

This is a Git-based companion for StarUML, not an extension installed inside StarUML. Each person keeps an independent working file and a personal Git branch. It does not provide simultaneous editing inside the application.

## One-time Windows setup

Prerequisites: Python 3, Git with Git Credential Manager signed into GitHub, and collaborator **write** access to this repository. The setup discovers your username from GitHub; it never stores your token in the repository or configuration.

Clone the repository and run the setup from PowerShell:

```powershell
git clone https://github.com/Kirichovka/integration2.git
cd integration2
.\tools\setup_contributor.ps1 -StarUmlSource 'D:\My Models\Machine VIsion.mdj'
```

Replace the example path with your working model outside the repository. To watch Visual Paradigm too:

```powershell
.\tools\setup_contributor.ps1 -StarUmlSource 'D:\My Models\Machine VIsion.mdj' -VisualParadigmSource 'D:\My Models\Machine Vision Lab.vpp'
```

Start with copies of the current models from `main`. Save to the configured paths; **Save As** to another path is not monitored. The setup creates or reuses your branch, enables automatic pushes, and installs a background task that starts at Windows sign-in. It requires a clean checkout and stops if your branch needs manual reconciliation with GitHub.

Public read access does not grant write access. The repository owner must invite teammates as collaborators before they run setup. No username list is needed by the watcher itself.

## Conflicts and validation

- Clean Git merges are additionally checked for valid StarUML Project JSON, unique object IDs, and references pointing to existing objects.
- Visual Paradigm databases receive an SQLite integrity check. Simultaneous edits to the binary `.vpp` normally require manual resolution.
- A conflict, invalid result, deleted model, or change outside the two configured model files stays on the contributor branch. The Actions run reports the reason and does not merge that branch.
- A clean textual merge does not prove that two design decisions agree. Review the combined diagram in StarUML after significant changes.
- GitHub merges are serialized. The bot never force-pushes and never automatically chooses one side of a conflict.

For a conflict, pause the watcher, fetch `main`, merge it into your contributor branch, resolve the model in the application, commit, and push. GitHub will retry. Never resolve a model conflict by blindly accepting one entire side.

## Receiving teammates' changes

The watcher sends your saves; it does not overwrite a model currently open in StarUML with other people's edits. After your branch has merged, stop the watcher and close the model, ensure your local changes are committed and pushed, then run:

```powershell
Stop-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
git fetch origin
git merge origin/main
```

If the merge succeeds, back up your external working file and copy the updated model from `models/machine-vision/` to that working path. Reopen it in StarUML and restart the watcher. Resolve any conflicts before copying or restarting. Do not switch the working checkout to `main`; the watcher is bound to your contributor branch.

## Operations

See [local watcher controls and diagnostics](MODEL_SYNC.md). Configuration is local at `.git/model-watcher/config.json`; logs and status are alongside it. Team mode uses `push: true`, `branch: contributors/<username>`, and `update_manifest: false`.

Run the GitHub workflow manually from its Actions page to retry pending branches. Each user push to a contributor branch, and each human push to `main`, also starts it. A run scans all branches so queued runs do not lose another contributor's work. Bot merges are processed in the same run because GitHub does not recursively trigger workflows for pushes made with `GITHUB_TOKEN` ([GitHub documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)).

This workflow is not branch protection. Administrators and collaborators with direct main access can still bypass it; routine model saves use contributor branches.
