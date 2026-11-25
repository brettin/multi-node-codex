# Backups, rollback and recovery

Rollback is separate from routine shutdown. `teamctl.py stop` leaves everything installed. `rollback.py` removes selected setup entries and archives corresponding installations.

## Records to retain

Keep these private and outside Git:

- `settings.json`, source commit, original laptop config and exact MCP registration block;
- `.state/baseline-HOST.json`, snapshot manifests/exclusions and deployment receipts;
- `.state/local-config.block`, `.state/local-config-change.json` and registration backup;
- a consistent SQLite backup of `.state/team.sqlite3`;
- each remote's baseline, administrator marker/scripts, Codex home and project snapshot.

Use SQLite's backup API, rather than copying a live database without its WAL:

```sh
python3 - <<'PY'
import datetime,sqlite3
from pathlib import Path
folder=Path('.state/backups');folder.mkdir(mode=0o700,parents=True,exist_ok=True)
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
source=sqlite3.connect('.state/team.sqlite3')
path=folder/('team-'+stamp+'.sqlite3')
destination=sqlite3.connect(path)
source.backup(destination)
destination.close();source.close();path.chmod(0o600)
print(path)
PY
```

Checkpoint and stop before coordinating a whole-installation backup or restoring files. Back up current state before recovery and reconcile work performed since the backup.

## Remove extra hosts

Preview without changing anything:

```sh
python3 rollback.py --host dell2 --host spark1
```

Checkpoint active work and stop the bridge. Run administrator undo only on hosts where this setup installed the profile and its marker still exists:

```sh
python3 teamctl.py stop
ssh -t dell2 'sh "$HOME/.local/share/atlas-codex/admin/disable-bwrap.sh"'
ssh -t spark1 'sh "$HOME/.local/share/atlas-codex/admin/disable-bwrap.sh"'
python3 rollback.py --host dell2 --host spark1 --apply
python3 teamctl.py start
python3 teamctl.py connect --host dell1
```

Use one `--host` to remove one extra machine. Targeted removal preserves the default host, its legacy mailbox, the laptop MCP registration and all shared history. Removing only the default host is refused while other hosts remain; plan that routing migration separately or uninstall all hosts.

Selected installations move into `REMOTE_HOME/.local/share/atlas-codex-undo-TIMESTAMP/`, with `codex-home/` and `setup/` directories. Known launcher symlinks are removed. Remote research and histories remain in the archives. Selected host entries are removed from settings. Original settings and completed remote archive records are retained in `.state/backups/rollback-TIMESTAMP/`.

The scripts check baseline ownership, expected launcher and administrator marker. Changed or unknown installations require review. Administrator undo verifies the profile hash before unloading/removing it; main rollback refuses while the marker remains.

## Full uninstall

```sh
python3 rollback.py
```

The preview lists all configured hosts and administrator undo commands. Stop the bridge, undo profiles installed by this setup, then:

```sh
python3 rollback.py --apply
```

Full uninstall archives all configured remote installations and removes only the exact managed laptop MCP block. A modified block causes refusal. Local source, project files, shared database and audit records remain. Reopen the desktop app if needed to refresh tool discovery.

## Partial rollback or restore

Do not blindly repeat `--apply` after an error. Inspect `.state/backups/rollback-TIMESTAMP/remote-results.json` to see which hosts were archived. Verify remote paths/settings before finishing or reversing it. Keep every archive until the restored system is validated.

To restore an archived remote installation, ensure its original paths are unoccupied, restore `codex-home` and `setup` to their original locations, recreate the original launcher symlink, and restore the saved host entry. Re-enable the recorded administrator profile if required. Restore MCP registration only if full uninstall removed it. Reconnect and inspect saved runs before resuming. There is no automatic destructive restore command.

For an interrupted initial deployment, preserve the original baseline and archive partial files before a reviewed retry. Do not regenerate a baseline that loses the pre-installation facts.
