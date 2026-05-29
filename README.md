# DCS Oscilloscope Tools

Python-based instrumentation tools for Tektronix oscilloscope control and data collection, built with PyQt5 and PyVISA.

---

## Tools

### [Scope Control](scope-control.md)

Multi-scope, multi-channel oscilloscope control for parallel operation across an entire lab setup. Arm, trigger, and save waveforms from multiple scopes simultaneously.

**Launcher:** `run-scope-control.bat`
**Script:** `ScopeControl_V3.py`

---

### [VOBB Timing Tool](scope-query-tool.md)

Single-scope measurement reader for VOBB timing shots. Pulls inter-channel delay measurements (MEAS1–4), plots waveforms with timing markers, and writes results directly into an Excel template.

**Launcher:** `run-scope-query.bat`
**Script:** `scope_query_tool.py`

---

## Shared Setup

Both tools use the same `scope_control` Conda environment and are launched via `.bat` files that handle environment creation and updates automatically.

**First-time setup:**
1. Install [Git](https://git-scm.com/) and [Miniconda](https://www.anaconda.com/download/success).
2. Clone this repository.
3. Run `conda init powershell` from an Anaconda prompt, then restart your terminal.
4. Double-click the `.bat` launcher for whichever tool you need — it handles the rest.

See each tool's documentation for full details.
