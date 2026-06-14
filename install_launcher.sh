#!/usr/bin/env bash
# Install the Picurate desktop launcher for the current user.
# Run once from the project directory: ./install_launcher.sh

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor"

echo "Installing Picurate launcher from: $DIR"

# Write a launcher with the absolute path baked in
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/picurate.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Picurate
GenericName=Photo Organizer
Comment=Local photo organizer — sort by people, places, topics
Exec=bash -c "cd '$DIR' && '$DIR/.venv/bin/python3' '$DIR/main.py'"
Icon=$DIR/assets/icon/picurate.png
Terminal=false
Categories=Graphics;Photography;
Keywords=photos;pictures;organizer;curation;
StartupWMClass=picurate
EOF

# Install icons at standard sizes
for size in 16 32 48 64 128 256 512; do
    src="$DIR/assets/icon/picurate_${size}.png"
    dst="$ICON_DIR/${size}x${size}/apps"
    if [ -f "$src" ]; then
        mkdir -p "$dst"
        cp "$src" "$dst/picurate.png"
    fi
done

# Update icon cache
gtk-update-icon-cache "$ICON_DIR" 2>/dev/null || true
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Done. Picurate should now appear in your applications menu."
echo "You can also pin it to your taskbar/dock."
