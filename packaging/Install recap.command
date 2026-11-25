#!/bin/sh
# Double-click in Finder to install recap.
cd "$(dirname "$0")"
xattr -dr com.apple.quarantine . 2>/dev/null || true   # files copied from a download or a drive
./install.sh
printf "\nPress Enter to close this window."
read -r _
