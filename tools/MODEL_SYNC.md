# Automatic model save tracking

On the configured Windows PC, the scheduled task **Integration2 Model Save Watcher** starts at sign-in and runs without a console window. It checks the source files every two seconds and waits for six seconds without a size or modification-time change before syncing.

| Application | Watched source | Repository copy |
| --- | --- | --- |
| StarUML | `D:\Thomas\integration\Machine VIsion.mdj` | `models/machine-vision/Machine VIsion.mdj` |
| Visual Paradigm | `C:\Users\kiril\Documents\VPProjects\Machine Vision Lab.vpp` | `models/machine-vision/Machine Vision Lab.vpp` |

Save to these exact source paths. A **Save As** to another location is not monitored. The applications do not have to remain open after saving. Monitoring runs while this Windows user is signed in and the computer is awake; it checks again after sign-in or waking.

Each changed, valid file is copied into the repository, its SHA-256 entry in `MANIFEST.csv` is updated, and a local commit is created. Unchanged saves do not create commits. Several rapid saves may be combined into one snapshot. Original source files are never modified by the watcher.

**Automatic push is disabled by default on this PC.** Publish local commits when ready with `git push` from the repository. The watcher is installed locally; cloning this repository does not install it on teammates' computers.

## Conflict handling

The watcher waits if a model or the manifest has uncommitted edits, if a Git operation is in progress, or if the active branch is not `main`. It does not overwrite those edits. Resolve or commit the changes to resume. An invalid or locked source file is retried later. A failed commit leaves the copied model and manifest available for review; commit or resolve them manually before further changes can sync.

StarUML saves are checked as JSON projects. Visual Paradigm saves are checked as SQLite project databases. These checks detect incomplete or corrupt files, but do not assess diagram meaning.

## Local configuration and status

Local settings are stored in `.git/model-watcher/config.json`, outside tracked project files. Restart the task after changing these settings.

- `.git/model-watcher/status.json`: last check, process ID, and current errors.
- `.git/model-watcher/watcher.log`: rotating activity and error log.

Use PowerShell to manage the task:

```powershell
Get-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
Stop-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
Start-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
```

To disable automatic startup as well as stop the current watcher:

```powershell
Stop-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
Disable-ScheduledTask -TaskName 'Integration2 Model Save Watcher'
```

For a one-time diagnostic, stop the task first, then run:

```powershell
python tools/watch_models.py --config .git/model-watcher/config.json --once
```

The script uses Python's standard library and Git. The current task uses `C:\Program Files\Python314\pythonw.exe`; update the scheduled task if Python or the repository is moved.
