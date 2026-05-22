#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="scope_control"
YAML_FILE="environment-linux.yml"
SCRIPT_NAME="ScopeControl_V3.py"
MARKER_FILE=".conda_env_installed_linux.yml"
DESKTOP_FILE="$HOME/.local/share/applications/ScopeControl.desktop"

echo "==================================================="
echo "            Scope Control Launcher"
echo "==================================================="
echo

# 1. Navigate to the repository root
cd "$(dirname "$(realpath "$0")")"
echo "Active directory: $(pwd)"

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
    "$HOME/anaconda3/etc/profile.d/conda.sh" \
    "/opt/miniconda3/etc/profile.d/conda.sh" \
    "/opt/anaconda3/etc/profile.d/conda.sh" \
    "/usr/local/miniconda3/etc/profile.d/conda.sh" \
    "/usr/local/anaconda3/etc/profile.d/conda.sh"
do
    if [ -f "$candidate" ]; then
        CONDA_SH="$candidate"
        break
    fi
done

# Fallback: search PATH for conda and derive the profile script
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
    echo "ERROR: Could not find conda.sh. Please ensure Miniconda or Anaconda is installed."
    echo "After installing, run:  conda init bash"
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

# 5. Create a desktop shortcut if it doesn't exist
if [ ! -f "$DESKTOP_FILE" ]; then
    echo "Creating desktop shortcut..."
    mkdir -p "$HOME/.local/share/applications"
    ICON_PATH="$(pwd)/ui/ScopeICO.png"
    [ -f "$ICON_PATH" ] || ICON_PATH=""
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Name=ScopeControl
Comment=DCS Oscilloscope Control
Exec=bash $(realpath "$0")
Icon=$ICON_PATH
Terminal=false
Type=Application
Categories=Science;
EOF
    chmod +x "$DESKTOP_FILE"
    echo "Shortcut created at $DESKTOP_FILE"
    echo
fi

# 6. Activate and launch
echo "Activating Conda environment '$ENV_NAME'..."
conda activate "$ENV_NAME"

echo "Launching $SCRIPT_NAME..."
python "$SCRIPT_NAME"

echo
echo "Application closed."
