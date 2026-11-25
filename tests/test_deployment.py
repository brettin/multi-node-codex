import hashlib
import tempfile
import unittest
from pathlib import Path
from prepare_deployment import inventory,validate_layout
from provision import snapshot_file,remote_config
import tomllib
import json
import tarfile
from unittest.mock import patch
import prepare_deployment
import provision


class DeploymentTests(unittest.TestCase):
    def test_fresh_checkout_builds_private_inputs_and_deployment_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'project';source.mkdir();(source/'data.txt').write_text('approved content')
            (root/'atlas_cluster').mkdir();(root/'atlas_cluster/__init__.py').write_text('')
            (root/'teamctl.py').write_text('# entry point')
            local_home=root/'local-home';(local_home/'.codex').mkdir(parents=True)
            (local_home/'.codex/mag-models.json').write_text('{}')
            h='/home/example';r=h+'/.local/share/atlas-codex'
            settings={'default_host':'node1','model':'gpt-6-astra','provider':'mag','credential_env':'AMSC_TOKEN','hosts':{'node1':{'remote_root':r,'remote_codex':h+'/.local/bin/codex','projects':{'atlas':{'source':str(source),'remote_cwd':r+'/projects/atlas'}}}}}
            (root/'settings.json').write_text(json.dumps(settings))
            baseline={'remote_home':h,'system':'Linux','architecture':'aarch64','python':[3,12,3],'remote_root_preexisted':False,'remote_codex_home_preexisted':False,'remote_codex_preinstalled':False,'admin_profile_preexisted':False}
            with patch.object(prepare_deployment,'ROOT',root),patch('sys.argv',['prepare_deployment.py','--host','node1']),patch('prepare_deployment.subprocess.run') as remote,patch('builtins.print'):
                remote.return_value.stdout=json.dumps(baseline)
                prepare_deployment.main()
                self.assertEqual((root/'.state/baseline-node1.json').stat().st_mode & 0o777,0o600)
                with self.assertRaises(SystemExit):prepare_deployment.main()
            args=['provision.py','--host','node1','--baseline',str(root/'.state/baseline-node1.json'),'--manifest',str(root/'.state/manifest-node1.json')]
            with patch.object(provision,'ROOT',root),patch('sys.argv',args),patch('provision.subprocess.run') as remote,patch('provision.Path.home',return_value=local_home),patch('builtins.print'):
                provision.main()
                self.assertEqual(remote.call_count,2)
            with tarfile.open(root/'.state/deploy-node1.tar.gz') as archive:
                self.assertEqual(archive.extractfile('.local/share/atlas-codex/projects/atlas/data.txt').read(),b'approved content')
                config=tomllib.loads(archive.extractfile('.codex/config.toml').read().decode())
                self.assertEqual(config['agents']['max_concurrent_threads_per_session'],16)
                self.assertNotIn('settings.json',archive.getnames())
            receipt=json.loads((root/'.state/deployment-node1.json').read_text())
            self.assertEqual(receipt['host'],'node1')

    def test_inventory_excludes_credentials_caches_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data.txt').write_text('data')
            for name in ('.env','private.key','auth.json'):(root/name).write_text('secret')
            (root/'.git').mkdir();(root/'.git/config').write_text('private metadata')
            (root/'alias').symlink_to(root/'data.txt')
            files,excluded=inventory(root)
            self.assertEqual([r['path'] for r in files],['data.txt'])
            self.assertEqual(set(excluded),{'.env','private.key','auth.json','.git','alias'})
            self.assertEqual(snapshot_file(root,files[0]),b'data')
            (root/'data.txt').write_text('changed')
            with self.assertRaises(ValueError):snapshot_file(root,files[0])

    def test_snapshot_rejects_escape_and_symlink_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data.txt').write_text('data');(root/'link').symlink_to(root,target_is_directory=True)
            for path in ('../data.txt','/etc/passwd','link/data.txt'):
                with self.assertRaises(ValueError):snapshot_file(root,{'path':path,'sha256':hashlib.sha256(b'data').hexdigest()})

    def test_layout_and_generated_config_keep_sandbox_controls(self):
        h='/home/example';r=h+'/.local/share/atlas-codex'
        settings={'remote_root':r,'remote_codex':h+'/.local/bin/codex','model':'gpt-6-astra','provider':'mag','credential_env':'AMSC_TOKEN','projects':{'atlas':{'remote_cwd':r+'/projects/atlas'}}}
        validate_layout(settings,h)
        config=tomllib.loads(remote_config(settings))
        self.assertFalse(config['features']['shell_snapshot'])
        self.assertFalse(config['allow_login_shell'])
        self.assertEqual(config['shell_environment_policy']['filters']['AMSC_TOKEN'],'exclude')
        self.assertEqual(config['agents']['max_concurrent_threads_per_session'],16)
        with self.assertRaises(ValueError):validate_layout(settings,'/home/other')


if __name__=='__main__':unittest.main()
