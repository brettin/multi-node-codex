#!/usr/bin/env python3
"""Inventory a local project and record a fresh remote baseline before installation.

Writes private deployment inputs locally. SSH is read-only; it installs nothing.
Inspect the manifest and exclusions before provisioning any project files.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from atlas_cluster.settings import load_settings, for_host

ROOT = Path(__file__).resolve().parent
EXCLUDED = {'.git','.codex','.ssh','.aws','.state','.venv','venv','node_modules','__pycache__'}
PATTERNS = ('.env','.env.*','*.pem','*.key','*.p12','*.pfx','credentials','credentials.*','auth.json','secrets','secrets.*')


def inventory(source):
    source = Path(source).resolve(strict=True)
    if not source.is_dir(): raise ValueError('Project source must be a directory')
    files, excluded = [], []
    for directory, dirs, names in os.walk(source, followlinks=False):
        for entry in sorted(dirs+names):
            path=Path(directory)/entry
            rel=path.relative_to(source).as_posix()
            skip=path.is_symlink() or entry in EXCLUDED or any(fnmatch.fnmatch(entry.lower(),p) for p in PATTERNS)
            if skip:
                excluded.append(rel)
                if entry in dirs: dirs.remove(entry)
            elif path.is_file():
                digest=hashlib.sha256()
                with path.open('rb') as f:
                    while chunk:=f.read(1048576): digest.update(chunk)
                files.append({'path':rel,'bytes':path.stat().st_size,'sha256':digest.hexdigest()})
    return sorted(files,key=lambda r:r['path']),sorted(excluded)


def validate_layout(settings, remote_home):
    home=PurePosixPath(remote_home)
    root=home/'.local/share/atlas-codex'
    expected={'remote_root':str(root),'remote_codex':str(home/'.local/bin/codex')}
    for key,value in expected.items():
        if settings[key]!=value: raise ValueError(f'{key} must be {value} for this provisioning workflow')
    if settings['projects']['atlas']['remote_cwd']!=str(root/'projects/atlas'):
        raise ValueError('The initial atlas snapshot must use REMOTE_HOME/.local/share/atlas-codex/projects/atlas')
    if settings['provider']!='mag' or settings['credential_env']!='AMSC_TOKEN':
        raise ValueError('This distribution provisions the MAG provider using AMSC_TOKEN')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    args=parser.parse_args()
    settings=for_host(load_settings(ROOT),args.host)
    state=ROOT/'.state'
    state.mkdir(mode=0o700,exist_ok=True)
    baseline_path=state/f'baseline-{args.host}.json'
    manifest_path=state/f'manifest-{args.host}.json'
    excluded_path=state/f'excluded-{args.host}.json'
    if any(p.exists() for p in (baseline_path,manifest_path,excluded_path)):
        raise SystemExit('Deployment inputs already exist. Preserve/review them instead of overwriting the baseline.')
    probe='''import json,platform,shutil,sys
from pathlib import Path
h=Path.home();root=h/'.local/share/atlas-codex';link=h/'.local/bin/codex'
print(json.dumps({'remote_home':str(h),'system':platform.system(),'architecture':platform.machine(),'python':list(sys.version_info[:3]),'remote_root_preexisted':root.exists() or root.is_symlink(),'remote_codex_home_preexisted':(h/'.codex').exists() or (h/'.codex').is_symlink(),'remote_codex_preinstalled':bool(shutil.which('codex') or link.exists() or link.is_symlink()),'admin_profile_preexisted':Path('/etc/apparmor.d/bwrap-userns-restrict').exists()}))
'''
    result=subprocess.run(['ssh','-o','BatchMode=yes',args.host,'python3 -c '+shlex.quote(probe)],check=True,capture_output=True,text=True)
    baseline=json.loads(result.stdout)
    validate_layout(settings,baseline['remote_home'])
    if baseline['system']!='Linux' or baseline['python'][:2]<[3,11]:
        raise SystemExit('Remote requires Linux and Python 3.11 or newer')
    if any(baseline[k] for k in ('remote_root_preexisted','remote_codex_home_preexisted','remote_codex_preinstalled')):
        raise SystemExit('Remote installation already exists. This fresh-host workflow refuses to replace it; see docs/deployment.md.')
    files,excluded=inventory(settings['projects']['atlas']['source'])
    if not files: raise SystemExit('Project snapshot is empty')
    baseline.update({'host':args.host,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'remote_root':settings['remote_root'],'codex_version':'0.166.0-alpha.6'})
    for path,data in ((baseline_path,baseline),(manifest_path,files),(excluded_path,excluded)):
        with path.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
        path.chmod(0o600)
    print(json.dumps({'baseline':str(baseline_path),'manifest':str(manifest_path),'exclusions':str(excluded_path),'files':len(files),'bytes':sum(r['bytes'] for r in files)},indent=2))


if __name__=='__main__':main()
