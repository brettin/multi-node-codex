# Deploy from a fresh clone

This workflow provisions new remote installations without replacing existing ones. For the already-running dell1/dell2/spark1 installation, use its existing checkout and operations guide; do not register a second bridge against the same hosts or sockets.

## 1. Prepare the laptop

Clone this repository into its permanent location. MCP registration records absolute paths, so moving the checkout later requires updating the registration.

Install Python 3.11+ and Git, have Codex configured for MAG, and confirm that `~/.codex/config.toml` and `~/.codex/mag-models.json` exist. Supply `AMSC_TOKEN` through your established authentication mechanism. Do not put its value in this repository, command arguments or a dispatch file.

```sh
python3 --version
python3 -m unittest discover -s tests -v
cp settings.example.json settings.json
```

Edit `settings.json`:

- Replace `/home/REMOTE_USER` with each SSH user's actual home directory.
- Replace `/ABSOLUTE/PATH/TO/PROJECT` with the canonical laptop project folder.
- Keep, remove or add host entries using existing SSH aliases. Set `default_host` to one of them.
- Keep the initial remote layout at `REMOTE_HOME/.local/share/atlas-codex`, the launcher at `REMOTE_HOME/.local/bin/codex`, and the `atlas` snapshot at `REMOTE_HOME/.local/share/atlas-codex/projects/atlas`. The helper validates these paths.
- Preserve `provider: mag` and `credential_env: AMSC_TOKEN` for this distribution.

Configure the laptop's native worker limit separately in your existing Codex setup. Remote configs created here set `agents.max_concurrent_threads_per_session = 16`. Do not enlarge limits by launching extra independent coordinators on a host.

## 2. Check SSH and record the fresh-host baseline

The examples below use `dell2`. Repeat all per-host steps for each configured alias.

```sh
DEPLOY_HOST=dell2
ssh "$DEPLOY_HOST"
# Authenticate normally if needed, then exit back to the laptop.
```

From the laptop:

```sh
ssh -o BatchMode=yes "$DEPLOY_HOST" 'python3 --version; uname -sm'
python3 prepare_deployment.py --host "$DEPLOY_HOST"
```

Run this **before installing Codex or creating the remote setup directory**. It makes only read-only SSH calls and writes these private local inputs:

- `.state/baseline-HOST.json`: remote home/platform and what existed before installation.
- `.state/manifest-HOST.json`: project paths, sizes and SHA-256 hashes.
- `.state/excluded-HOST.json`: paths omitted from the snapshot.

It refuses an existing Codex installation or setup root, and refuses to overwrite earlier inputs. Keep the original baseline for rollback. Review the manifest/exclusions before transfer. Common credential names, Git metadata, runtime state, dependency caches and symlinks are excluded; filename filtering cannot identify every possible secret. Snapshot only a project directory you intend to send to these trusted computers.

If a remote already has Codex or a setup directory, stop here and plan an explicit migration with backups. Do not manufacture a “fresh” baseline or delete existing work to bypass the check.

## 3. Install the pinned remote Codex

Create the remote staging area and retain the recorded baseline immediately:

```sh
ssh "$DEPLOY_HOST" 'umask 077; mkdir -p "$HOME/.local/share/atlas-codex/installers"'
scp ".state/baseline-$DEPLOY_HOST.json" "$DEPLOY_HOST:.local/share/atlas-codex/setup-baseline.json"
```

Download and verify the official installer on that remote:

```sh
ssh -T "$DEPLOY_HOST" 'sh -s' <<'REMOTE_SCRIPT'
set -eu
umask 077
base="$HOME/.local/share/atlas-codex"
curl -fsSL https://chatgpt.com/codex/install.sh -o "$base/installers/install.sh"
printf '%s  %s\n' 44ac252c1b9a68c02bdf0de8e1c6cbfde36fc1afa8684624962eae7d4c461e70 "$base/installers/install.sh" | sha256sum --check --status
PATH="$HOME/.local/bin:$PATH" sh "$base/installers/install.sh" --release 0.166.0-alpha.6
"$HOME/.local/bin/codex" --version
REMOTE_SCRIPT
```

Expected version: `codex-cli 0.166.0-alpha.6`. The checksum pins the installer reviewed for the original deployment. If the official installer changes or the pinned release is unavailable, stop and review/update the pinned inputs rather than omitting verification. Prepending the launcher directory to PATH for this invocation avoids installer changes to shell startup files.

## 4. Deploy code, configuration and the snapshot

From the laptop:

```sh
python3 provision.py --host "$DEPLOY_HOST" \
  --baseline ".state/baseline-$DEPLOY_HOST.json" \
  --manifest ".state/manifest-$DEPLOY_HOST.json"
```

This refuses an existing project snapshot or remote `config.toml`, checks every source hash again, and deploys:

- the bridge code under `~/.local/share/atlas-codex/code/`;
- a minimal `~/.codex/config.toml` and your model catalog;
- the project snapshot and its hash manifest;
- the original per-host baseline for rollback.

The deployment archive contains no provider token. Its hash and result are recorded in `.state/deployment-HOST.json`. Retain this receipt with the original manifest. If copying fails partway through, inspect the receipt/remote files and archive the partial installation before retrying; do not overwrite the original baseline.

Verify snapshot hashes from the laptop:

```sh
ssh -T "$DEPLOY_HOST" 'python3 -' <<'REMOTE_PY'
import hashlib,json
from pathlib import Path
root=Path.home()/'.local/share/atlas-codex'
rows=json.loads((root/'atlas-baseline.json').read_text())
bad=[r['path'] for r in rows if hashlib.sha256((root/'projects/atlas'/r['path']).read_bytes()).hexdigest()!=r['sha256']]
assert not bad,bad
print('Verified',len(rows),'snapshot files')
REMOTE_PY
```

## 5. Verify the Ubuntu sandbox

First try the harmless namespace probe:

```sh
ssh "$DEPLOY_HOST" 'bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc -- /usr/bin/true'
```

On the tested Ubuntu 24.04 machines, `bubblewrap` and `apparmor_parser` were already installed, but the unprivileged namespace restriction required Ubuntu's bubblewrap AppArmor profile. If these packages are missing, have the machine administrator install the distribution's prerequisites first.

If the namespace probe fails with the same AppArmor restriction, stage the profile without installing the profile package system-wide:

```sh
ssh -T "$DEPLOY_HOST" 'sh -s' <<'REMOTE_SCRIPT'
set -eu
umask 077
base="$HOME/.local/share/atlas-codex"
mkdir -p "$base/installers/apparmor" "$base/admin"
cd "$base/installers/apparmor"
apt download apparmor-profiles
dpkg-deb -x ./apparmor-profiles_*.deb extracted
profile=extracted/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
printf '%s  %s\n' 11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9 "$profile" | sha256sum --check --status
apparmor_parser --skip-kernel-load --skip-cache "$profile"
REMOTE_SCRIPT
scp admin/enable-bwrap.sh admin/disable-bwrap.sh "$DEPLOY_HOST:.local/share/atlas-codex/admin/"
ssh -t "$DEPLOY_HOST" 'sh "$HOME/.local/share/atlas-codex/admin/enable-bwrap.sh"'
```

Enter the sudo password in your terminal. If the profile checksum differs, review the changed Ubuntu profile before using it; the scripts intentionally pin the reviewed version. If the target system profile already exists, verify the existing administrator setup instead of overwriting it. The script installs only `/etc/apparmor.d/bwrap-userns-restrict`, loads it and records an undo marker. It does not disable the system-wide namespace restriction.

## 6. Register the laptop MCP server and validate

After all intended hosts are provisioned:

```sh
python3 register_local.py
python3 teamctl.py start
python3 teamctl.py connect --host dell1
python3 teamctl.py connect --host dell2
python3 teamctl.py connect --host spark1
python3 teamctl.py call team_status '{"refresh_remote":true}'
```

Use only hosts present in your settings. Registration appends one marked block, backs up the original config and preserves existing model/provider settings. It refuses a second `atlas_team` registration. If that registration already points to a working bridge, keep using that checkout; moving to this repository is a separate migration.

Check that Codex exposes `team_dispatch` and `team_interrupt` with a `host` argument. If this chat already has them, no refresh is needed. Otherwise quit and reopen the desktop app, then check the tools again. This refresh is separate from remote-server lifecycle. CLI calls are available immediately.

Run one explicitly bounded live validation per new host:

```sh
python3 tests/live_smoke.py YOUR-UNIQUE-SMOKE-ID --host dell2 --compute
```

It uses the configured model/provider, performs a read-only Python computation, and launches one native worker. Check the resulting `.state/YOUR-UNIQUE-SMOKE-ID-dell2-report.json` and `team_status`. Do not claim full-capacity validation from this small smoke test.

## Updating code or adding projects

Before a code update, checkpoint active work and stop the bridge. Back up each remote's existing `code/` directory and the laptop source/settings/state. Then:

```sh
python3 provision.py --host dell2 --code-only
```

Repeat for other intended hosts, restart the bridge and reconnect. Code-only deployment preserves remote config and research. Changing the pinned Codex version requires protocol/compatibility testing before updating the other hosts.

For another project, copy it to a separate remote folder, add its `source` and `remote_cwd` under each host's `projects`, add the trusted path in the remote Codex config, and restart affected services. Pass its project key to `team_dispatch`. Initial `provision.py` remains specific to the `atlas` snapshot key. Preserve each project's canonical laptop copy and review remote changes before importing them.
