#!/bin/sh
# recap @VERSION@: offline installer for Linux and macOS.
#   ./install.sh [folder]     default: ~/Applications/recap-@VERSION@ (Mac), ~/.local/share/recap/@VERSION@ (Linux)
# Set RECAP_NO_ICON=1 to skip the desktop icon.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
case "$(uname -s)" in
  Darwin) DEFAULT="$HOME/Applications/recap-@VERSION@" ;;
  *) DEFAULT="${XDG_DATA_HOME:-$HOME/.local/share}/recap/@VERSION@" ;;
esac
TARGET="${1:-$DEFAULT}"
echo "Installing recap @VERSION@ into $TARGET (a few minutes)..."
mkdir -p "$TARGET"
"$HERE/recap-env.sh" -o "$TARGET" -s bash -q

# the pretrained StarDist model, so the first run needs no internet (kept if already there:
# StarDist may have cached it differently, and copying over it fails)
if [ -e "$HOME/.keras/models/StarDist2D/2D_versatile_fluo" ]; then
  echo "StarDist model already in $HOME/.keras/models (kept)"
else
  mkdir -p "$HOME/.keras/models"
  cp -R "$HERE/stardist-models/StarDist2D" "$HOME/.keras/models/"
fi

cat > "$TARGET/recap" <<EOS
#!/bin/bash
# recap launcher: activates the installed environment and runs recap.
. "$TARGET/activate.sh"
export RECAP_LAUNCHER="$TARGET/recap"
exec python -m recap "\$@"
EOS
chmod +x "$TARGET/recap"
"$TARGET/recap" --version
if [ "${RECAP_NO_ICON:-0}" != "1" ]; then "$TARGET/recap" desktop-icon; fi
echo
echo "Done. Open recap from its desktop icon, or run:  $TARGET/recap --help"
echo "Fiji is not included: install it from https://fiji.sc, then run:  $TARGET/recap fiji set <Fiji folder>"
