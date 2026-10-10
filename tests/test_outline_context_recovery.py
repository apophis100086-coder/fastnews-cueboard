"""Context notes must not discard a returned outline or bypass factual gates."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fastnews import writing as w
from fastnews.host_provider import HostResult
from fastnews.outline_recovery import REJECTION, recover_failed_outline
from fastnews.task_control import TaskControl
from tests import test_news_writing as fixtures


class OutlineContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.NewsWritingTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.conn=self.fixture.conn
        self.snapshot=w._snapshot(self.conn,self.fixture.cfg,self.fixture.eid,1)
        self.snapshot['product_context']['published_history']=[dict(id='history_fixture',title='合成历史范文')]
        self.raw=self.fixture.outline(self.snapshot)
        self.raw['dispositions'].append(dict(material_id='history_fixture',role='历史参照',reason='只比较写法，不采用旧事实'))

    def failed(self, supplement=False):
        control=TaskControl()
        run=w.accept_run(self.conn,self.fixture.cfg,'outline',self.fixture.data(supplement_latest=supplement),control)
        row=self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(run['run_id'],)).fetchone()
        data=json.loads(row['input_json'])
        data.update(snapshot=deepcopy(self.snapshot),raw_failed_response=deepcopy(self.raw),failure_message=REJECTION)
        if supplement:
            data['raw_failed_response'].update(supplemental_sources=[],research_summary='合成补查未新增')
            data['snapshot']['supplemental_research']=dict(enabled=True,status='completed',reported_sources=[],source_ids=[])
        with self.conn:
            self.conn.execute("UPDATE fn_writing_runs SET status='failed',error_code='failed',input_json=? WHERE run_id=?",(w.dumps(data),run['run_id']))
        return run['run_id']

    def test_known_history_note_is_retained_separately_and_success_keeps_raw(self):
        original=deepcopy(self.raw)
        with patch.object(w,'_snapshot',return_value=deepcopy(self.snapshot)):
            control=TaskControl();run=w.accept_run(self.conn,self.fixture.cfg,'outline',self.fixture.data(),control)
        version=w.execute_run(self.conn,run['run_id'],control,runner=lambda *a,**kw:HostResult(self.raw,{},{}))
        self.assertEqual(version['checks']['status'],'ready_to_confirm')
        self.assertEqual(len(version['content']['dispositions']),1)
        self.assertEqual(version['content']['context_dispositions'][0]['material_id'],'history_fixture')
        self.assertEqual(self.raw,original)
        saved=json.loads(self.conn.execute('SELECT input_json FROM fn_writing_runs WHERE run_id=?',(run['run_id'],)).fetchone()[0])
        self.assertEqual(saved['model_response'],original)
        self.assertEqual(self.fixture.confirm(version)['stage'],'confirmed_outline')

    def test_unknown_duplicate_and_missing_material_arrangements_stay_invalid(self):
        for kind in ['unknown','duplicate','missing']:
            with self.subTest(kind=kind):
                raw=deepcopy(self.raw)
                if kind=='unknown':raw['dispositions'][-1]['material_id']='history_not_in_input'
                elif kind=='duplicate':raw['dispositions'].append(deepcopy(raw['dispositions'][-1]))
                else:raw['dispositions'].pop(0)
                with self.assertRaises(ValueError):w.validate_outline(raw,self.snapshot)

    def test_history_is_not_evidence_and_real_gaps_still_block(self):
        for place in ['claims','paragraphs']:
            raw=deepcopy(self.raw)
            if place=='claims':raw['claims'][0]['material_id']='history_fixture'
            else:raw['paragraphs'][0]['material_ids'].append('history_fixture')
            with self.assertRaises(ValueError):w.validate_outline(raw,self.snapshot)
        self.raw['blocking_gaps']=['合成主新闻仍缺依据']
        self.raw['claims'][0]['quote']='原文没有这句话'
        content,checks=w.validate_outline(self.raw,self.snapshot)
        self.assertEqual(checks['status'],'needs_materials')
        self.assertTrue(any(e['code']=='unobserved_quote' for e in checks['errors']))
        self.assertTrue(any(e['code']=='model_gap' for e in checks['errors']))

    def test_recovery_is_idempotent_and_preserves_failure_selection_usage_and_gate(self):
        self.raw['blocking_gaps']=['合成主新闻仍缺依据']
        run_id=self.failed(supplement=True)
        before=dict(self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(run_id,)).fetchone())
        decision=self.fixture.conn.execute('SELECT data_json FROM fn_decisions').fetchone()[0]
        attempts=w.task_attempts(self.conn)
        with patch.object(w,'run_host',side_effect=AssertionError('recovery never calls host')):
            result=recover_failed_outline(self.conn,run_id)
            self.assertEqual(recover_failed_outline(self.conn,run_id),result)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM fn_writing_runs WHERE run_id=?',(run_id,)).fetchone()),before)
        self.assertEqual(self.conn.execute('SELECT data_json FROM fn_decisions').fetchone()[0],decision)
        self.assertEqual(w.task_attempts(self.conn),attempts)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_writing_versions').fetchone()[0],1)
        self.assertEqual(result['checks']['status'],'needs_materials')
        self.assertEqual(w.workflow(self.conn,self.fixture.eid)['runs'][-1]['recovered_version_id'],result['version_id'])
        with self.assertRaises(ValueError):self.fixture.confirm(result)

    def test_recovery_does_not_restage_or_trust_missing_supplement_verification(self):
        run_id=self.failed(supplement=True)
        row=self.conn.execute('SELECT input_json FROM fn_writing_runs WHERE run_id=?',(run_id,)).fetchone()
        data=json.loads(row[0]);data['snapshot']['supplemental_research'].pop('reported_sources')
        with self.conn:self.conn.execute('UPDATE fn_writing_runs SET input_json=? WHERE run_id=?',(w.dumps(data),run_id))
        with self.assertRaisesRegex(ValueError,'核对记录'):recover_failed_outline(self.conn,run_id)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_writing_versions').fetchone()[0],0)

    def test_recovery_rejects_changed_human_selection(self):
        run_id=self.failed();self.fixture.save(1)
        with self.assertRaises(ValueError):recover_failed_outline(self.conn,run_id)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM fn_writing_versions').fetchone()[0],0)


if __name__=='__main__':unittest.main()
