"""Repository guards exercised on disposable Git histories, without remotes."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import check_repository as guard


class RepositoryGuardTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.git('init','-q')
        self.git('config','user.email','fixture@example.invalid')
        self.git('config','user.name','Synthetic test')
        self.git('config','core.hooksPath',str(self.root/'no-hooks'))
        self.git('config','commit.gpgsign','false')
        patched=patch.object(guard,'ROOT',self.root);patched.start();self.addCleanup(patched.stop)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add','-A');self.git('commit','-qm','synthetic fixture')
        return self.git('rev-parse','HEAD')

    def test_foreign_repository_cannot_change_hook_repository_config(self):
        original = (self.root / '.git/config').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory)
            inherited = {'GIT_DIR': str(self.root / '.git'), 'GIT_WORK_TREE': str(self.root),
                         'GIT_INDEX_FILE': str(self.root / '.git/index'),
                         'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'user.name',
                         'GIT_CONFIG_VALUE_0': 'must not cross the boundary'}
            with patch.dict(os.environ, inherited):
                environment = guard.isolated_environment()
            self.assertNotIn('GIT_DIR', environment)
            self.assertNotIn('GIT_CONFIG_COUNT', environment)
            self.assertNotIn('GIT_CONFIG_VALUE_0', environment)
            subprocess.run(['git', 'init', '-q'], cwd=foreign, env=environment, check=True)
            subprocess.run(['git', 'config', '--local', 'user.name', 'Isolated fixture'],
                           cwd=foreign, env=environment, check=True)
            self.assertTrue((foreign / '.git').is_dir())
            self.assertEqual((self.root / '.git/config').read_bytes(), original)

    def test_deleted_runtime_file_is_still_rejected_from_history(self):
        path=self.root/'data'/'private.txt';path.parent.mkdir();path.write_text('synthetic')
        self.commit();path.unlink();(self.root/'source.txt').write_text('safe');head=self.commit()
        with self.assertRaisesRegex(SystemExit,'Private/runtime'):
            guard.check_history([head])

    def test_deleted_secret_is_rejected_without_echoing_value(self):
        value='ghp_'+'A'*36
        path=self.root/'source.txt';path.write_text(value);self.commit()
        path.write_text('safe');head=self.commit()
        with self.assertRaises(SystemExit) as caught:
            guard.check_history([head])
        self.assertIn('GitHub token',str(caught.exception));self.assertNotIn(value,str(caught.exception))

    def test_annotated_ancestor_tag_and_head_branch_are_checked(self):
        path=self.root/'source.txt';path.write_text('one');first=self.commit()
        self.git('-c','tag.gpgsign=false','tag','-a','v-test','-m','synthetic restore point')
        tag=self.git('rev-parse','v-test');path.write_text('two');head=self.commit()
        tips=guard.push_tips([f'refs/heads/work {head} refs/heads/main '+ '0'*40,
                             f'refs/tags/v-test {tag} refs/tags/v-test '+ '0'*40],head)
        self.assertEqual(guard.check_history(tips),2)
        with self.assertRaisesRegex(SystemExit,'Check out'):
            guard.push_tips([f'refs/heads/old {first} refs/heads/old '+ '0'*40],head)

    def test_tag_cannot_push_an_unchecked_descendant(self):
        path=self.root/'source.txt';path.write_text('one');first=self.commit()
        path.write_text('two');second=self.commit()
        with self.assertRaisesRegex(SystemExit,'Tags must'):
            guard.push_tips([f'refs/tags/new {second} refs/tags/new '+ '0'*40],first)

    def test_tag_annotation_is_scanned(self):
        (self.root/'source.txt').write_text('safe');head=self.commit()
        self.git('-c','tag.gpgsign=false','tag','-a','v-test','-m','ghp_'+'A'*36)
        tag=self.git('rev-parse','v-test')
        with self.assertRaisesRegex(SystemExit,'GitHub token'):
            guard.push_tips([f'refs/tags/v-test {tag} refs/tags/v-test '+ '0'*40],head)
