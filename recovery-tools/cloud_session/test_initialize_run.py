"""Small flow rendering tests; no live operator, mailbox or network."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest

HERE=Path(__file__).resolve().parent

class RunInitializationTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('initialize_run',HERE/'initialize_run.py')
        self.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.module)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.archive=self.root/'snapshot.tar.gz';self.archive.write_bytes(b'fixture')
        self.parts=self.root/'parts';self.parts.mkdir();self.tool=self.root/'tool';(self.tool/'bin').mkdir(parents=True)
        (self.tool/'bin/workspace-recover.mjs').write_text('// fixture; never executed\n')
        (self.tool/'package.json').write_text(json.dumps({'name':'workspace-recover','version':'1.0.0'}))
        self.kw=dict(run=self.root/'run',destination=self.root/'restored',parts=self.parts,checkpoint=self.archive,checksum='a'*64,tool=self.tool,python=Path(sys.executable),drive_folder='verified-folder-id',recipient='user@example.test')
    def test_restore_flow_uses_explicit_inputs_and_no_shell(self):
        report=self.module.initialize(**self.kw)
        flow=json.loads(Path(report['flow']).read_text());args=flow['steps'][0]['argv']
        self.assertEqual(args[0],str(self.kw['python']))
        self.assertIn(str(self.archive),args);self.assertIn(str(self.kw['destination']),args)
        self.assertNotIn('shell',flow['steps'][0]);self.assertNotIn('work',args)
    def test_new_binding_is_only_a_path_not_a_session(self):
        report=self.module.initialize(**self.kw);c=json.loads(Path(report['run_config']).read_text())
        binding=Path(c['caller_binding']);self.assertFalse(binding.exists());self.assertNotIn('session',c)
        self.assertTrue(binding.name.startswith('cloud-'));self.assertEqual(binding.parent,self.kw['destination']/'poise-op/operator/profile')
    def test_reinitialization_does_not_overwrite(self):
        self.module.initialize(**self.kw)
        with self.assertRaises(ValueError):self.module.initialize(**self.kw)
    def test_rejects_bad_checksum_before_creating_run(self):
        self.kw['checksum']='f'*63
        with self.assertRaises(ValueError):self.module.initialize(**self.kw)
        self.assertFalse(self.kw['run'].exists())
    def test_rejects_existing_restore_target(self):
        self.kw['destination'].mkdir()
        with self.assertRaises(ValueError):self.module.initialize(**self.kw)
    def test_rejects_unpinned_tool_version(self):
        (self.tool/'package.json').write_text(json.dumps({'version':'9.9.9'}))
        with self.assertRaises(ValueError):self.module.initialize(**self.kw)
    def test_preserves_backup_and_delivery_parameters(self):
        report=self.module.initialize(**self.kw);c=json.loads(Path(report['run_config']).read_text())
        self.assertEqual(c['backup_policy'],{'min_seconds':600,'max_seconds':900})
        self.assertEqual(c['delivery'],{'google_drive_parent':'verified-folder-id','gmail_recipient':'user@example.test'})

if __name__=='__main__':unittest.main()
