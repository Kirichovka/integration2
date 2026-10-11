"""Exercise incoming updates against disposable Git repositories and working files."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock
import unittest

import test_merge_models
import receive_models as receiver


class ReceiveTests(test_merge_models.ModelMergeTests):
    def setUp(self):
        super().setUp()
        self.source = self.root / 'working.mdj'
        self.source.write_bytes(self.mdj.read_bytes())
        self.config = {
            'repository': str(Path.cwd()), 'git': 'git', 'branch': 'contributors/alice',
            'models': [{'name': 'StarUML', 'source': str(self.source),
                        'destination': self.mdj.as_posix()}],
        }
        self.git('checkout', '-b', 'contributors/alice')
        self.git('checkout', 'main')
        self.write_model('Team update')
        self.git('add', '.')
        self.git('commit', '-m', 'Team update')
        self.git('push', 'origin', 'main')
        self.git('checkout', 'contributors/alice')

    # Keep this suite focused on receiving rather than rerunning inherited merge tests.
    test_clean_merge_and_repeat = None
    test_conflict_leaves_main_unchanged = None
    test_invalid_model_is_not_merged = None
    test_non_model_change_requires_manual_review = None

    def test_open_editor_keeps_both_files_unchanged(self):
        before = self.source.read_bytes()
        head = self.git('rev-parse', 'HEAD')
        status = receiver.receive(self.config, lambda: ['StarUML.exe'])
        self.assertIn('waiting for editors', status)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.mdj.read_bytes(), before)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_closed_editor_receives_update_and_keeps_backup(self):
        before = self.source.read_bytes()
        self.assertIn('Updated from main', receiver.receive(self.config, lambda: []))
        self.assertEqual(json.loads(self.source.read_bytes())['name'], 'Team update')
        backups = list(receiver.state_path(self.config).glob('backups/*/*.mdj'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertFalse(receiver.has_pending(self.config))
        self.assertEqual(receiver.receive(self.config, lambda: []), 'Up to date with main')

    def test_new_local_save_is_not_overwritten(self):
        self.source.write_text('{"_type":"Project","_id":"root","name":"My save"}')
        before = self.source.read_bytes()
        self.assertIn('committed first', receiver.receive(self.config, lambda: []))
        self.assertEqual(self.source.read_bytes(), before)

    def test_unmerged_local_commit_waits(self):
        self.write_model('My committed save')
        self.source.write_bytes(self.mdj.read_bytes())
        self.git('add', '.')
        self.git('commit', '-m', 'Local work')
        self.assertIn('Waiting for contributor commits', receiver.receive(self.config, lambda: []))
        self.assertEqual(json.loads(self.source.read_bytes())['name'], 'My committed save')

    def test_crash_after_fast_forward_recovers_without_outgoing_undo(self):
        original = receiver.atomic_write

        def fail_source(path, data):
            if path == self.source:
                raise OSError('Simulated interruption')
            return original(path, data)

        with mock.patch.object(receiver, 'atomic_write', side_effect=fail_source):
            with self.assertRaisesRegex(OSError, 'interruption'):
                receiver.receive(self.config, lambda: [])
        self.assertTrue(receiver.has_pending(self.config))
        self.assertEqual(json.loads(self.mdj.read_bytes())['name'], 'Team update')
        self.assertEqual(json.loads(self.source.read_bytes())['name'], 'Original')
        self.assertIn('Updated from main', receiver.receive(self.config, lambda: []))
        self.assertFalse(receiver.has_pending(self.config))

    def test_recovery_does_not_discard_changes_made_after_interruption(self):
        with mock.patch.object(receiver, 'finish_pending', return_value='Simulated interruption'):
            receiver.receive(self.config, lambda: [])
        self.source.write_text('new work after interruption')
        with self.assertRaisesRegex(RuntimeError, 'changed since backup'):
            receiver.receive(self.config, lambda: [])
        self.assertEqual(self.source.read_text(), 'new work after interruption')
        self.assertTrue(receiver.has_pending(self.config))

    def test_editor_reopened_during_update_defers_replacement(self):
        # Preflight, journal recovery, before merge, before source replacement.
        check = mock.Mock(side_effect=[[], [], [], ['StarUML.exe']])
        self.assertIn('queued', receiver.receive(self.config, check))
        self.assertEqual(json.loads(self.source.read_bytes())['name'], 'Original')
        self.assertTrue(receiver.has_pending(self.config))
        self.assertIn('Waiting for editors', receiver.receive(self.config, lambda: ['StarUML.exe']))
        self.assertIn('Updated from main', receiver.receive(self.config, lambda: []))

    def test_process_discovery_failure_does_not_update(self):
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaises(OSError):
            receiver.receive(self.config, mock.Mock(side_effect=OSError('Process list unavailable')))
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)

    def test_direct_repository_model_updates_after_editor_closes(self):
        self.config['models'][0]['source'] = str(self.mdj.resolve())
        before = self.mdj.read_bytes()
        self.assertIn('waiting for editors', receiver.receive(self.config, lambda: ['StarUML.exe']))
        self.assertEqual(self.mdj.read_bytes(), before)
        self.assertIn('Updated from main', receiver.receive(self.config, lambda: []))
        self.assertEqual(json.loads(self.mdj.read_bytes())['name'], 'Team update')
        backups = list(receiver.state_path(self.config).glob('backups/*/*.mdj'))
        self.assertEqual(backups[0].read_bytes(), before)

    def test_direct_visual_paradigm_receives_only_after_editor_closes(self):
        path = Path('models/machine-vision/Machine Vision Lab.vpp')
        before = path.read_bytes()
        self.git('checkout', 'main')
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO PROJECT_INFO VALUES ('team-vpp-update')")
            db.commit()
        self.git('add', '.')
        self.git('commit', '-m', 'Team Visual Paradigm update')
        self.git('push', 'origin', 'main')
        self.git('checkout', 'contributors/alice')
        self.config['models'] = [{'name': 'Visual Paradigm', 'source': str(path.resolve()),
                                  'destination': path.as_posix()}]
        self.assertIn('waiting for editors', receiver.receive(self.config, lambda: ['Visual Paradigm.exe']))
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('Updated from main', receiver.receive(self.config, lambda: []))
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM PROJECT_INFO WHERE ID='team-vpp-update'").fetchone()[0], 1)
        backups = list(receiver.state_path(self.config).glob('backups/*/*.vpp'))
        self.assertEqual(backups[0].read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
