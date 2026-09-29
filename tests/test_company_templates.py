"""Company copy isolation, date validity, and an audit that cannot edit prose."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from fastnews.writing_templates import digest,select_template,attach,writer_brief
from fastnews.writing_delivery import render,word
from fastnews.writing_contracts import EDITORIAL_EDIT,ARTICLE_AUDIT
from fastnews.writing import _validate_shape


class CompanyTemplatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'library.json'
        source=dict(name='合成基金',title='合成统一模板',published_at='2026-09-01',url='https://example.org/doc')
        self.template=dict(id='sample',version='1',product_code='family',effective_from='2026-09-01',
            blocks=[dict(kind='product_intro',text='成员123456与654321，覆盖不同方向。',sources=[source]),
                    dict(kind='risk',text='风险提示：合成风险文字。',sources=[source],display_sources=False)])

    def save(self):
        self.template['sha256']=digest({k:v for k,v in self.template.items() if k!='sha256'})
        self.path.write_text(json.dumps({'items':[self.template]}),encoding='utf-8')

    def test_cutoff_hash_and_dynamic_value_validity(self):
        self.save()
        self.assertEqual(select_template('family','2026-08-31T23:59:59+08:00',library=self.path)['status'],'missing')
        self.assertEqual(select_template('family','2026-09-02T23:59:59+08:00',library=self.path)['status'],'ready')
        self.template['blocks'][0]['variables']={'holders':dict(value=1,as_of='2026-10-01',source='observed')}
        self.save()
        self.assertEqual(select_template('family','2026-09-02T23:59:59+08:00',library=self.path)['status'],'ready')
        self.assertEqual(len(select_template('family','2026-09-02T23:59:59+08:00',library=self.path)['blocks']),1)
        x=json.loads(self.path.read_text());x['items'][0]['blocks'][1]['text']='changed'
        self.path.write_text(json.dumps(x));self.assertEqual(select_template('family','2026-09-02T23:59:59+08:00',library=self.path)['status'],'invalid')

    def test_frozen_copy_once_and_urls_only_in_trace(self):
        self.save();template=select_template('family','2026-09-02T23:59:59+08:00',library=self.path)
        snapshot=dict(company_template=template,product_definition={})
        content=dict(title='合成标题',paragraphs=[dict(text='合成新闻。',sources=[self.template['blocks'][0]['sources'][0]])],risk_notice={'text':'旧通用风险'},appendix=[{'text':'旧追加风险'}])
        attach(content,snapshot);attach(content,snapshot)
        rendered=render(content)
        self.assertEqual(rendered.count('风险提示：'),1)
        self.assertNotIn('旧通用风险',rendered);self.assertNotIn('https://',rendered)
        self.assertEqual(content['template_blocks'],template['blocks'])
        self.assertIn('https://',json.dumps(content))
        from zipfile import ZipFile
        from io import BytesIO
        with ZipFile(BytesIO(word(content))) as z:self.assertNotIn('https://',z.read('word/document.xml').decode())
        brief=writer_brief(template);self.assertNotIn('risk',json.dumps(brief))

    def test_auditor_cannot_replace_title_or_text(self):
        self.assertNotIn('evidence',EDITORIAL_EDIT['properties'])
        self.assertNotIn('appendix',EDITORIAL_EDIT['properties'])
        self.assertNotIn('paragraphs',ARTICLE_AUDIT['properties'])
        with self.assertRaises(ValueError):_validate_shape({'title':'覆盖正文'},ARTICLE_AUDIT)

    def test_template_rename_conflict_is_visible(self):
        self.save();template=select_template('family','2026-09-02T23:59:59+08:00',library=self.path)
        snapshot=dict(company_template=template,product_definition={'effective_updates':[{'previous_name':'成员123456','subject_code':'123456'}]})
        content={'paragraphs':[]};attach(content,snapshot)
        self.assertEqual(content['company_template']['status'],'pending')
        self.assertTrue(content['company_template']['issues'])


if __name__=='__main__':unittest.main()
