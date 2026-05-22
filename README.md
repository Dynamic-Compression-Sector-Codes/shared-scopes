# DCS Oscilloscope Control

A multi-scope, multi-channel oscilloscope control application built in Python using PyQt5 and PyVISA. Monitor, arm, trigger, view, and save waveform data from multiple Tektronix oscilloscopes in parallel.

---

## Prerequisites

Before running the application, ensure the following are installed on your Windows machine:

1. **Git** — used to clone the repository and pull updates automatically. Download from [git-scm.com](https://git-scm.com/).
2. **Miniconda** or **Anaconda** — manages the Python environment and dependencies. Download from [docs.anaconda.com/miniconda](https://www.anaconda.com/download/success).
3. **VISA Backend** *(optional, skip for now)* — NI-VISA or Keysight VISA for instrument communication. If no native VISA backend is found, the app falls back to the pure-Python `pyvisa-py` library automatically.

---

## Installation & First Launch

1. **Clone the repository:**
   ```
   git clone https://github.com/Dynamic-Compression-Sector-Codes/shared-scopes/
   cd shared-scopes
   ```

2. **Initialize Conda for your terminal** *(first time only)*:
   ```
   conda init powershell
   ```
   Restart your terminal after running this.

3. **Set up your scope configuration:**
   Copy the included template and fill in your scope IPs:
   ```
   copy scopes.template.json scopes.json
   ```
   Then open `scopes.json` in any text editor and replace the placeholder IPs and scope names with your instruments. See [Scope Configuration](#scope-configuration) below for the full schema.

   > **Back up your `scopes.json`** — this file is excluded from version control (gitignored) and will not be restored by `git pull`. Once configured, save a copy somewhere safe (a network share, cloud folder, or email it to yourself). If the file is lost you will need to re-enter all scope IPs manually.

4. **Launch the application:**
   Double-click **`run-scope-control.bat`** in the repository folder.

That's it. The launcher handles everything else automatically.

### What the Launcher Does

Every time `run-scope-control.bat` is run, it:

- Pulls the latest code from GitHub (`git pull`) so you always have up-to-date software.
- Searches your system for a Miniconda or Anaconda installation.
- Creates the `scope_control` Conda environment if it doesn't exist yet, installing all dependencies from `environment.yml`.
- Detects changes to `environment.yml` on subsequent runs and updates the environment automatically if needed.
- Creates a **ScopeControl shortcut on your Desktop** on the first run — use that shortcut for future launches instead of navigating to the folder each time.
- Activates the environment and starts the application.

---

## Scope Configuration

Scopes, presets, platforms, and archiving settings are stored in a JSON configuration file (default: `scopes.json` in the same directory as the script).

### JSON Schema

```json
{
  "app_title": "My Lab Oscilloscope Control",
  "scopes": {
    "Scope1": {
      "ip": "192.168.1.10",
      "purpose": "Timing Channel"
    },
    "Scope2": {
      "ip": "192.168.1.11",
      "purpose": "Velocity"
    }
  },
  "presets": {
    "Shot Config A": ["Scope1", "Scope2"],
    "Shot Config B": ["Scope1"]
  },
  "platforms": [
    {
      "label": "Gas Gun",
      "code": "1",
      "subdir": "GG-1",
      "dir_type": "gun"
    }
  ],
  "archiving": {
    "enabled": false,
    "engineering_drive": "//server/share/",
    "gun_shot_data_path": "Shot Data",
    "laser_shot_data_path": "Laser/Shot Data",
    "user_data_path": "User_Data"
  }
}
```

**Key fields:**
- `"app_title"` — sets the window title bar text.
- `"scopes"` — dictionary of scope name → `ip` and `purpose`.
- `"presets"` — named groups of scope names for one-click selection.
- `"platforms"` — list of experimental platforms used in archiving mode.
- `"archiving"` — controls the advanced shot-archiving workflow (see Save Modes below).

### Loading a Different Config File

**File → Set Scope Configuration File…** — browse to any `.json` file on your local machine or network. The app reloads immediately.

### Editing the Config

Two options:

- **File → Edit Scope Configuration…** — opens the Scope Manager dialog where you can add, edit, or delete scopes, and save changes back to the active JSON file. Use **Open File…** inside the dialog to open the raw JSON in your default text editor, or **Reload from File** to pick up external edits.
- Edit the JSON file directly in any text editor, then use **Reload from File** in the Scope Manager dialog to apply changes without restarting.

---

## Features & Usage

### Toolbar Buttons

| Button | Action |
|---|---|
| **Update Table** | Queries all selected scopes in parallel (threaded). Pulls trigger state, connection status, record length, sample depth, and waveform byte width. |
| **Select Scopes** | Opens a checklist dialog to activate or deactivate individual scopes. Select a preset from the dropdown to auto-check the matching group. |
| **ARM Scopes** | Arms all selected scopes simultaneously to wait for a trigger event. Confirms before proceeding. |
| **Save Scope Data** | Saves waveform data from all active scopes in parallel background threads. The GUI remains responsive while saving. |
| **Soft Trigger** | Sends a software trigger to all armed scopes. |

### File Menu

| Item | Action |
|---|---|
| **Set Directory…** | Choose the base folder where waveform files are saved. |
| **Set Filename…** | Set a custom base filename for saved waveforms. |
| **Set Scope Configuration File…** | Load a different `.json` config file. |
| **Edit Scope Configuration…** | Open the Scope Manager dialog. |

### Viewing Waveforms

Right-click any row in the scope table → **Show Current Waveform** to open a live plot of that scope's active channels. The plot includes interactive cursors, decimation toggle, and a channel legend.

### Removing a Scope from the Table

Click the **✕** button on the right side of any scope row to remove it from the active set. This does not delete it from the config file.

### Table Columns

| Column | Description |
|---|---|
| Scope | Scope name and purpose |
| Trigger | Current trigger state (Ready, Armed, Triggered, etc.) |
| Conn | Connection status |
| Mode | Acquisition mode |
| IP | IP address |
| Range | Vertical range |
| #Pts | Record length (number of points) |
| Bytes | Bytes per sample (1 = 8-bit, 2 = 12/16-bit) |

Columns are resizable by dragging the header dividers. The Scope column stretches to fill available width.

---

## Save Modes

### Standard Save Mode

Used when *Advanced Shot/Platform Archiving* is **off** (Settings menu).

- Prompts you to choose a directory via **File → Set Directory…**.
- Saves `.isf` waveform files directly to that directory.
- Files are named `{filename}_S{scope#}C{channel}.isf`.

### Advanced Shot/Platform Archiving Mode

Used when `"archiving": { "enabled": true }` is set in the JSON config file. Designed for labs with a structured network archive.

- Select the active **Platform** from the toolbar dropdown.
- The app automatically determines the next shot number based on existing folders in the archive.
- Files are first staged to a user directory, then copied to the central shot archive.
- Shot folders are created automatically; empty folders are not created until the first save.

Archive paths and platform definitions are configured in the `"archiving"` and `"platforms"` sections of the JSON config file.

---

## Troubleshooting

**Scope shows as disconnected / `VI_ERROR_RSRC_NFOUND`**
Check that the IP address in `scopes.json` is correct and the scope is turned on. Scope IP will be visible on startup or through the LAN configuration menu.

**App doesn't start after `git pull`**
The `environment.yml` may have been updated. The launcher will detect this and update the Conda environment automatically — allow it to finish before relaunching.

**Conda not found**
Ensure Miniconda or Anaconda is installed and that `conda.bat` is accessible. The launcher checks several standard installation paths and falls back to searching `PATH`.

**Conda environment creation fails / `conda` not recognized in the launcher**
Run the following once from a terminal after installing Miniconda/Anaconda, then restart your terminal:
```
conda init powershell
```
This registers Conda with PowerShell so the launcher can activate environments correctly.
