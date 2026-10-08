import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from fastnews import api_connection as c
from fastnews.api_provider import build_request,post_json
from fastnews.shared_api import bootstrap,read_seed,DEFAULTS,SEED


class SharedAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.seed=self.root/SEED
        self.seed.parent.mkdir(parents=True)
        self.seed.write_text(json.dumps({'api_key':'synthetic-shared-fixture'}),encoding='utf-8')
        self.crypto=patch.object(c,'protect',side_effect=lambda v,decrypt=False:v.removeprefix('protected:') if decrypt else 'protected:'+v)
        self.crypto.start();self.addCleanup(self.crypto.stop)

    def test_portable_seed_import_is_local_and_has_no_material_consent(self):
        self.assertTrue(bootstrap(self.root));status=c.public_status(self.root/'data/connections')
        self.assertTrue(status['configured']);self.assertFalse(status['authorized']);self.assertFalse(status['web_search'])
        self.assertEqual(status['credential_origin'],'shared_package')
        self.assertNotIn('synthetic-shared-fixture',json.dumps(status))
        self.assertTrue(self.seed.exists());self.assertFalse(bootstrap(self.root))

    def test_existing_personal_settings_never_overwritten(self):
        c.save(dict(DEFAULTS,api_key='synthetic-personal-fixture'),self.root/'data/connections')
        self.assertFalse(bootstrap(self.root))
        self.assertEqual(c.load(self.root/'data/connections',secret=True)['api_key'],'synthetic-personal-fixture')

    def test_personal_replacement_clears_shared_label_but_blank_retains_it(self):
        bootstrap(self.root);where=self.root/'data/connections'
        c.save(dict(DEFAULTS,authorized=True,api_key=''),where)
        self.assertEqual(c.public_status(where)['credential_origin'],'shared_package')
        c.save(dict(DEFAULTS,authorized=True,api_key='synthetic-replacement'),where)
        self.assertEqual(c.public_status(where)['credential_origin'],'personal')

    def test_seed_cannot_change_service_and_errors_do_not_echo_content(self):
        self.seed.write_text(json.dumps({'base_url':'https://example.org','api_key':'synthetic-shared-fixture'}),encoding='utf-8')
        with self.assertRaises(ValueError) as error:read_seed(self.seed)
        self.assertNotIn('synthetic-shared-fixture',str(error.exception))
        self.assertFalse((self.root/'data/connections').exists())

    def test_deepseek_json_request_disables_thinking_without_affecting_other_services(self):
        path,request=build_request(DEFAULTS,'test JSON',{'type':'object'})
        self.assertEqual(request['model'],'deepseek-flash');self.assertEqual(request['thinking'],{'type':'disabled'})
        self.assertEqual(request['response_format'],{'type':'json_object'})
        _,other=build_request(dict(DEFAULTS,base_url='https://example.org'),'test JSON',{})
        self.assertNotIn('thinking',other)

    def test_official_deepseek_uses_validated_public_dns_and_verified_tls(self):
        conn=Mock();conn.getresponse.return_value.status=200;conn.getresponse.return_value.read.return_value=b'{}'
        with patch('fastnews.network.SafeHTTPClient._resolve',return_value=('https://api.deepseek.com/chat/completions',['93.184.216.34'])) as resolver,patch('fastnews.network._PinnedHTTP',return_value=conn) as http:
            self.assertEqual(post_json('https://api.deepseek.com/chat/completions','synthetic',{},5),{})
            resolver.assert_called_once();http.assert_called_once_with('api.deepseek.com',443,'93.184.216.34',5,True)
