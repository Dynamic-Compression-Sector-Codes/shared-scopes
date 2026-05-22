#!/usr/bin/env bash
set -eu

ENV_NAME="scope_control"
YAML_FILE="environment-linux.yml"
SCRIPT_NAME="ScopeControl_V3.py"
MARKER_FILE=".conda_env_installed_linux.yml"

echo "==================================================="
echo "            Scope Control Launcher"
echo "==================================================="
echo

# 1. Navigate to the repository root (portable — no realpath needed)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
echo "Active directory: $(pwd)"

# Detect OS
OS="$(uname -s)"

# 2. Pull the latest code
echo "Checking for code updates..."
if ! git pull; then
    echo "WARNING: Git pull failed. You might be offline or have local conflicts."
    echo "Launching using current local files..."
fi
echo

# 3. Find conda
echo "Locating Conda installation..."
CONDA_SH=""

for candidate in \
    "$HOME/miniconda3/etc/profile.d/conda.sh" \
    "$HOME/opt/miniconda3/etc/profile.d/conda.sh" \
    "$HOME/anaconda3/etc/profile.d/conda.sh" \
    "$HOME/opt/anaconda3/etc/profile.d/conda.sh" \
    "$HOME/AppData/Local/miniconda3/etc/profile.d/conda.sh" \
    "/opt/miniconda3/etc/profile.d/conda.sh" \
    "/opt/anaconda3/etc/profile.d/conda.sh" \
    "/usr/local/miniconda3/etc/profile.d/conda.sh" \
    "/usr/local/anaconda3/etc/profile.d/conda.sh" \
    "/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh" \
    "/opt/homebrew/Caskroom/anaconda/base/etc/profile.d/conda.sh"
do
    if [ -f "$candidate" ]; then
        CONDA_SH="$candidate"
        break
    fi
done

# Fallback: ask conda itself where it lives
if [ -z "$CONDA_SH" ]; then
    CONDA_BIN="$(command -v conda 2>/dev/null || true)"
    if [ -n "$CONDA_BIN" ]; then
        CONDA_ROOT="$(conda info --base 2>/dev/null || true)"
        if [ -n "$CONDA_ROOT" ] && [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
            CONDA_SH="$CONDA_ROOT/etc/profile.d/conda.sh"
        fi
    fi
fi

if [ -z "$CONDA_SH" ]; then
    echo "ERROR: Could not find conda.sh."
    echo "Please ensure Miniconda or Anaconda is installed, then run:"
    echo "  conda init bash"
    read -rp "Press Enter to exit..."
    exit 1
fi

echo "Found Conda at: $CONDA_SH"
# shellcheck disable=SC1090
source "$CONDA_SH"
echo

# 4. Verify and configure the Conda environment
echo "Verifying Conda environment '$ENV_NAME'..."

if ! conda env list | grep -q "^$ENV_NAME "; then
    echo "Environment '$ENV_NAME' does not exist. Creating it now..."
    if ! conda env create -n "$ENV_NAME" -f "$YAML_FILE"; then
        echo "ERROR: Failed to create Conda environment."
        read -rp "Press Enter to exit..."
        exit 1
    fi
    cp "$YAML_FILE" "$MARKER_FILE"
else
    echo "Environment '$ENV_NAME' already exists."

    NEEDS_UPDATE=0
    if [ ! -f "$MARKER_FILE" ]; then
        NEEDS_UPDATE=1
    elif ! diff -q "$YAML_FILE" "$MARKER_FILE" > /dev/null 2>&1; then
        NEEDS_UPDATE=1
    fi

    if [ "$NEEDS_UPDATE" -eq 1 ]; then
        echo "Changes detected in '$YAML_FILE'. Updating environment dependencies..."
        if conda env update -n "$ENV_NAME" -f "$YAML_FILE" --prune; then
            cp "$YAML_FILE" "$MARKER_FILE"
            echo "Environment successfully updated."
        else
            echo "WARNING: Environment update encountered errors. Attempting to continue..."
        fi
    else
        echo "Environment dependencies are up to date."
    fi
fi
echo

# 5. Create a launcher shortcut if it doesn't exist
if [ "$OS" = "Linux" ]; then
    DESKTOP_FILE="$HOME/.local/share/applications/ScopeControl.desktop"
    if [ ! -f "$DESKTOP_FILE" ]; then
        echo "Creating desktop shortcut..."
        mkdir -p "$HOME/.local/share/applications"
        ICON_PATH="$SCRIPT_DIR/ui/ScopeICO.png"
        [ -f "$ICON_PATH" ] || ICON_PATH=""
        cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=Scope Control v3
Comment=DCS Oscilloscope Control
Exec=bash $SCRIPT_DIR/run-scope-control.sh
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Science;
EOF
        chmod +x "$DESKTOP_FILE"
        echo "Shortcut created at $DESKTOP_FILE"
        echo
    fi
elif [ "$OS" = "Darwin" ]; then
    APP_DIR="$HOME/Applications/ScopeControl.app"
    if [ ! -d "$APP_DIR" ]; then
        echo "Creating ScopeControl.app in ~/Applications..."
        mkdir -p "$APP_DIR/Contents/MacOS"
        mkdir -p "$APP_DIR/Contents/Resources"
        cat > "$APP_DIR/Contents/MacOS/ScopeControl" <<EOF
#!/usr/bin/env bash
cd "$SCRIPT_DIR"
bash "$SCRIPT_DIR/run-scope-control.sh"
EOF
        chmod +x "$APP_DIR/Contents/MacOS/ScopeControl"
        cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>ScopeControl</string>
    <key>CFBundleExecutable</key><string>ScopeControl</string>
    <key>CFBundleIdentifier</key><string>gov.anl.dcs.scopecontrol</string>
    <key>CFBundleVersion</key><string>3.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
</dict>
</plist>
EOF
        ICON_SRC="$SCRIPT_DIR/ui/ScopeICO.png"
        if command -v sips > /dev/null 2>&1 && [ -f "$ICON_SRC" ]; then
            sips -s format icns "$ICON_SRC" --out "$APP_DIR/Contents/Resources/ScopeControl.icns" > /dev/null 2>&1 || true
        fi
        echo "App created at $APP_DIR — drag to Dock to pin it."
        echo
    fi
fi

# 6. Activate and launch
echo "Activating Conda environment '$ENV_NAME'..."
conda activate "$ENV_NAME"

echo "Launching $SCRIPT_NAME..."
python "$SCRIPT_NAME"

echo
echo "Application closed."
