"""Direct-edit watcher tests against disposable Git repositories."""
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import test_merge_models

if os.name == 'nt':
    import watch_models as watcher


@unittest.skipUnless(os.name == 'nt', 'The save watcher runs on Windows')
class DirectSaveTests(test_merge_models.ModelMergeTests):
    test_clean_merge_and_repeat = None
    test_conflict_leaves_main_unchanged = None
    test_invalid_model_is_not_merged = None
    test_non_model_change_requires_manual_review = None

    def setUp(self):
        super().setUp()
        self.config = {'repository': str(Path.cwd()), 'git': 'git', 'branch': 'main',
                       'update_manifest': False}
        self.model = {'name': 'StarUML', 'source': str(self.mdj.resolve()),
                      'destination': self.mdj.as_posix()}

    def test_save_commits_without_replacing_working_file(self):
        self.write_model('Saved directly')
        original = self.mdj.read_bytes()
        manifest = Path('MANIFEST.csv').read_bytes()
        Path('unrelated.txt').write_text('keep staged')
        self.git('add', 'unrelated.txt')
        with mock.patch.object(watcher.os, 'replace', side_effect=AssertionError('Must not replace direct file')):
            self.assertTrue(watcher.sync(self.config, self.model))
        self.assertEqual(self.mdj.read_bytes(), original)
        self.assertEqual(Path('MANIFEST.csv').read_bytes(), manifest)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'unrelated.txt')
        self.assertFalse(watcher.sync(self.config, self.model))

    def test_invalid_save_remains_uncommitted_and_untouched(self):
        self.mdj.write_text('{incomplete')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaises(json.JSONDecodeError):
            watcher.sync(self.config, self.model)
        self.assertEqual(self.mdj.read_text(), '{incomplete')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_save_during_validation_is_preserved(self):
        self.write_model('First save')
        parse = watcher.json.loads

        def save_again(data):
            self.write_model('Newer save')
            return parse(data)

        with mock.patch.object(watcher.json, 'loads', side_effect=save_again):
            with self.assertRaisesRegex(RuntimeError, 'changed during validation'):
                watcher.sync(self.config, self.model)
        self.assertEqual(json.loads(self.mdj.read_bytes())['name'], 'Newer save')


if __name__ == '__main__':
    unittest.main()
