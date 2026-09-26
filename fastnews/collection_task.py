"""Bound collection as a subprocess; completed source transactions survive expiry."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .host_provider import ProcessGroup, clean_environment
from .task_control import TaskStopped


def collect(conn, config, action, parameters, control, progress=None):
    control.check()
    db_path=conn.execute('PRAGMA database_list').fetchone()[2]
    if not db_path:
        raise ValueError('有时限的真实采集需要持久化数据库')
    root=Path(__file__).resolve().parents[1]
    owner=uuid.uuid4().hex
    config=dict(config,_collection_task_id=owner)
    command=[sys.executable,'-m','fastnews.collection_task']
    proc=subprocess.Popen(command,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',env=clean_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
        start_new_session=os.name!='nt')
    group=ProcessGroup(proc)
    deadline=min(control.deadline-30,time.monotonic()+60)
    request=json.dumps(dict(database=db_path,config=config,action=action,parameters=parameters),ensure_ascii=False)
    first=True
    try:
        while True:
            control.check()
            if time.monotonic()>=deadline:
                return dict(collection_incomplete=True,status='collection_timed_out',
                    note='前置采集达到时限，已完成来源事务保留，未完成来源不是无结果')
            try:
                out,err=proc.communicate(request if first else None,timeout=min(.25,max(.01,deadline-time.monotonic())))
                if proc.returncode:
                    return dict(collection_incomplete=True,status='collection_failed',
                        note='前置采集未完成，已入库材料保留')
                response=json.loads(out)
                for item in response.pop('progress',[]):
                    if progress:
                        progress(item)
                return response['result']
            except subprocess.TimeoutExpired:
                first=False
    finally:
        if proc.poll() is None:
            if os.name!='nt':
                import signal
                os.killpg(proc.pid,signal.SIGKILL)
            else:
                proc.kill()
        group.close()
        proc.communicate(timeout=5)
        # Only this worker's unfinished transactions are marked failed. Earlier
        # sources have already committed, and unrelated jobs are never touched.
        from .budget import utc_now
        with conn:
            for table,field,result_field in [('fn_scan_batches','parameters_json','summary_json'),
                                             ('fn_research_runs','context_json','report_json')]:
                for row in conn.execute('SELECT run_id,'+field+' FROM '+table+" WHERE status='running'").fetchall():
                    if json.loads(row[1]).get('collection_task_id')==owner:
                        conn.execute('UPDATE '+table+" SET status='failed',finished_at=?,"+result_field+'=? WHERE run_id=?',
                            (utc_now(),json.dumps(dict(coverage_incomplete=True,reason='collection_interrupted',
                                note='采集未完成；已入库材料保留')),row[0]))


def main():
    from .db import connect
    from .custom_sources import ReaderAdapter
    from .pipeline import scan_morning
    from .research import research_product
    request=json.load(sys.stdin)
    import sqlite3
    with sqlite3.connect(Path(request['database']).resolve().as_uri()+'?mode=ro',uri=True) as reader:
        mode=reader.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]
    conn=connect(request['database'],mode=mode)
    config=request['config']
    offline=conn.execute("SELECT value FROM fn_meta WHERE key='mode'").fetchone()[0]=='fixture'
    progress=[]
    options=request['parameters']
    adapter=None if offline else ReaderAdapter(config)
    try:
        if request['action']=='digest':
            result=scan_morning(conn,config,offline=offline,adapter=adapter,progress=progress.append,**options)
        else:
            code=options.pop('product_code')
            if options.pop('fetch_public_text',False):
                from .public_text import PublicTextProvider
                options.update(fulltext_provider=PublicTextProvider(config),external_calls_authorized=True)
            result=research_product(conn,config,code,discovery_adapter=adapter,progress=progress.append,**options)
        print(json.dumps(dict(result=result,progress=progress),ensure_ascii=True))
    finally:
        conn.close()


if __name__=='__main__':
    main()
