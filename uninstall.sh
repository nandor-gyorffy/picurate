#!/usr/bin/env bash
# Picurate uninstaller for Linux.
# Removes the desktop launcher, icons, and optionally the catalog/cache data.
# Does NOT delete your photos or this application folder.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Picurate Uninstaller ==="
echo ""

# ── Remove desktop launcher ───────────────────────────────────────────────────
DESKTOP_FILE="$HOME/.local/share/applications/picurate.desktop"
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo "✓ Desktop launcher removed"
else
    echo "  Desktop launcher not found (already removed or never installed)"
fi

# ── Remove icons ──────────────────────────────────────────────────────────────
ICON_DIR="$HOME/.local/share/icons/hicolor"
removed_icons=0
for size in 16 32 48 64 128 256 512; do
    ico="$ICON_DIR/${size}x${size}/apps/picurate.png"
    if [ -f "$ico" ]; then
        rm -f "$ico"
        removed_icons=$((removed_icons + 1))
    fi
done
if [ "$removed_icons" -gt 0 ]; then
    echo "✓ Icons removed ($removed_icons sizes)"
    gtk-update-icon-cache "$ICON_DIR" 2>/dev/null || true
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

# ── Optionally remove catalog data ────────────────────────────────────────────
DATA_DIR="$HOME/.local/share/Picurate"
CACHE_DIR="$HOME/.cache/Picurate"
LOG_DIR="$HOME/.local/state/Picurate"

echo ""
echo "The following directories contain your catalog, thumbnails, and logs:"
[ -d "$DATA_DIR" ]  && echo "  $DATA_DIR"
[ -d "$CACHE_DIR" ] && echo "  $CACHE_DIR"
[ -d "$LOG_DIR" ]   && echo "  $LOG_DIR"
echo ""
read -rp "Delete catalog data and thumbnails? (your photos are NOT affected) [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    [ -d "$DATA_DIR" ]  && rm -rf "$DATA_DIR"  && echo "✓ Removed $DATA_DIR"
    [ -d "$CACHE_DIR" ] && rm -rf "$CACHE_DIR" && echo "✓ Removed $CACHE_DIR"
    [ -d "$LOG_DIR" ]   && rm -rf "$LOG_DIR"   && echo "✓ Removed $LOG_DIR"
else
    echo "  Catalog data kept."
fi

# ── Optionally remove the app folder itself ───────────────────────────────────
echo ""
read -rp "Delete the Picurate application folder ($DIR)? [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    # Must cd out first
    cd "$HOME"
    rm -rf "$DIR"
    echo "✓ Application folder removed."
fi

echo ""
echo "=== Picurate has been uninstalled. ==="
