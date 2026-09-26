"""Deduplicated task attempts across this project's live databases and backups."""
from pathlib import Path
from contextlib import contextmanager
import os
import sqlite3
from .metadata import parse_instant

ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def reservation(conn):
    """Serialize the count-and-insert step across live project databases."""
    location=conn.execute('PRAGMA database_list').fetchone()[2]
    mode=conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]
    if mode!='live' or not location or not Path(location).resolve().is_relative_to(ROOT/'data'):
        yield
        return
    with (ROOT/'data/.host-task-budget.lock').open('a+b') as handle:
        if handle.seek(0,2)==0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('另一个任务正在登记共享预算，请稍后重试') from None
        try:
            yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def attempts(conn,lo,hi):
    seen=set()
    def read(db):
        for table,key in [('fn_reading_reports','report_id'),('fn_writing_runs','run_id')]:
            try:
                rows=db.execute('SELECT '+key+',created_at FROM '+table)
                for ident,created in rows:
                    if lo<=parse_instant(created)<=hi:
                        seen.add((table,ident))
            except sqlite3.OperationalError:
                continue
    read(conn)
    location=conn.execute('PRAGMA database_list').fetchone()[2]
    mode=conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]
    # Fixture runs and outside-workspace unit-test databases stay isolated.
    if mode=='live' and location and Path(location).resolve().is_relative_to(ROOT/'data'):
        for path in (ROOT/'data').rglob('*.db'):
            if path.resolve()==Path(location).resolve():
                continue
            db=None
            try:
                db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)
                row=db.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()
                if row and row[0]=='live':
                    read(db)
            except sqlite3.DatabaseError:
                # A damaged live budget log must not silently reset allowance.
                raise ValueError('项目中存在无法读取的任务预算记录：'+path.name)
            finally:
                if db:
                    db.close()
    return len(seen)
