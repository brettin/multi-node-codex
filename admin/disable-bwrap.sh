#!/bin/sh
# Undo only the profile added by enable-bwrap.sh; no global sysctl changes.
set -eu
base="$HOME/.local/share/atlas-codex"
target=/etc/apparmor.d/bwrap-userns-restrict
test -f "$base/admin-profile-change.json"
printf '%s  %s\n' 11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9 "$target" | sha256sum --check --status
sudo apparmor_parser -R "$target"
sudo rm -- "$target"
mv "$base/admin-profile-change.json" "$base/admin-profile-change.undone.json"
echo 'Removed only the bubblewrap AppArmor profile added by this setup.'
