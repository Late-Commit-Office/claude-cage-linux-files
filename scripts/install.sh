#!/bin/sh
# Copies the plugin into Cage's plugin folder. Run this on the Mac (not in a
# Cage terminal: the Linux environment cannot see ~/.claude-cage), then open
# Plugins in the Cage sidebar and click Reload.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
profile="${CAGE_PROFILE:-$HOME/.claude-cage}"
if [ ! -d "$profile" ]; then
  echo "No Cage profile at $profile. Run this on the Mac where Cage is installed." >&2
  exit 1
fi
dest="$profile/plugins/linux-files"
mkdir -p "$dest"
rsync -a --delete --exclude '__pycache__' --exclude '.DS_Store' "$here/linux-files/" "$dest/"
echo "Installed linux-files into $dest"
echo "In Cage: sidebar → Plugins → Reload (turn the plugins switch on first if needed)."
