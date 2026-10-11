"""Receive main only while editors are closed, with durable recovery and backups."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from merge_models import MODELS, validate

EDITOR_NAMES = {'staruml.exe', 'visual paradigm.exe', 'visualparadigm.exe', 'javaw.exe'}


def digest(data):
    return hashlib.sha256(data).hexdigest().upper()


def command(config, *args, check=True, binary=False):
    result = subprocess.run(
        [config['git'], '-C', config['repository'], *args],
        capture_output=True, timeout=90,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace').strip())
    if not check:
        return result.returncode
    return result.stdout if binary else result.stdout.decode().strip()


def editors_running():
    # Fail closed if process discovery fails. Include javaw for Java-based VP launchers.
    result = subprocess.run(['tasklist.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, timeout=15,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Cannot inspect running editors; incoming updates paused')
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if not rows or any(len(row) < 2 for row in rows):
        raise RuntimeError('Unexpected process listing; incoming updates paused')
    return sorted({row[0] for row in rows if row[0].lower() in EDITOR_NAMES})


def stable_read(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise RuntimeError(f'File changed while reading: {path.name}')
    return data


def atomic_write(path, data):
    handle, temporary = tempfile.mkstemp(prefix='.model-sync-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def state_path(config):
    return Path(config['repository']) / '.git' / 'model-watcher'


def has_pending(config):
    return (state_path(config) / 'incoming.json').exists()


def check_checkout(config):
    if command(config, 'branch', '--show-current') != config['branch']:
        raise RuntimeError('Incoming updates paused: contributor branch is not active')
    if command(config, 'status', '--porcelain'):
        raise RuntimeError('Incoming updates paused: repository has local changes')
    git_dir = Path(command(config, 'rev-parse', '--absolute-git-dir'))
    if any((git_dir / name).exists() for name in
           ['index.lock', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'rebase-merge', 'rebase-apply']):
        raise RuntimeError('Incoming updates paused: Git operation in progress')


def finish_pending(config, editor_check=editors_running):
    state = state_path(config)
    journal_path = state / 'incoming.json'
    journal = json.loads(journal_path.read_text(encoding='utf-8'))
    running = editor_check()
    if running:
        return 'Waiting for editors to close: ' + ', '.join(running)
    check_checkout(config)
    # Check all sources before changing any of them; unknown edits are never discarded.
    for item in journal['models']:
        current = digest(stable_read(Path(item['source'])))
        if current not in (item['before'], item['after']):
            raise RuntimeError(f"Incoming update paused: {item['name']} changed since backup; preserve and reconcile it manually")
    target = journal['target']
    head = command(config, 'rev-parse', 'HEAD')
    if head != target:
        if command(config, 'merge-base', '--is-ancestor', head, target, check=False):
            raise RuntimeError('Incoming update paused: branch diverged during update')
        if editor_check():
            return 'Waiting for editors to close'
        command(config, 'merge', '--ff-only', target)
    for item in journal['models']:
        source = Path(item['source'])
        if digest(stable_read(source)) == item['after']:
            continue
        if editor_check():
            return 'Waiting for editors to close; update safely queued'
        data = (Path(config['repository']) / item['destination']).read_bytes()
        if digest(data) != item['after']:
            raise RuntimeError('Repository model changed during incoming update')
        # Recheck immediately before replacement, including any newly saved local work.
        if digest(stable_read(source)) != item['before']:
            raise RuntimeError('Working file changed during incoming update; update paused')
        atomic_write(source, data)
    journal_path.unlink()
    return f"Updated from main ({target[:8]}); previous files kept in {journal['backup']}"


def receive(config, editor_check=editors_running):
    if has_pending(config):
        return finish_pending(config, editor_check)
    command(config, 'fetch', 'origin', 'main')
    check_checkout(config)
    target = command(config, 'rev-parse', 'origin/main')
    head = command(config, 'rev-parse', 'HEAD')
    if command(config, 'merge-base', '--is-ancestor', target, head, check=False) == 0:
        return 'Up to date with main'
    running = editor_check()
    if running:
        return 'Main update available; waiting for editors to close: ' + ', '.join(running)
    if command(config, 'merge-base', '--is-ancestor', head, target, check=False):
        return 'Waiting for contributor commits to merge into main; resolve conflicts if reported on GitHub'
    models = []
    for model in config['models']:
        source = Path(model['source'])
        before = stable_read(source)
        local = (Path(config['repository']) / model['destination']).read_bytes()
        if before != local:
            return f"Waiting for saved {model['name']} changes to be committed first"
        models.append((model, before))
    # Validate the exact incoming revision before the fast-forward or any source replacement.
    incoming = {}
    with tempfile.TemporaryDirectory(prefix='integration2-incoming-') as folder:
        root = Path(folder)
        for name in MODELS:
            mode = command(config, 'ls-tree', target, '--', name).split()[0]
            if mode != '100644':
                raise RuntimeError('Incoming model is not a regular data file')
            data = command(config, 'show', f'{target}:{name}', binary=True)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            incoming[name] = data
        validate(root)
    state = state_path(config)
    backup = state / 'backups' / (target[:12] + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    journal = {'target': target, 'backup': str(backup), 'models': []}
    for model, before in models:
        path = backup / Path(model['destination']).name
        path.write_bytes(before)
        journal['models'].append({**model, 'before': digest(before),
                                  'after': digest(incoming[model['destination']])})
    # Persist before modifying Git or sources. On restart, outgoing sync stays suspended.
    atomic_write(state / 'incoming.json', json.dumps(journal, indent=2).encode())
    return finish_pending(config, editor_check)
