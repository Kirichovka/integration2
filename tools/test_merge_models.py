"""Integration tests using disposable repositories, never the real remote."""
import csv
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

import merge_models as merger


class ModelMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='integration2-merge-')
        self.previous = Path.cwd()
        self.root = Path(self.temp.name)
        self.git('init', '--bare', str(self.root / 'remote.git'))
        self.git('clone', str(self.root / 'remote.git'), str(self.root / 'work'))
        os.chdir(self.root / 'work')
        self.git('checkout', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        Path('.gitattributes').write_text('* -text\n')
        self.mdj = Path('models/machine-vision/Machine VIsion.mdj')
        self.mdj.parent.mkdir(parents=True)
        self.write_model('Original')
        with merger.closing(sqlite3.connect('models/machine-vision/Machine Vision Lab.vpp')) as db:
            db.execute('CREATE TABLE PROJECT_INFO(ID TEXT)')
            db.execute("INSERT INTO PROJECT_INFO VALUES ('project')")
            db.commit()
        with Path('MANIFEST.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['path', 'bytes', 'sha256', 'source'])
            writer.writeheader()
            for name in sorted(merger.MODELS):
                writer.writerow({'path': name, 'source': 'test'})
        merger.update_manifest(Path.cwd())
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('push', '-u', 'origin', 'main')

    @staticmethod
    def git(*args):
        result = subprocess.run(['git', *args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def write_model(self, name):
        self.mdj.write_text(json.dumps({'_type': 'Project', '_id': 'root', 'name': name}, indent=2))

    def branch(self, name='alice'):
        self.git('checkout', '-b', f'contributors/{name}', 'origin/main')

    def publish(self, name='alice'):
        self.git('add', '.')
        self.git('commit', '-m', 'Save')
        self.git('push', 'origin', f'contributors/{name}')
        self.git('checkout', 'main')

    def test_clean_merge_and_repeat(self):
        self.branch()
        self.write_model('New')
        self.publish()
        self.assertEqual(merger.merge_branch(Path.cwd(), 'contributors/alice'), 'Merged into main')
        self.assertEqual(merger.merge_branch(Path.cwd(), 'contributors/alice'), 'Already merged')
        self.assertEqual(json.loads(self.mdj.read_text())['name'], 'New')
        rows = list(csv.DictReader(Path('MANIFEST.csv').read_text().splitlines()))
        entry = next(row for row in rows if row['path'] == self.mdj.as_posix())
        self.assertEqual(entry['sha256'], merger.hashlib.sha256(self.mdj.read_bytes()).hexdigest().upper())

    def test_conflict_leaves_main_unchanged(self):
        self.branch()
        self.write_model('Alice')
        self.publish()
        self.write_model('Main change')
        self.git('add', '.')
        self.git('commit', '-m', 'Main update')
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'Merge conflict'):
            merger.merge_branch(Path.cwd(), 'contributors/alice')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertFalse(self.git('status', '--porcelain'))

    def test_invalid_model_is_not_merged(self):
        self.branch()
        self.mdj.write_text('{"_type":"Project","_id":"root","link":{"$ref":"missing"}}')
        self.publish()
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'dangling references'):
            merger.merge_branch(Path.cwd(), 'contributors/alice')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)

    def test_non_model_change_requires_manual_review(self):
        self.branch()
        Path('unexpected.py').write_text('print("must not run")')
        self.publish()
        with self.assertRaisesRegex(ValueError, 'outside model files'):
            merger.merge_branch(Path.cwd(), 'contributors/alice')


if __name__ == '__main__':
    unittest.main()
