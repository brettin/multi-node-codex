#!/usr/bin/env python3
"""Copy bridge source, minimal config/catalog and an ATLAS snapshot to one host.

First provisioning only. Refuses to overwrite an existing project or config.
Credentials are never included in the archive. Rerun with --code-only to update
bridge source without touching research or Codex configuration.
"""
import argparse
import hashlib
import io
import json
import shlex
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from atlas_cluster.settings import load_settings, for_host
from prepare_deployment import validate_layout

ROOT = Path(__file__).resolve().parent


def snapshot_file(source, row):
    relative=PurePosixPath(row['path'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Snapshot entries must be relative paths inside the project')
    source=Path(source).resolve(strict=True)
    path=source
    for part in relative.parts:
        path=path/part
        if path.is_symlink():raise ValueError(f'Snapshot refuses symlink: {relative}')
    content=path.read_bytes()
    if hashlib.sha256(content).hexdigest()!=row['sha256']:
        raise ValueError(f'Project changed since inventory: {relative}')
    return content


def remote_config(settings):
    root = settings['remote_root']
    home = str(Path(settings['remote_codex']).parents[2])
    return f'''allow_login_shell = false
model = {json.dumps(settings['model'])}
model_reasoning_effort = "high"
model_context_window = 272000
model_provider = "mag"
model_catalog_json = "{home}/.codex/mag-models.json"
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[agents]
max_concurrent_threads_per_session = 16

[features]
shell_snapshot = false

[shell_environment_policy]
inherit = "core"
ignore_default_excludes = false

[shell_environment_policy.filters]
"AMSC_TOKEN" = "exclude"

[model_providers.mag]
requires_openai_auth = false
name = "AmSC MAG"
base_url = "https://mag.genesis.americansciencecloud.org/v1"
env_key = "AMSC_TOKEN"
wire_api = "responses"
supports_websockets = false
http_headers = {{ "X-AmSC-MAG-Provider" = "openai" }}

[sandbox_workspace_write]
network_access = true

[projects."{root}/projects/atlas"]
trust_level = "trusted"

[mcp_servers.atlas_team]
command = "/usr/bin/python3"
args = ["{root}/code/teamctl.py", "--socket", "{root}/bridge.sock", "mcp", "--worker"]
default_tools_approval_mode = "approve"
startup_timeout_sec = 15
tool_timeout_sec = 60
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-only',action='store_true')
    parser.add_argument('--host',help='Configured host; defaults to dell1')
    parser.add_argument('--manifest',type=Path,default=ROOT/'.state/atlas-baseline.json')
    parser.add_argument('--baseline',type=Path,default=ROOT/'.state/baseline.json')
    args = parser.parse_args()
    settings = for_host(load_settings(ROOT),args.host)
    validate_layout(settings,str(Path(settings['remote_codex']).parents[2]))
    (ROOT/'.state').mkdir(mode=0o700,exist_ok=True)
    remote_root = settings['remote_root']
    preflight = f'test ! -e {shlex.quote(remote_root+"/projects/atlas")} && test ! -e "$HOME/.codex/config.toml"'
    if not args.code_only:
        baseline_meta = json.loads(args.baseline.read_text())
        if baseline_meta.get('host',baseline_meta.get('remote_host')) != settings['host']:
            raise SystemExit('Baseline host does not match target; supply --baseline for this host')
        if any(baseline_meta.get(k,True) for k in ('remote_root_preexisted','remote_codex_home_preexisted','remote_codex_preinstalled')):
            raise SystemExit('Initial provisioning requires the recorded fresh-host baseline')
        if baseline_meta.get('remote_home')!=str(Path(settings['remote_codex']).parents[2]):
            raise SystemExit('Baseline home does not match target settings')
        subprocess.run(['ssh','-o','BatchMode=yes',settings['host'],preflight],check=True)
    archive = ROOT/'.state'/f'deploy-{settings["host"]}.tar.gz'
    files = sorted((ROOT/'atlas_cluster').glob('*.py')) + [ROOT/'teamctl.py']
    def add_bytes(tar,name,data):
        info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600
        tar.addfile(info,io.BytesIO(data))
    with tarfile.open(archive,'w:gz') as tar:
        for f in files:
            tar.add(f,arcname='.local/share/atlas-codex/code/'+str(f.relative_to(ROOT)),recursive=False)
        if not args.code_only:
            add_bytes(tar,'.codex/config.toml',remote_config(settings).encode())
            tar.add(Path.home()/'.codex/mag-models.json',arcname='.codex/mag-models.json',recursive=False)
            source = Path(settings['projects']['atlas']['source'])
            baseline = json.loads(args.manifest.read_text())
            for row in baseline:
                content = snapshot_file(source,row)
                add_bytes(tar,'.local/share/atlas-codex/projects/atlas/'+row['path'],content)
            tar.add(args.manifest,arcname='.local/share/atlas-codex/atlas-baseline.json',recursive=False)
            tar.add(args.baseline,arcname='.local/share/atlas-codex/setup-baseline.json',recursive=False)
    archive.chmod(0o600)
    with archive.open('rb') as data:
        subprocess.run(['ssh','-o','BatchMode=yes',settings['host'],
                        'umask 077; tar -xz -C "$HOME" && chmod 700 "$HOME/.codex" "$HOME/.local/share/atlas-codex"'],stdin=data,check=True)
    report = {'host':settings['host'],'deployed':'code' if args.code_only else 'code, minimal config, catalog, ATLAS snapshot',
              'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    (ROOT/'.state'/f'deployment-{settings["host"]}.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__': main()
