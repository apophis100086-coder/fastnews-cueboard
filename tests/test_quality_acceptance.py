from pathlib import Path
import tempfile
import unittest
from tools import quality_acceptance as q


class QualityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);q.init(self.root/'batch')

    def register(self,case_id):
        path=self.root/'article.json';q.write(path,dict(version_id=case_id,
            snapshot=dict(product_code=case_id.rsplit('-',1)[0],workflow='direct-newsroom-v4-agent',news_cutoff='2026-09-30'),
            content=dict(paragraphs=[dict(text='Synthetic news')]),checks=dict(status='ready')))
        return q.register(self.root/'batch',case_id,path,True)

    def test_unreviewed_work_never_has_an_adoption_rate(self):
        self.register('513130-01');result=q.summary(self.root/'batch')
        self.assertIsNone(result['adoption_rate']);self.assertEqual(result['passed'],0)

    def test_all_cases_must_be_reviewed_and_each_product_meets_threshold(self):
        for code in q.PRODUCTS:
            for i in range(1,6):
                case=f'{code}-{i:02d}';self.register(case)
                path=self.root/'review.json';q.write(path,dict(reviewer='Synthetic test reviewer',actual_edit_minutes=8,
                    criteria={k:i<5 for k in q.CRITERIA}))
                q.review(self.root/'batch',case,path)
        result=q.summary(self.root/'batch')
        self.assertEqual(result['status'],'passed');self.assertEqual(result['adoption_rate'],.8)

    def test_freeze_and_missing_review_conditions_are_enforced(self):
        case='513130-01';self.register(case)
        with self.assertRaises(ValueError):self.register(case)
        path=self.root/'review.json';q.write(path,dict(reviewer='Synthetic test reviewer',actual_edit_minutes=5,criteria={}))
        with self.assertRaises(ValueError):q.review(self.root/'batch',case,path)

    def test_one_article_cannot_fill_two_case_slots(self):
        self.register('513130-01')
        with self.assertRaises(ValueError):q.register(self.root/'batch','513130-02',self.root/'article.json',True)
