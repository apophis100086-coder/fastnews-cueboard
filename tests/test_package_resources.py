"""Fail release construction when required writing resources are incomplete."""
import hashlib,json,unittest
from tools.verify_package import verify_resources


class PackageResourceTests(unittest.TestCase):
    def library(self):
        files={};items=[]
        for i in range(13):
            name=f'data/writing_samples/originals/{i}.docx';raw=f'synthetic {i}'.encode()
            files[name]=raw
            items.append(dict(product_code=['513130','588710','dividend_family'][i%3],local_original=name,sha256=hashlib.sha256(raw).hexdigest()))
        files['data/writing_samples/library.json']=json.dumps(dict(items=items)).encode()
        files['data/writing_templates/library.json']=json.dumps(dict(items=[dict(product_code=c) for c in ['513130','588710','dividend_family']])).encode()
        return files

    def test_complete_resources_and_original_integrity(self):
        files=self.library()
        self.assertEqual(verify_resources(files.__getitem__),dict(samples=13,templates=3))
        files['data/writing_samples/originals/0.docx']=b'changed'
        with self.assertRaisesRegex(ValueError,'checksum'):verify_resources(files.__getitem__)

    def test_missing_product_template_blocks_release(self):
        files=self.library();files['data/writing_templates/library.json']=b'{"items":[]}'
        with self.assertRaisesRegex(ValueError,'templates'):verify_resources(files.__getitem__)

    def test_sample_cannot_read_outside_package(self):
        files=self.library();v=json.loads(files['data/writing_samples/library.json'])
        v['items'][0]['local_original']='../private.docx'
        files['data/writing_samples/library.json']=json.dumps(v).encode()
        with self.assertRaisesRegex(ValueError,'Unsafe'):verify_resources(files.__getitem__)
