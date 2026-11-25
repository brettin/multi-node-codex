# Multi-node Codex over SSH

Run a main coordinator on a laptop and a separate coordinator with native subagents on each remote Linux machine. A Python bridge shares durable mailboxes, exclusive task claims and versioned text artifacts across the teams. Canonical project files stay on the laptop.

With the example three remote hosts and a separately configured 16-worker laptop limit, capacity is **64 workers plus four coordinators**. This is a group of cooperating native sessions. Model calls still use the configured provider and share its quotas.

## Documentation

| Task | Guide |
| --- | --- |
| Deploy from a fresh clone onto new machines | [Deployment](docs/deployment.md) |
| Start, check status, shut down, restart, refresh credentials | [Operations](docs/operations.md) |
| Understand processes, communication and security boundaries | [Architecture](docs/architecture.md) |
| Undo deployment, back up state and recover | [Rollback and recovery](docs/rollback.md) |
| Direct the laptop coordinator | [Coordinator instructions](LAPTOP_COORDINATOR.md) |
| Understand what changed in this distribution | [Change log](CHANGELOG.md) |

## Supported configuration

- Laptop: macOS or Linux, Python 3.11+, OpenSSH with Unix-socket forwarding, and a configured Codex installation.
- Remote: Linux, Python 3.11+, OpenSSH server and a working Codex sandbox. Live validation used Ubuntu 24.04 on ARM64.
- Codex: **0.166.0-alpha.6**, pinned because the experimental App Server protocol can change.
- Provider: **AmSC MAG**, with `AMSC_TOKEN` supplied in the laptop bridge's environment, and a local `~/.codex/mag-models.json` catalog. This distribution provisions that provider; other authentication/provider arrangements need adaptation.
- Initial snapshot key: `atlas`. It can point to your chosen project folder. Initial deployment uses the documented standard home-relative layout. Additional projects can be configured afterwards.

No Python packages need installing: runtime and tests use the standard library. No cloud scheduler, PBS or Slurm integration is included. The bridge does not run at login and starts no research automatically.

## Quick reference after deployment

Run from this repository's checkout, using a shell with the existing provider credential:

```sh
python3 teamctl.py start
python3 teamctl.py connect --host dell1
python3 teamctl.py connect --host dell2
python3 teamctl.py connect --host spark1
python3 teamctl.py call team_status '{"refresh_remote":true}'
```

To close all bridge-managed remote servers:

```sh
python3 teamctl.py stop
```

See [Operations](docs/operations.md) before stopping an active run. Native laptop workers are controlled by their own coordinator. Keep the laptop awake while using the bridge.

## Repository contents

- `atlas_cluster/`: SSH App Server transport, multi-host routing, SQLite coordination and MCP adapter.
- `teamctl.py`: lifecycle commands and tool-call CLI.
- `prepare_deployment.py`: read-only remote preflight and private snapshot/baseline generation.
- `provision.py`: initial config/code/snapshot deployment or code-only updates.
- `register_local.py`: exact, removable laptop MCP registration.
- `rollback.py`, `admin/`: conservative uninstall and Ubuntu sandbox profile undo.
- `settings.example.json`, `examples/`: non-secret configuration and dispatch examples.
- `tests/`: offline regression tests and an explicitly invoked live smoke test.

Copy `settings.example.json` to `settings.json` and edit it following the deployment guide. `settings.json`, `.state/`, credentials and local environment files are ignored by Git. Private runtime state and project snapshots are not distributed.

## Validation

```sh
python3 -m unittest discover -s tests -v
```

Tests cover cross-host routing, isolation, persistence, claims, artifact revisions, conservative rollback and deployment input handling. Live tests make provider calls and spawn a bounded native worker; their commands are in the operations guide. The predecessor installation passed live tests on dell1, dell2 and spark1. This fresh repository adds deployment packaging and offline checks; it has not redeployed those running hosts. Full 64-worker throughput has not been load-tested.

Protocol references: [App Server](https://learn.chatgpt.com/docs/app-server), [MCP](https://learn.chatgpt.com/docs/extend/mcp), [sandboxing](https://learn.chatgpt.com/docs/sandboxing), and [security cookbook](https://learn.chatgpt.com/docs/enterprise/security-cookbook).
