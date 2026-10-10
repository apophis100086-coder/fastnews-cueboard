import unittest
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from fastnews.writing_delivery import render,word,attach_appendix

class DeliveryTests(unittest.TestCase):
    def content(self):
        return dict(title='测试快讯',paragraphs=[dict(text='新闻事实。参考[原文](https://example.org/private)。',sources=[dict(name='统计机构',title='产业信息 https://example.org/long',url='https://example.org/long',published_at='2026-09-25',data_period='2026年8月')])],risk_notice={'text':'基金有风险，投资需谨慎。'})

    def test_word_and_clipboard_have_same_content_and_no_hyperlinks(self):
        content=self.content();text=render(content)
        self.assertNotIn('https',text);self.assertIn('数据统计期：2026年8月',text)
        with ZipFile(BytesIO(word(content))) as z:
            xml=z.read('word/document.xml').decode()
            self.assertNotIn('example.org',xml)
            root=ET.fromstring(xml)
            self.assertEqual(''.join(root.itertext()),text.replace('\n\n',''))
            self.assertNotIn('hyperlink',xml)
            self.assertIn('11906',xml)
            for name in z.namelist():ET.fromstring(z.read(name))

    def test_appendix_does_not_transfer_data_between_members(self):
        source=dict(id='s1',subject_code='510880',text_observed=True,date_observed=True,published_at='2026-09-25',text='管理费0.5%。',name='管理人',title='资料概要',url='https://example.org/x')
        snapshot=dict(product_code='dividend_family',product_definition={'member_codes':['510880','512890']},news_cutoff='2026-09-25T23:59:59+08:00',sources=[source])
        checks={'editorial':{'notes':[]}};content=self.content()
        attach_appendix(content,[dict(kind='product_note',subject_code='512890',source_id='s1',text='管理费0.5%。',quote='管理费0.5%。')],snapshot,checks)
        self.assertEqual(content['appendix'],[])
        self.assertIn('备注与成员产品不对应',content['appendix_missing'][0])
        self.assertEqual(checks['appendix']['status'],'pending')
