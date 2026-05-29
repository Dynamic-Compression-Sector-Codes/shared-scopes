# VOBB Timing Tool (Scope Query Tool)

A lightweight PyQt5 utility for reading inter-channel timing measurements from a single Tektronix oscilloscope and writing the results directly into an Excel template. Designed for VOBB timing shots where four sequential delay measurements (CH1→2, CH2→3, CH3→4, CH1→4) need to be logged into a structured spreadsheet.

---

## Prerequisites

1. **Git** — used to pull updates automatically. Download from [git-scm.com](https://git-scm.com/).
2. **Miniconda** or **Anaconda** — manages the Python environment. Download from [docs.anaconda.com/miniconda](https://www.anaconda.com/download/success).
3. **Microsoft Excel** — required for the auto-detect and xlsx-write features. The tool uses the COM interface to find and write to open workbooks.
4. **VISA Backend** *(optional but recommended)* — NI-VISA or Keysight VISA for instrument communication. Falls back to `pyvisa-py` automatically.

---

## Installation & First Launch

1. **Clone the repository** (skip if already done for Scope Control):
   ```
   git clone https://github.com/Dynamic-Compression-Sector-Codes/shared-scopes/
   cd shared-scopes
   ```

2. **Initialize Conda for your terminal** *(first time only)*:
   ```
   conda init powershell
   ```
   Restart your terminal after running this.

3. **Launch the application:**
   Double-click **`run-scope-query.bat`** in the repository folder.

The launcher creates the `scope_control` Conda environment (shared with Scope Control), installs any additional dependencies (`xlwings`, `pywin32`) if missing, and creates a **"VOBB Timing Tool" shortcut on your Desktop** on the first run.

---

## Usage

### 1. Enter the Scope IP

Type the IP address of the oscilloscope into the **Scope IP** field. The last-used IP is remembered and restored automatically on the next launch.

### 2. Pull Measurements

Click **Pull Measurements**. The tool connects to the scope over TCP/IP and reads MEAS1–MEAS4 (value, unit, and type). Results are displayed in the text area.

Expected configuration on the scope:
- MEAS1: delay CH1 → CH2
- MEAS2: delay CH2 → CH3
- MEAS3: delay CH3 → CH4
- MEAS4: delay CH1 → CH4 (direct)

After a successful pull, the Excel save controls appear.

### 3. Show Waveform *(optional)*

Click **Show Waveform** to fetch and plot all four channels. The plot:
- Shifts all channels so CH1's 50% falling edge is at t = 0.
- Draws vertical lines at each measured delay position with channel colors matching standard Tektronix colors (CH1 yellow, CH2 cyan, CH3 pink, CH4 green).
- Auto-zooms horizontally to the region of interest, using the average inter-channel delay as padding.

### 4. Select and Save to Excel

**Auto-detect:** When Excel is open with 5 or fewer workbooks, the tool scans them automatically for VOBB anchor cells (`VOBB`, `1 to 2`, `2 to 3`, `3 to 4`, `1 to 4`). If exactly one workbook matches, it is selected automatically — no manual selection needed. This runs on startup and after each **Pull Measurements**.

**Manual selection:** Click **Select output .xlsx** to browse. The dialog opens to the last-used directory.

Once a file is selected, the save button updates to show the filename. Click **Save to ...\filename.xlsx** to write values.

#### How values are written

The tool searches the first sheet for anchor cells:

| Anchor | Written to |
|---|---|
| `1 to 2` cell found | Cell immediately to the right |
| `2 to 3` cell found | Cell immediately to the right |
| `3 to 4` cell found | Cell immediately to the right |
| `1 to 4` cell found | Cell immediately to the right |
| `VOBB` cell found (fallback) | 4 cells below, one per channel |

If neither pattern is found, a warning is shown and nothing is written. If target cells already contain data, a confirmation dialog asks before overwriting.

The last-used xlsx path is saved and restored across sessions.

---

## Troubleshooting

**"VOBB timing cells not found in selected .xlsx"**
The selected workbook does not contain the expected anchor text. Open the file and confirm that cells with the text `VOBB`, `1 to 2`, `2 to 3`, `3 to 4`, or `1 to 4` exist on the first sheet.

**Auto-detect does not find the workbook**
Auto-detect is skipped if Excel is not running, or if more than 5 workbooks are open. Use **Select output .xlsx** to set the path manually.

**Scope shows as disconnected / connection timeout**
Verify the IP address is correct and the scope is on. Default timeout is 10 seconds.

**`win32com` or `xlwings` import error**
The launcher installs these automatically, but if you are running `scope_query_tool.py` directly outside the launcher:
```
pip install xlwings pywin32
```

**Conda not found**
Ensure Miniconda or Anaconda is installed. Run `conda init powershell` once from a terminal and restart.
