import unittest
from tools.recall_acceptance import compare


class RecallAcceptanceTests(unittest.TestCase):
    def test_missing_and_duplicate_events_remain_visible(self):
        report=dict(news_cutoff='2026-09-30',items=[dict(title='产业 新进展',sources=[]),dict(title='产业新进展 重报',sources=[])],gaps=['来源未接通'])
        answers=dict(news_cutoff='2026-09-30',events=[dict(event_id='one',aliases=['产业新进展']),dict(event_id='two',aliases=['央行通知'],priority='important')])
        result=compare(report,answers)
        self.assertEqual(result['recall'],.5)
        self.assertEqual(result['missing'][0]['event_id'],'two')
        self.assertEqual(len(result['duplicate_candidates']),1)
        self.assertEqual(result['coverage_gaps'],['来源未接通'])

    def test_different_cutoffs_and_empty_answers_are_rejected(self):
        with self.assertRaises(ValueError):compare(dict(news_cutoff='one'),dict(news_cutoff='two',events=[]))
        with self.assertRaises(ValueError):compare(dict(news_cutoff='one'),dict(news_cutoff='one',events=[]))

    def test_urls_match_without_claiming_semantic_verification(self):
        result=compare(dict(news_cutoff='x',items=[dict(title='不同标题',sources=[dict(url='https://example.invalid/a')])]),
                       dict(news_cutoff='x',events=[dict(event_id='one',source_urls=['https://example.invalid/a'])]))
        self.assertEqual(result['recall'],1)
        self.assertIn('pending_human_check',result['status'])
