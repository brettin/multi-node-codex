#!/bin/sh
# Run as the remote login user; sudo prompts in the user's terminal.
set -eu
base="$HOME/.local/share/atlas-codex"
source="$base/installers/apparmor/extracted/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
target=/etc/apparmor.d/bwrap-userns-restrict
digest=11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9
printf '%s  %s\n' "$digest" "$source" | sha256sum --check --status
if test -e "$target"; then
  echo 'Target profile already exists; refusing to overwrite it.' >&2
  exit 1
fi
apparmor_parser --skip-kernel-load --skip-cache "$source"
sudo install -m 0644 "$source" "$target"
if ! sudo apparmor_parser -r "$target"; then
  sudo apparmor_parser -R "$target" || true
  sudo rm -- "$target"
  echo 'Profile loading failed; removed the file created by this script.' >&2
  exit 1
fi
printf '%s\n' '{"installed":"/etc/apparmor.d/bwrap-userns-restrict","sha256":"11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9","preexisted":false}' > "$base/admin-profile-change.json"
bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc -- /usr/bin/true
echo 'Ubuntu bubblewrap profile loaded; namespace test passed.'
