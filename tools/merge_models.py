"""Merge model-only contributor branches, validating the merged result before push."""
from contextlib import closing
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

MODELS = {'models/machine-vision/Machine VIsion.mdj',
          'models/machine-vision/Machine Vision Lab.vpp'}


def git(*args, check=True):
    result = subprocess.run(['git', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip() if check else result


def validate(root):
    for name in MODELS:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Model must be a regular file: {name}')
        if path.suffix == '.vpp':
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Visual Paradigm database is corrupt')
                if not db.execute('SELECT ID FROM PROJECT_INFO').fetchone():
                    raise ValueError('Visual Paradigm project is missing')
            continue
        project = json.loads(path.read_bytes())
        if not isinstance(project, dict) or project.get('_type') != 'Project':
            raise ValueError('StarUML root must be a Project')
        ids, refs = set(), []

        def visit(value):
            if isinstance(value, dict):
                if '_id' in value:
                    identity = value['_id']
                    if not isinstance(identity, str) or identity in ids:
                        raise ValueError(f'Invalid or duplicate StarUML ID: {identity}')
                    ids.add(identity)
                if '$ref' in value:
                    refs.append(value['$ref'])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(project)
        if any(not isinstance(ref, str) or ref not in ids for ref in refs):
            raise ValueError('StarUML contains dangling references')


def update_manifest(root):
    path = root / 'MANIFEST.csv'
    rows = list(csv.DictReader(path.read_text(encoding='utf-8-sig').splitlines()))
    for name in MODELS:
        matching = [row for row in rows if row['path'] == name]
        if len(matching) != 1:
            raise ValueError(f'Manifest must contain exactly one entry for {name}')
        data = (root / name).read_bytes()
        matching[0].update(bytes=str(len(data)), sha256=hashlib.sha256(data).hexdigest().upper())
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=['path', 'bytes', 'sha256', 'source'])
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(output.getvalue(), encoding='utf-8', newline='')


def merge_branch(root, branch):
    head = git('rev-parse', f'refs/remotes/origin/{branch}')
    if git('merge-base', '--is-ancestor', head, 'HEAD', check=False).returncode == 0:
        return 'Already merged'
    base = git('merge-base', 'HEAD', head)
    changed = set(filter(None, git('diff', '--name-only', '-z', base, head).split('\0')))
    if changed - MODELS:
        raise ValueError('Manual review required: changes outside model files')
    result = git('merge', '--no-ff', '--no-commit', head, check=False)
    if result.returncode:
        git('merge', '--abort', check=False)
        raise ValueError('Merge conflict: resolve on the contributor branch')
    try:
        validate(root)
        update_manifest(root)
        git('add', '--', 'MANIFEST.csv')
        git('commit', '-m', f'Merge validated models from {branch}')
    except Exception:
        git('merge', '--abort', check=False)
        raise
    # No force push: a concurrent main update must never be overwritten.
    git('push', 'origin', 'HEAD:main')
    return 'Merged into main'


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run through the Model collaboration GitHub Actions workflow')
    root = Path.cwd()
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('fetch', 'origin', '+refs/heads/*:refs/remotes/origin/*', '--prune')
    branches = git('for-each-ref', '--format=%(refname:strip=3)',
                   'refs/remotes/origin/contributors/').splitlines()
    messages = ['## Model collaboration', '', '| Branch | Result |', '| --- | --- |']
    failed = False
    # Revisit blocked branches after successful merges; bot pushes do not trigger another run.
    pending = [b for b in branches if re.fullmatch(r'contributors/[A-Za-z0-9-]+', b)]
    results = {}
    for _ in range(len(pending) + 1):
        retry, progress = [], False
        for branch in pending:
            git('checkout', '-B', 'main', 'origin/main')
            try:
                results[branch] = merge_branch(root, branch)
                progress |= results[branch] == 'Merged into main'
            except Exception as error:
                results[branch] = str(error).replace('|', '/').replace('\n', ' ')[:500]
                retry.append(branch)
            git('fetch', 'origin', 'main')
        pending = retry
        if not progress or not pending:
            break
    failed = bool(pending)
    for branch, result in results.items():
        print(f'{branch}: {result}')
        messages.append(f'| `{branch}` | {result} |')
    if not results:
        messages.append('| — | No contributor branches yet |')
    Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('\n'.join(messages) + '\n', encoding='utf-8')
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
