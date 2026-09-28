"""Recover a saved outline rejected by the context-disposition bug, without a host.

This maintenance operation appends one immutable version. Original failed runs,
their returned content, source checks, selection and task usage are unchanged.
"""
from copy import deepcopy
import json

from . import writing as w
from .budget import utc_now
from .writing_contracts import OUTLINE, OUTLINE_RESEARCH

REJECTION='框架必须说明每条已选材料的安排或未用原因'


def recover_failed_outline(conn, run_id):
    conn.execute('BEGIN IMMEDIATE')
    try:
        row=conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(run_id,)).fetchone()
        if not row or row['stage']!='outline' or row['status']!='failed':
            raise ValueError('只能恢复因材料安排校验失败而保存的框架')
        data=json.loads(row['input_json'])
        if data.get('failure_message')!=REJECTION or not isinstance(data.get('raw_failed_response'),dict):
            raise ValueError('没有符合本次修复条件的完整模型返回')
        w._decision(conn,row['edition_id'],row['decision_revision'])
        if conn.execute("SELECT 1 FROM fn_writing_runs WHERE status='running' LIMIT 1").fetchone():
            raise ValueError('请等待正在执行的写作任务完成后再恢复')
        request_id='recover-outline-'+run_id
        old=conn.execute('SELECT version_id FROM fn_writing_versions WHERE request_id=?',(request_id,)).fetchone()
        if old:
            result=w.public_version(w.version(conn,old['version_id']))
            conn.commit()
            return result
        snapshot=deepcopy(data['snapshot']);raw=deepcopy(data['raw_failed_response'])
        w._validate_shape(raw,OUTLINE_RESEARCH if data.get('supplement_latest') else OUTLINE)
        payload={key:deepcopy(raw[key]) for key in OUTLINE['properties']}
        if data.get('supplement_latest'):
            # The failing validator ran after staging and original-page checks.
            # Never promote an unverified saved discovery during recovery.
            research=snapshot.get('supplemental_research',{})
            added={d['source_id'] for d in raw['supplemental_sources']}
            observed={s['id']:s for s in snapshot['sources']}
            if (research.get('status') not in {'partial','completed'} or
                research.get('reported_sources')!=raw['supplemental_sources'] or
                added!=set(research.get('source_ids',[])) or not added<=set(observed)):
                raise ValueError('原任务没有保留完整补查核对记录，不能仅凭模型摘录恢复')
            from .writing_research import finalize_discoveries
            finalize_discoveries(payload,snapshot,added)
        content,checks=w.validate_outline(payload,snapshot)
        if not content['context_dispositions']:
            raise ValueError('此框架不属于已知历史范文说明误拦截，保留原失败记录')
        content['recovery']=dict(source_run_id=run_id,recovered_at=utc_now(),
            mode='saved_model_response_no_new_host_call',validation_version='context-dispositions-v1',
            original_skill_version=snapshot['skill_version'],original_created_at=row['created_at'])
        result=w.public_version(w._save_version(conn,snapshot,'outline',content,checks,
            parent_id=data.get('parent_id'),run_id=run_id,request_id=request_id,
            input_hash=w.fingerprint(['context-dispositions-v1',run_id,raw])))
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise
