# Change log

## 2026-11-25 — standalone repository

- Exported bridge, CLI, provisioning, registration, rollback, administrator scripts and tests from the verified multi-host setup at source commit `ba9ec3d`.
- Added deployment, lifecycle, architecture and rollback guides, including desktop refresh versus daemon restart versus bridge restart.
- Added example settings/dispatch inputs. Excluded live settings, credentials, snapshots, logs, databases and private backups.
- Added read-only pre-installation inventory to record fresh-host baselines and hash project snapshots. Common credential paths, caches and symlinks are excluded and reported for review.
- Added provisioning checks for supported layout/baselines, project path traversal, symlink ancestors and changed source hashes. First-time MCP registration now creates its private backup directory.
- Generalized coordinator/tool descriptions to configured host aliases. Deployment still supports the pinned MAG provider and initial `atlas` project key.
- The source setup passed bounded live tests on dell1, dell2 and spark1. This repository is a separate distribution; packaging changes have not been applied to those running hosts. Full 64-worker throughput is not load-tested.
- Passed 27 offline tests, including a simulated fresh-host inventory/provisioning flow, plus shell syntax checks and documentation/example validation. No model calls or live installation changes were made for this packaging task.

No provider credentials, model catalog, private coordination history or project snapshots are included. Scripts retain private receipts and backups as described in the guides.
