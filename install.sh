#!/bin/sh
# Install Orbit for this user: the code in ~/.local/share/orbit, an `orbit` command in ~/.local/bin,
# and an "Orbit" entry in the apps menu. No root needed. Run again to update; `./install.sh --uninstall` removes it.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
share="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$share/orbit"
bin="$HOME/.local/bin"
apps="$share/applications"
icons="$share/icons/hicolor/scalable/apps"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$dest" "$bin/orbit" "$apps/orbit.desktop" "$icons/orbit.svg"
    echo "Orbit removed. Your settings are kept in ${XDG_CONFIG_HOME:-$HOME/.config}/orbit (delete that folder to remove them)."
    exit 0
fi

command -v python3 >/dev/null || { echo "Orbit needs python3." >&2; exit 1; }
python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null ||
    echo "Note: for Orbit's own window install python3-gi and gir1.2-webkit2-4.1 (it falls back to a browser window)."

mkdir -p "$dest" "$bin" "$apps" "$icons"
rm -rf "$dest/orbit"
cp -r "$here/orbit" "$dest/orbit"
find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} +
cp "$here/orbit/static/icon.svg" "$icons/orbit.svg"

cat > "$bin/orbit" <<EOF
#!/bin/sh
PYTHONPATH="$dest\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m orbit "\$@"
EOF
chmod +x "$bin/orbit"

cat > "$apps/orbit.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Orbit
Comment=One place for all your apps
Exec=$bin/orbit
Icon=orbit
Terminal=false
Categories=Utility;System;
StartupWMClass=orbit
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true

echo "Orbit installed. Open it from your apps menu, or run: orbit"
