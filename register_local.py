#!/usr/bin/env python3
"""Register the private bridge MCP with an exact, removable config block."""
import hashlib
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    path = Path.home()/'.codex/config.toml'
    original = path.read_text()
    if 'atlas_team' in tomllib.loads(original).get('mcp_servers',{}):
        raise SystemExit('atlas_team already configured; inspect rather than overwrite')
    backup = ROOT/'.state/backups/local-config.registration.toml'
    backup.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if backup.exists(): raise SystemExit('Registration backup already exists; inspect state')
    shutil.copy2(path,backup);backup.chmod(0o600)
    block = '\n\n# BEGIN MULTI-NODE CODEX BRIDGE (managed by register_local.py)\n'
    block += '[mcp_servers.atlas_team]\n'
    block += 'command = '+json.dumps(sys.executable)+'\n'
    block += 'args = '+json.dumps([str(ROOT/'teamctl.py'),'mcp'])+'\n'
    block += 'default_tools_approval_mode = "approve"\nstartup_timeout_sec = 15\ntool_timeout_sec = 60\n'
    block += '# END MULTI-NODE CODEX BRIDGE\n'
    updated = original+block
    tomllib.loads(updated)
    (ROOT/'.state/local-config.block').write_text(block)
    manifest={'before_sha256':hashlib.sha256(original.encode()).hexdigest(),
              'after_sha256':hashlib.sha256(updated.encode()).hexdigest(),
              'path':str(path),'backup':str(backup)}
    (ROOT/'.state/local-config-change.json').write_text(json.dumps(manifest,indent=2)+'\n')
    tmp=path.with_name('config.atlas-tmp.toml')
    with open(tmp,'x') as f: f.write(updated)
    tmp.chmod(path.stat().st_mode & 0o777)
    os.replace(tmp,path)
    print('Registered atlas_team MCP. Existing model, provider and agent settings preserved.')


if __name__ == '__main__': main()
