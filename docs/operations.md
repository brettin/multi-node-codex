# Start, shut down and restart

Run commands from the checkout registered with the laptop's `atlas_team` MCP server. Do not operate two bridge checkouts against the same remote socket paths. Replace example host aliases with your configured hosts.

## Start

Use a laptop shell with the current `AMSC_TOKEN` from your established authentication mechanism:

```sh
python3 teamctl.py start
python3 teamctl.py connect --host dell1
python3 teamctl.py connect --host dell2
python3 teamctl.py connect --host spark1
python3 teamctl.py call team_status '{"refresh_remote":true}'
```

`start` launches the laptop bridge; `connect` starts each SSH-managed remote app server. Neither starts agent model work. A repeat `start` reports an already-running bridge; it does not refresh that process's environment. `connect` leaves an already-connected host unchanged.

Status should show each intended host connected, no connection error, and no active threads when idle. Keep the laptop awake and online. The bridge starts no login service and has no scheduled work.

## Launch a coordinated task

Have the laptop coordinator read `LAPTOP_COORDINATOR.md`. Review and edit a copy of `examples/dispatch.json`, using a unique run ID, selected host, concrete scope, acceptance criteria and deadline:

```sh
python3 teamctl.py call team_dispatch --file YOUR-REQUEST.json
```

Dispatch to other hosts with the same run ID to share mailboxes, tasks and artifacts. Reusing a completed `(run_id, host)` continues that coordinator's history. A different run ID starts a separate history. Omitted `host` selects `default_host`.

Use host-prefixed worker identities. Poll inboxes at checkpoints and acknowledge handled messages. See `team_status` for each host's coordinator mailbox; `steer=true` sends live updates to an active remote coordinator. Worker and laptop inboxes require polling.

## Shut down cleanly

1. Ask coordinators to checkpoint and finish active tasks. Inspect `team_status` with `refresh_remote=true`.
2. If a coordinator must be interrupted, name both run and host:

   ```sh
   python3 teamctl.py call team_interrupt '{"run_id":"YOUR-RUN","host":"dell2"}'
   python3 teamctl.py call team_status '{"host":"dell2","refresh_remote":true}'
   ```

3. Close one remote connection, or the whole bridge:

   ```sh
   python3 teamctl.py disconnect --host dell2
   # Or, for all hosts and the laptop bridge:
   python3 teamctl.py stop
   ```

`team_interrupt` targets the coordinator; inspect its native children afterwards. Closing its SSH app server stops bridge-managed execution on that host. Local native workers are controlled separately by their laptop coordinator. Detached compute jobs are not managed by the bridge; avoid them in bounded work.

The shared database, messages, artifact revisions and remote session history persist after shutdown. In-flight runs become `disconnected`, not silently completed. Do not delete the SQLite database or replay uncertain tasks.

## Restart one remote server

After checkpointing that host's work:

```sh
python3 teamctl.py disconnect --host dell2
python3 teamctl.py connect --host dell2
python3 teamctl.py call team_status '{"host":"dell2","refresh_remote":true}'
```

Other hosts stay connected. Use this after a remote configuration/binary change or to recover that connection. It reuses the credential already held by the laptop bridge.

## Restart the entire bridge / refresh the provider token

Checkpoint active work first. From a shell containing the refreshed credential:

```sh
python3 teamctl.py stop
python3 teamctl.py start
python3 teamctl.py connect --host dell1
python3 teamctl.py connect --host dell2
python3 teamctl.py connect --host spark1
python3 teamctl.py call team_status '{"refresh_remote":true}'
```

Restarting is required to replace the bridge's inherited token. Reconnecting only the remote servers does not load a changed token from your current shell into an already-running bridge.

## Desktop refresh versus daemon restart versus bridge restart

| Action | What it affects |
| --- | --- |
| Quit and reopen the Codex desktop app | Refreshes the desktop client and its tool discovery; verify whether refreshed tools are needed first |
| `codex app-server daemon restart` | The managed daemon on the machine where that command runs |
| `teamctl.py disconnect/connect --host HOST` | One custom SSH-launched `codex app-server --stdio` |
| `teamctl.py stop/start` plus reconnects | This laptop bridge and its remote app servers |

The daemon command does not manage this bridge's remote servers. `remote_launch.py` receives a credential over SSH stdin and then replaces itself with `codex app-server --stdio` using `exec`. Seeing its path in an SSH command line does not mean a separate long-running Python launcher also needs a restart. See [Architecture](architecture.md).

## Recover after sleep, network loss or an uncertain turn

```sh
python3 teamctl.py status
python3 teamctl.py connect --host dell2
python3 teamctl.py call team_status '{"host":"dell2","refresh_remote":true}'
python3 teamctl.py call team_events '{"run_id":"YOUR-RUN","limit":50}'
```

If SSH authentication expired, open `ssh dell2`, authenticate normally, exit and reconnect. After reconnect, inspect the saved run and its events. Reconcile a disconnected/uncertain coordinator with `team_interrupt` before explicitly continuing it. A failed host blocks new dispatch only on that host. Read unacknowledged inbox messages with `after=0`; a cursor alone does not acknowledge them.

Task claims expire after at most 1800 seconds. This prevents stale completion but does not kill processes. Reconcile expired work and inspect native children rather than blindly retrying.

Logs are in `.state/bridge.log` and `.state/ssh-HOST-stderr.log`. Never paste credentials when reporting errors. If `start` reports a running bridge but it has an outdated environment, use the full stop/start sequence. If a stale PID prevents `stop`, inspect its command and ownership before signaling anything; the CLI deliberately refuses PID mismatches.

## Bounded live validation

Single host:

```sh
python3 tests/live_smoke.py UNIQUE-ID --host dell2 --compute
```

Two-host exchange: run these in separate laptop terminals at the same time, using one fresh shared run ID:

```sh
python3 tests/live_smoke.py UNIQUE-SHARED-ID --host dell2 --peer spark1 --compute
python3 tests/live_smoke.py UNIQUE-SHARED-ID --host spark1 --peer dell2 --compute
```

Each launches one coordinator and one worker. Reports are `.state/RUN-HOST-report.json`. These tests verify native shell credential exclusion, steering, claims, artifact revisions and direct peer exchange; they do not benchmark 64-worker capacity. A timed-out test interrupts its coordinator and requires inspection of remaining child state.
