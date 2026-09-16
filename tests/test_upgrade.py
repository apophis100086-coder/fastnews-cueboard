"""Real SQLite M1→current upgrades in disposable databases, never user data."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastnews import db
from fastnews.config import load_config
from fastnews.pipeline import scan_morning


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'existing-m1.sqlite3'
        self.config = load_config()

    def create_m1(self):
        with patch.object(db,'migration_files',return_value=[db.MIGRATION_PATH]):
            with closing(db.connect(self.path,mode='fixture')) as conn:
                db.sync_registry(conn,self.config)
                result = scan_morning(conn,self.config,offline=True)
                self.assertGreater(result['articles_new'],0)
                with conn:
                    conn.execute("INSERT INTO fn_meta VALUES('user_note','必须保留的本地备注')")
                    conn.execute('CREATE TABLE personal_notes(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
                    conn.execute('INSERT INTO personal_notes VALUES(?,?)',(7,'用户资料：不允许删库重建。'))
                tables = db._tables(conn)
                snapshot = self.snapshot(conn,tables)
                self.assertEqual([row[0] for row in conn.execute('SELECT version FROM fn_schema_migrations')],[1])
        self.assertEqual(self.backups(),[])
        return tables,snapshot

    @staticmethod
    def snapshot(conn,tables):
        return {table:sorted([tuple(row) for row in conn.execute('SELECT * FROM "'+table+'"')],key=repr) for table in tables}

    def backups(self):
        return sorted(self.path.parent.glob(self.path.name+'.backup-*.sqlite3'))

    def assert_original_data(self,conn,tables,before,include_ledger=False):
        checked = tables if include_ledger else tables-{'fn_schema_migrations'}
        self.assertEqual(self.snapshot(conn,checked),{table:before[table] for table in checked})
        self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0],'ok')
        self.assertIsNone(conn.execute('PRAGMA foreign_key_check').fetchone())

    def test_m1_data_and_user_metadata_survive_all_additive_migrations(self):
        tables,before = self.create_m1()
        with closing(db.connect(self.path,mode='fixture')) as upgraded:
            self.assert_original_data(upgraded,tables,before)
            self.assertEqual([row[0] for row in upgraded.execute('SELECT version FROM fn_schema_migrations ORDER BY version')],[1,2,3,4,5,6])
            self.assertTrue({'fn_editions','fn_research_runs','fn_pilot_feedback','fn_reader_profiles','fn_reading_reports','fn_source_preferences'} <= db._tables(upgraded))
            ledger = [tuple(row) for row in upgraded.execute('SELECT version,backup_path FROM fn_schema_migrations ORDER BY version')]
        backups = self.backups()
        self.assertEqual(len(backups),1)
        self.assertEqual(ledger[0],(1,None))
        self.assertEqual({row[1] for row in ledger[1:]},{str(backups[0])})
        # The backup is a readable, pre-upgrade M1 database with user data intact.
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(db._tables(backup),tables)
            self.assert_original_data(backup,tables,before,include_ledger=True)
        with closing(db.connect(self.path,mode='fixture')) as again:
            self.assert_original_data(again,tables,before)
        self.assertEqual(self.backups(),backups)

    def test_failure_during_003_rolls_back_002_and_003_preserving_m1_and_backup(self):
        tables,before = self.create_m1()
        original_apply = db._apply_migration
        attempted = []
        def fail_after_research_ddl(conn,script):
            attempted.append('003' if 'CREATE TABLE fn_research_runs' in script else '002' if 'CREATE TABLE fn_editions' in script else 'other')
            original_apply(conn,script)
            if 'CREATE TABLE fn_research_runs' in script:
                raise sqlite3.OperationalError('explicit synthetic failure after M4 DDL')
        with patch.object(db,'_apply_migration',side_effect=fail_after_research_ddl):
            with self.assertRaisesRegex(sqlite3.OperationalError,'synthetic failure'):
                db.connect(self.path,mode='fixture')
        self.assertEqual(attempted,['002','003'])
        with closing(sqlite3.connect(self.path)) as preserved:
            self.assertEqual(db._tables(preserved),tables)
            self.assert_original_data(preserved,tables,before,include_ledger=True)
            self.assertFalse({'fn_editions','fn_research_runs','fn_pilot_feedback'} & db._tables(preserved))
        backups = self.backups()
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assert_original_data(backup,tables,before,include_ledger=True)
        # The unmodified M1 program can still reopen the database after failure.
        with patch.object(db,'migration_files',return_value=[db.MIGRATION_PATH]):
            with closing(db.connect(self.path,mode='fixture')) as m1:
                self.assert_original_data(m1,tables,before,include_ledger=True)
        self.assertEqual(self.backups(),backups)
        # Removing the injected failure permits a later normal additive upgrade.
        with closing(db.connect(self.path,mode='fixture')) as retried:
            self.assert_original_data(retried,tables,before)
            self.assertEqual(retried.execute('SELECT max(version) FROM fn_schema_migrations').fetchone()[0],6)
        self.assertEqual(len(self.backups()),2)

    def test_mode_mismatch_does_not_upgrade_or_create_a_backup(self):
        tables,before = self.create_m1()
        with self.assertRaisesRegex(ValueError,'mode mismatch'):
            db.connect(self.path,mode='live')
        with closing(sqlite3.connect(self.path)) as preserved:
            self.assert_original_data(preserved,tables,before,include_ledger=True)
            self.assertEqual(db._tables(preserved),tables)
        self.assertEqual(self.backups(),[])


if __name__=='__main__':
    unittest.main()
