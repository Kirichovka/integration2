"""Copy completed model saves into Git; run with --once for a single check."""
import argparse
from contextlib import closing
import csv
import hashlib
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import msvcrt
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from receive_models import has_pending, receive


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def git(config, *args):
    result = subprocess.run(
        [config['git'], '-C', config['repository'], *args],
        capture_output=True, text=True, timeout=90,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def sync(config, model):
    root = Path(config['repository'])
    source = Path(model['source'])
    relative = model['destination']
    destination = root / relative
    direct = source.resolve() == destination.resolve()
    before = source.stat()
    data = source.read_bytes()
    after = source.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        raise RuntimeError('Source changed during reading; retrying later')
    digest = sha(data)
    if direct and not git(config, 'status', '--porcelain', '--', relative):
        return False
    if not direct and digest == sha(destination.read_bytes()):
        return False
    if not direct and destination.with_name('.' + destination.name + '.lck').exists():
        raise RuntimeError('Close the repository model in Visual Paradigm before syncing')
    branch = config.get('branch', 'main')
    if git(config, 'branch', '--show-current') != branch:
        raise RuntimeError(f'Switch back to {branch} before syncing models')
    tracked = [relative]
    if config.get('update_manifest', True):
        tracked.append('MANIFEST.csv')
    git_dir = Path(git(config, 'rev-parse', '--absolute-git-dir'))
    if any((git_dir / name).exists() for name in
           ['index.lock', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'rebase-merge', 'rebase-apply']):
        raise RuntimeError('Git operation in progress; retrying later')
    if direct and git(config, 'diff', '--cached', '--name-only', '--', *tracked):
        raise RuntimeError('Model has manually staged edits; commit or unstage them before automatic tracking')
    if not direct and git(config, 'status', '--porcelain', '--', *tracked):
        raise RuntimeError('Model or MANIFEST.csv has local edits; resolve them before syncing')
    # Validate the complete saved file before replacing the repository copy.
    handle, temporary = tempfile.mkstemp(suffix=source.suffix, dir=git_dir)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
        if source.suffix.lower() == '.mdj':
            project = json.loads(data)
            if project.get('_type') != 'Project':
                raise ValueError('Not a StarUML project')
        elif source.suffix.lower() == '.vpp':
            with closing(sqlite3.connect(Path(temporary).as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Visual Paradigm database check failed')
                db.execute('SELECT ID FROM PROJECT_INFO').fetchone()
        if config.get('update_manifest', True):
            manifest = root / 'MANIFEST.csv'
            rows = list(csv.DictReader(manifest.read_text(encoding='utf-8-sig').splitlines()))
            matches = [row for row in rows if row['path'] == relative]
            if len(matches) != 1:
                raise ValueError('Expected exactly one model entry in MANIFEST.csv')
            matches[0].update(bytes=str(len(data)), sha256=digest)
            output = io.StringIO(newline='')
            writer = csv.DictWriter(output, fieldnames=['path', 'bytes', 'sha256', 'source'])
            writer.writeheader()
            writer.writerows(rows)
        if direct:
            if sha(source.read_bytes()) != digest:
                raise RuntimeError('Model changed during validation; retrying later')
            # The editor owns this file. Never replace it with an earlier read snapshot.
        else:
            os.replace(temporary, destination)
        if config.get('update_manifest', True):
            manifest.write_text(output.getvalue(), encoding='utf-8', newline='')
        git(config, 'add', '--', *tracked)
        # --only excludes any unrelated changes already staged by the user.
        git(config, 'commit', '--only', '-m', f"Update {model['name']} model after save",
            '--', *tracked)
        logging.info('Synced and committed %s (%s)', model['name'], digest)
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(config, once=False):
    state = Path(config['repository']) / '.git' / 'model-watcher'
    state.mkdir(exist_ok=True)
    handler = RotatingFileHandler(state / 'watcher.log', maxBytes=1_000_000, backupCount=2)
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(levelname)s %(message)s')
    lock = (state / 'watcher.lock').open('a+b')
    lock.seek(0)
    lock.write(b'1')
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit('A model watcher is already running')
    logging.info('Watcher started; automatic push=%s', config.get('push', False))
    observed = {}
    errors = {}
    last_push = 0
    last_receive = 0
    incoming_status = 'Not checked yet'
    try:
        while True:
            # A persisted incoming transaction always takes precedence over outgoing saves.
            for model in ([] if has_pending(config) else config['models']):
                key = model['source']
                try:
                    stat = Path(key).stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    previous, since = observed.get(key, (None, 0))
                    if signature != previous:
                        observed[key] = (signature, time.monotonic())
                        if not once:
                            continue
                    if once:
                        time.sleep(config.get('settle_seconds', 6))
                        stat = Path(key).stat()
                        if signature != (stat.st_size, stat.st_mtime_ns):
                            raise RuntimeError('Source still changing; retry later')
                    elif time.monotonic() - since < config.get('settle_seconds', 6):
                        continue
                    sync(config, model)
                    errors.pop(key, None)
                except Exception as error:
                    if errors.get(key) != str(error):
                        logging.error('%s: %s', model['name'], error)
                        errors[key] = str(error)
            if config.get('push', False) and not has_pending(config) and time.monotonic() - last_push >= 60:
                last_push = time.monotonic()
                try:
                    branch = config.get('branch', 'main')
                    if git(config, 'branch', '--show-current') != branch:
                        raise RuntimeError(f'Expected active branch {branch}')
                    git(config, 'push', 'origin', f'HEAD:refs/heads/{branch}')
                    errors.pop('push', None)
                except Exception as error:
                    if errors.get('push') != str(error):
                        logging.error('Push failed; will retry: %s', error)
                        errors['push'] = str(error)
            if (config.get('receive_main', False) or has_pending(config)) and (
                    once or has_pending(config) or time.monotonic() - last_receive >= 30):
                last_receive = time.monotonic()
                try:
                    message = receive(config)
                    if message != incoming_status:
                        logging.info('Incoming: %s', message)
                    incoming_status = message
                    errors.pop('receive', None)
                except Exception as error:
                    incoming_status = 'Paused: ' + str(error)
                    if errors.get('receive') != str(error):
                        logging.error('Incoming update: %s', error)
                        errors['receive'] = str(error)
            (state / 'status.json').write_text(json.dumps({
                'pid': os.getpid(), 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'errors': errors, 'automatic_push': config.get('push', False),
                'branch': config.get('branch', 'main'),
                'receive_main': config.get('receive_main', False),
                'incoming_status': incoming_status,
                'incoming_pending': has_pending(config),
            }, indent=2), encoding='utf-8')
            if once:
                return 1 if errors else 0
            time.sleep(config.get('poll_seconds', 2))
    finally:
        lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(json.loads(Path(args.config).read_text(encoding='utf-8-sig')), args.once))
