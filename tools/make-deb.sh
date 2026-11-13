#!/bin/sh
# Orbit as a Debian/Ubuntu package: make-deb.sh <program> <version> <amd64|arm64> <out.deb>
#
# The program is the build with its window (it needs WebKitGTK, which the package depends on). The package puts it in
# /usr/bin/orbit, with an apps-menu entry and an icon both named after the window's app id (app.novalabs.orbit, see
# desktop/src/window.rs), so the open window shows with Orbit's name and icon. Orbit installed this way updates itself through apt (see desktop/src/recipes/orbit.rs).
set -e
prog="$1"; version="$2"; arch="$3"; out="$4"
here="$(cd "$(dirname "$0")/.." && pwd)"
root="$(mktemp -d)"
chmod 0755 "$root"
trap 'rm -rf "$root"' EXIT
install -D -m 0755 "$prog" "$root/usr/bin/orbit"
install -D -m 0644 "$here/orbit/static/icon.svg" "$root/usr/share/icons/hicolor/scalable/apps/app.novalabs.orbit.svg"
install -D -m 0644 "$here/desktop/icons/icon.png" "$root/usr/share/icons/hicolor/512x512/apps/app.novalabs.orbit.png"
install -D -m 0644 /dev/stdin "$root/usr/share/applications/app.novalabs.orbit.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Orbit
GenericName=App hub
Comment=One place for YTM Starcast and Nova
Exec=/usr/bin/orbit
Icon=app.novalabs.orbit
Terminal=false
Categories=Utility;System;
StartupWMClass=app.novalabs.orbit
EOF
install -D -m 0644 /dev/stdin "$root/usr/share/doc/orbit/copyright" <<EOF
Orbit by NovaLabs: https://github.com/dash1101/orbit
License: MIT
EOF
size=$(du -sk "$root/usr" | cut -f1)
mkdir -p "$root/DEBIAN"
cat > "$root/DEBIAN/control" <<EOF
Package: orbit
Version: $version
Architecture: $arch
Maintainer: NovaLabs <62864632+dash1101@users.noreply.github.com>
Installed-Size: $size
Depends: libwebkit2gtk-4.1-0, libgtk-3-0
Section: utils
Priority: optional
Homepage: https://github.com/dash1101/orbit
Description: one place for your apps
 Orbit installs, updates and watches YTM Starcast, Nova and any other app you add,
 in a window of its own. It only listens on this computer (127.0.0.1).
EOF
cat > "$root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
EOF
chmod 0755 "$root/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$root" "$out" >/dev/null
echo "built $out ($(du -h "$out" | cut -f1))"
