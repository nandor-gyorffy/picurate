#!/usr/bin/env bash
# Picurate setup script for Linux/macOS.
# Creates a virtual environment, installs all dependencies, and optionally
# installs the desktop launcher so Picurate appears in your app menu.
#
# Usage:  ./setup.sh
#

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Picurate Setup ==="
echo ""

# ── Python check ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.12+ and try again."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    echo "ERROR: Python 3.12+ required (found $PY_VER)."
    exit 1
fi

echo "✓ Python $PY_VER found"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "✓ Virtual environment created at .venv/"
else
    echo "✓ Virtual environment already exists"
fi

source .venv/bin/activate

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "Installing dependencies (this may take a few minutes)..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "✓ Dependencies installed"

# ── Desktop launcher (Linux only) ────────────────────────────────────────────
if [[ "$(uname)" == "Linux" ]]; then
    echo ""
    read -rp "Install desktop launcher (app menu / taskbar)? [Y/n] " REPLY
    REPLY="${REPLY:-Y}"
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        bash "$DIR/install_launcher.sh"
    fi
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To launch Picurate:"
if [[ "$(uname)" == "Linux" ]]; then
    echo "  ./run.sh          (from this folder)"
    echo "  or use the desktop launcher if you installed it"
else
    echo "  open -a Picurate  (if installed)"
    echo "  or: python main.py"
fi
echo ""
