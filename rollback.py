#!/usr/bin/env python3
"""Preview rollback; --host removes selected extra hosts, omission removes all.

Remote work is archived, never deleted. The shared database is always retained.
"""
import argparse
import datetime
import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from atlas_cluster.settings import load_settings

ROOT = Path(__file__).resolve().parent


def remove_block(current, block):
    if current.count(block) != 1:
        raise ValueError('Managed block changed or is missing. Inspect config manually; no automatic overwrite.')
    result = current.replace(block, '', 1)
    tomllib.loads(result)
    return result


def remaining_settings(settings, hosts):
    unknown = set(hosts) - set(settings['hosts'])
    if unknown: raise ValueError(f'Unknown hosts: {sorted(unknown)}')
    remaining = {h:v for h,v in settings['hosts'].items() if h not in hosts}
    if remaining and settings['default_host'] not in remaining:
        raise ValueError('Keep the default host to preserve legacy mailbox routing, or uninstall all hosts.')
    return {**settings, 'hosts':remaining}


def remote_program(host, apply=False):
    # Preflight is rerun before archival. Unknown installations are untouched.
    return f'''from pathlib import Path
import datetime,json
h=Path.home();root=h/'.local/share/atlas-codex'
if (root/'admin-profile-change.json').exists():raise SystemExit('Run the administrator undo script first')
marker=root/'setup-baseline.json'
if not marker.exists():raise SystemExit('Remote baseline missing; refusing to archive unknown installation')
baseline=json.loads(marker.read_text())
if baseline.get('host',baseline.get('remote_host')) != {host!r}:raise SystemExit('Baseline host mismatch')
if baseline['remote_codex_home_preexisted'] or baseline['remote_root_preexisted']:raise SystemExit('Baseline does not permit this rollback')
link=h/'.local/bin/codex'
expected=h/'.codex/packages/standalone/current/bin/codex'
if not link.is_symlink() or link.readlink()!=expected:raise SystemExit('Codex launcher changed; refusing to remove it')
if not {apply!r}:
 print(json.dumps({{'host':{host!r},'preflight':'ok'}}))
else:
 archive=h/'.local/share'/('atlas-codex-undo-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
 archive.mkdir(mode=0o700)
 link.unlink()
 codex=h/'.codex'
 if codex.exists():codex.rename(archive/'codex-home')
 root.rename(archive/'setup')
 print(json.dumps({{'host':{host!r},'remote_archive':str(archive)}}))
'''


def ssh(host, program):
    result = subprocess.run(['ssh','-o','BatchMode=yes',host,'python3 -c '+shlex.quote(program)],
                            text=True,capture_output=True)
    if result.returncode:
        raise SystemExit(f'{host}: {result.stderr.strip()}. Check retained rollback records before retrying.')
    return json.loads(result.stdout)


def replace_file(path, content):
    tmp = path.with_name(path.name+'.atlas-rollback-tmp')
    with open(tmp,'x') as f: f.write(content)
    tmp.chmod(path.stat().st_mode & 0o777)
    os.replace(tmp,path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--host',action='append',help='Extra host to remove; repeat to remove multiple hosts')
    args = parser.parse_args()
    settings = load_settings(ROOT)
    hosts = list(dict.fromkeys(args.host or settings['hosts']))
    remaining = remaining_settings(settings,hosts)
    full = not remaining['hosts']
    plan = {
        'hosts':hosts,
        'stop':'Stop the laptop bridge and all its SSH app servers; restart retained hosts afterwards',
        'local_config':'Remove only the exact atlas_team block' if full else 'Keep the atlas_team MCP registration; remove only selected hosts from settings.json',
        'remote':'Archive selected hosts\' ~/.codex and ~/.local/share/atlas-codex; remove only the known installed launcher symlinks',
        'preserved':'All local source, backups, shared task database, research and remaining hosts',
        'admin_profile':['ssh -t '+h+' \'sh "$HOME/.local/share/atlas-codex/admin/disable-bwrap.sh"\'' for h in hosts],
        'order':'Stop the bridge, run the listed administrator undo commands, then rerun this command with --apply'
    }
    print(json.dumps(plan,indent=2),flush=True)
    if not args.apply: return
    for host in hosts: ssh(host,remote_program(host))
    cfg = Path.home()/'.codex/config.toml'
    if full:
        current = cfg.read_text()
        updated = remove_block(current,(ROOT/'.state/local-config.block').read_text())
    subprocess.run([sys.executable,str(ROOT/'teamctl.py'),'stop'],check=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT/'.state/backups'/('rollback-'+stamp)
    backup.mkdir(mode=0o700,parents=True)
    (backup/'settings.before.json').write_text((ROOT/'settings.json').read_text())
    results=[]
    for host in hosts:
        results.append(ssh(host,remote_program(host,True)))
        (backup/'remote-results.json').write_text(json.dumps(results,indent=2)+'\n')
    if full:
        (backup/'local-config.before.toml').write_text(current)
        (backup/'local-config.before.toml').chmod(0o600)
        replace_file(cfg,updated)
    else:
        replace_file(ROOT/'settings.json',json.dumps(remaining,indent=2)+'\n')
    (backup/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps({'rollback':'complete','archives':results,'backups':str(backup)},indent=2))
    print('Shared history is retained. '+('Reload Codex to remove the MCP tools.' if full else 'Restart the bridge to use retained hosts.'))


if __name__ == '__main__': main()
