import sys
from pathlib import Path
import numpy as np
import pyvisa as visa
import win32com.client
import xlwings as xw
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import QSettings
import pyqtgraph as pg

_ICON_PATH   = Path(__file__).parent / "ui" / "scope_icon.ico"
_MANUAL_PATH = Path(__file__).parent / "ui" / "manual_query.html"

_VERSION    = "1.0"
_WRITE_DATE = "2026-05-29"


SETTINGS_ORG = "DCS"
SETTINGS_APP = "VOBB Measurement Tool"
KEY_IP   = "last_ip"
KEY_XLSX = "last_xlsx"

_TIME_MEAS_PREFIXES = ('DEL', 'PHAS')
_MEAS_COLORS = ['#ff4444', '#00e5ff', '#76ff03', '#ff9800', '#e040fb']

# Standard Tektronix channel colors
_CH_COLORS = {
    'CH1': '#ffff00',
    'CH2': '#00bcd4',
    'CH3': '#ff4081',
    'CH4': '#69f0ae',
}
_CHANNELS = ['CH1', 'CH2', 'CH3', 'CH4']


def _find_falling_50pct(t, v):
    """
    Average the first 1000 points as the high-state reference.
    Return the interpolated time where v first crosses below 50% of that reference.
    """
    n_ref = min(1000, len(v))
    ref_high = float(np.mean(v[:n_ref]))
    threshold = ref_high * 0.5

    below = v < threshold
    idxs = np.where(below)[0]
    if len(idxs) == 0:
        return float(t[0])
    idx = idxs[0]
    if idx == 0:
        return float(t[0])
    dv = float(v[idx]) - float(v[idx - 1])
    if dv == 0:
        return float(t[idx])
    frac = (threshold - float(v[idx - 1])) / dv
    return float(t[idx - 1]) + frac * float(t[idx] - t[idx - 1])


def _fetch_waveform(scope, channel):
    """
    Returns (t, v) numpy arrays in physical units (seconds, volts).
    Also returns a dict of the WFMPRE scale factors for diagnostics.
    """
    scope.write(f'DATA:SOURCE {channel}')
    # header 0: suppress keyword prefixes in responses
    # rib: signed big-endian binary; byt_n 1: 1 byte/pt (faster transfer)
    scope.write('header 0; wfmo:byt_n 1; data:encdg rib; data:start 1; data:stop 1000000000')

    # Batch preamble in one round-trip; fall back to individual queries if unsupported
    try:
        meta = scope.query('wfmo:ymult?;yoff?;yzero?;xincr?;xzero?;pt_off?').strip()
        y_mult, y_off, y_zero, x_incr, x_zero, x_off = (float(v) for v in meta.split(';'))
    except Exception:
        y_mult = float(scope.query('wfmo:ymult?'))
        y_off  = float(scope.query('wfmo:yoff?'))
        y_zero = float(scope.query('wfmo:yzero?'))
        x_incr = float(scope.query('wfmo:xincr?'))
        x_zero = float(scope.query('wfmo:xzero?'))
        x_off  = float(scope.query('wfmo:pt_off?'))

    wfid = scope.query('wfmo:wfid?').strip()

    # query_binary_values handles the IEEE block header automatically
    samples = np.array(scope.query_binary_values(
        'CURV?', datatype='b', is_big_endian=True, chunk_size=10_485_760
    ))

    # pt_off is the trigger point index (1-based); accounts for pre-trigger samples
    t = x_zero + x_incr * (np.arange(len(samples)) + 1 - x_off)
    v = (samples.astype(float) - y_off) * y_mult + y_zero

    info = {
        'wfid': wfid, 'pts': len(samples),
        'xincr': x_incr, 'xzero': x_zero, 'xoff': x_off,
        'ymult': y_mult, 'yoff': y_off, 'yzero': y_zero,
    }
    return t, v, info


class WaveformWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Waveform View")
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        self.pw = pg.PlotWidget()
        self.pw.setLabel('left', 'Voltage', units='V')
        self.pw.setLabel('bottom', 'Time', units='s')
        self.pw.showGrid(x=True, y=True, alpha=0.3)
        self.pw.addLegend()
        layout.addWidget(self.pw)
        self.resize(800, 500)

    def plot(self, channel_data, measurements):
        """
        channel_data: dict {channel: (t, v)}
        measurements: list of measurement dicts from _pull_measurements
        """
        self.pw.clear()
        self.pw.addLegend()

        if not channel_data:
            return

        # Use CH1 for t=0 reference; fall back to first available channel
        ref_ch = next((ch for ch in _CHANNELS if ch in channel_data), None)
        t_ref, v_ref = channel_data[ref_ch]
        ref_time = _find_falling_50pct(t_ref, v_ref)

        # Plot all channels shifted to the common t=0
        for ch in _CHANNELS:
            if ch not in channel_data:
                continue
            t, v = channel_data[ch]
            col = _CH_COLORS.get(ch, 'w')
            self.pw.plot(t - ref_time, v, pen=pg.mkPen(col, width=1), name=ch)

        v_min = float(np.min(v_ref))
        v_max = float(np.max(v_ref))
        v_span = v_max - v_min or 1.0

        # t=0 dashed yellow line (matches CH1)
        col0 = _CH_COLORS['CH1']
        self.pw.addItem(pg.InfiniteLine(
            pos=0.0, angle=90,
            pen=pg.mkPen(col0, width=1.5, style=QtCore.Qt.DashLine),
        ))
        lbl_zero = pg.TextItem("t=0  (50% fall CH1)", color=col0, anchor=(0.0, 1.0))
        lbl_zero.setPos(0.0, v_max)
        self.pw.addItem(lbl_zero)

        # Measurements are sequential inter-channel delays:
        #   MEAS1 = 1→2  →  line at val1            (CH2 color)
        #   MEAS2 = 2→3  →  line at val1 + val2     (CH3 color)
        #   MEAS3 = 3→4  →  line at val1+val2+val3  (CH4 color)
        #   MEAS4 = 1→4  →  line at val4 raw        (CH4 color, dot-dash)
        raw_vals = {}
        raw_units = {}
        for m in measurements:
            try:
                raw_vals[m['slot']] = float(m['value'])
                raw_units[m['slot']] = m['unit']
            except (ValueError, KeyError):
                pass

        v1 = raw_vals.get(1, 0.0)
        v2 = raw_vals.get(2, 0.0)
        v3 = raw_vals.get(3, 0.0)

        line_defs = [
            # (slot, position, color, dash_style, label)
            (1, v1,           _CH_COLORS['CH2'], QtCore.Qt.DashLine,
                f"CH2  (1→2)\n{v1:.4e} {raw_units.get(1,'')}"),
            (2, v1 + v2,      _CH_COLORS['CH3'], QtCore.Qt.DashLine,
                f"CH3  (1→3)\n{v1+v2:.4e} {raw_units.get(2,'')}\n(Δ {v2:.4e})"),
            (3, v1+v2+v3,     _CH_COLORS['CH4'], QtCore.Qt.DashLine,
                f"CH4  (1→4 cum)\n{v1+v2+v3:.4e} {raw_units.get(3,'')}\n(Δ {v3:.4e})"),
            (4, raw_vals.get(4), _CH_COLORS['CH4'], QtCore.Qt.DotLine,
                f"CH4  (1→4 direct)\n{raw_vals.get(4, 0):.4e} {raw_units.get(4,'')}"),
        ]

        for i, (slot, pos, col, dash, label) in enumerate(line_defs):
            if pos is None or slot not in raw_vals:
                continue
            self.pw.addItem(pg.InfiniteLine(
                pos=pos, angle=90,
                pen=pg.mkPen(col, width=1.5, style=dash),
            ))
            lbl = pg.TextItem(label, color=col, anchor=(0.0, 0.0))
            lbl.setPos(pos, v_min + v_span * (0.12 + i * 0.18))
            self.pw.addItem(lbl)

        # Zoom x-axis to region of interest with average inter-channel delay as padding
        x_positions = [0.0]
        if 1 in raw_vals: x_positions.append(v1)
        if 2 in raw_vals: x_positions.append(v1 + v2)
        if 3 in raw_vals: x_positions.append(v1 + v2 + v3)
        if 4 in raw_vals: x_positions.append(raw_vals[4])
        seq_delays = [d for slot, d in [(1, v1), (2, v2), (3, v3)] if slot in raw_vals]
        pad = sum(seq_delays) / len(seq_delays) if seq_delays else abs(max(x_positions) - min(x_positions)) * 0.1
        self.pw.setXRange(min(x_positions) - pad, max(x_positions) + pad, padding=0)

        self.show()
        self.raise_()


class ScopeQueryTool(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scope Query Tool")
        self.settings = QSettings(QSettings.NativeFormat, QSettings.UserScope, SETTINGS_ORG, SETTINGS_APP)
        self._measurements = []
        self._waveform_win = None
        self._output_path = None
        self._build_ui()
        self._restore_settings()
        self._auto_detect_xlsx()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        ip_row = QtWidgets.QHBoxLayout()
        ip_row.addWidget(QtWidgets.QLabel("Scope IP:"))
        self.ip_edit = QtWidgets.QLineEdit()
        self.ip_edit.setPlaceholderText("e.g. 10.54.235.87")
        ip_row.addWidget(self.ip_edit)
        layout.addLayout(ip_row)

        btn_row = QtWidgets.QHBoxLayout()
        self.meas_btn = QtWidgets.QPushButton("Pull Measurements")
        self.meas_btn.clicked.connect(self._pull_measurements)
        btn_row.addWidget(self.meas_btn)
        self.wave_btn = QtWidgets.QPushButton("Show Waveform")
        self.wave_btn.clicked.connect(self._show_waveform)
        btn_row.addWidget(self.wave_btn)
        layout.addLayout(btn_row)

        # xlsx row — hidden until measurements have been pulled
        self._xlsx_row = QtWidgets.QWidget()
        xlsx_layout = QtWidgets.QHBoxLayout(self._xlsx_row)
        xlsx_layout.setContentsMargins(0, 0, 0, 0)
        self.select_xlsx_btn = QtWidgets.QPushButton("Select output .xlsx")
        self.select_xlsx_btn.clicked.connect(self._select_output_xlsx)
        xlsx_layout.addWidget(self.select_xlsx_btn)
        self.save_xlsx_btn = QtWidgets.QPushButton("Save to output .xlsx")
        self.save_xlsx_btn.setEnabled(False)
        self.save_xlsx_btn.clicked.connect(self._save_to_xlsx)
        xlsx_layout.addWidget(self.save_xlsx_btn)
        self._xlsx_row.setVisible(False)
        layout.addWidget(self._xlsx_row)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(100)
        layout.addWidget(self.output)

        self.resize(400, 250)

        # Help menu
        menu_bar  = self.menuBar()
        help_menu = menu_bar.addMenu("Help")
        manual_action = QtWidgets.QAction("Manual", self)
        manual_action.triggered.connect(self._open_manual)
        help_menu.addAction(manual_action)
        about_action = QtWidgets.QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _restore_settings(self):
        self.ip_edit.setText(self.settings.value(KEY_IP, ""))
        saved_xlsx = self.settings.value(KEY_XLSX, "")
        if saved_xlsx and Path(saved_xlsx).exists():
            self._set_output_path(saved_xlsx)

    def _save_settings(self):
        self.settings.setValue(KEY_IP, self.ip_edit.text().strip())

    def _open_scope(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            self.output.setPlainText("Enter an IP address first.")
            return None, None
        self._save_settings()
        rm = visa.ResourceManager()
        scope = rm.open_resource(f"TCPIP::{ip}::INSTR")
        scope.timeout = 10000
        return rm, scope

    def _pull_measurements(self):
        rm, scope = self._open_scope()
        if scope is None:
            return
        self.output.setPlainText("Querying measurements...")
        QtWidgets.QApplication.processEvents()
        try:
            lines = [f"Connected: {scope.query('*IDN?').strip()}", ""]
            self._measurements = []
            for slot in range(1, 5):
                try:
                    val   = scope.query(f"MEASUrement:MEAS{slot}:VALue?").strip()
                    unit  = scope.query(f"MEASUrement:MEAS{slot}:UNIts?").strip()
                    mtype = scope.query(f"MEASUrement:MEAS{slot}:TYPe?").strip()
                    self._measurements.append({'slot': slot, 'mtype': mtype, 'value': val, 'unit': unit})
                    lines.append(f"MEAS{slot} ({mtype}): {val} {unit}")
                except Exception as e:
                    lines.append(f"MEAS{slot}: error — {e}")
            scope.close()
            self.output.setPlainText("\n".join(lines))
            self._xlsx_row.setVisible(True)
            self._auto_detect_xlsx()
        except Exception as e:
            self.output.appendPlainText(f"\nError:\n{e}")
        finally:
            rm.close()

    def _show_waveform(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            self.wave_btn.setText("No IP!")
            QtCore.QTimer.singleShot(1500, lambda: self.wave_btn.setText("Show Waveform"))
            return

        self._save_settings()
        self.wave_btn.setEnabled(False)
        rm = visa.ResourceManager()
        try:
            scope = rm.open_resource(f"TCPIP::{ip}::INSTR")
            scope.timeout = 10000
            channel_data = {}

            for ch in _CHANNELS:
                self.wave_btn.setText(f"Pulling {ch}...")
                QtWidgets.QApplication.processEvents()
                try:
                    t, v, _info = _fetch_waveform(scope, ch)
                    channel_data[ch] = (t, v)
                except Exception:
                    pass

            scope.close()

            if self._waveform_win is None:
                self._waveform_win = WaveformWindow(self)
            self._waveform_win.plot(channel_data, self._measurements)

        except Exception as e:
            self.output.appendPlainText(f"\nWaveform error:\n{e}")
        finally:
            rm.close()
            self.wave_btn.setText("Show Waveform")
            self.wave_btn.setEnabled(True)

    def _set_output_path(self, path):
        self._output_path = path
        self.settings.setValue(KEY_XLSX, path)
        name = Path(path).name
        self.save_xlsx_btn.setText(f"Save to ...\\{name}")
        self.save_xlsx_btn.setEnabled(True)

    def _auto_detect_xlsx(self):
        _ANCHORS = ['VOBB', '1 to 2', '2 to 3', '3 to 4', '1 to 4']
        try:
            xl = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            return  # Excel not running

        wbs = list(xl.Workbooks)
        if not wbs or len(wbs) > 5:
            return

        matches = []
        for wb in wbs:
            found = False
            for ws in wb.Worksheets:
                for anchor in _ANCHORS:
                    if ws.Cells.Find(What=anchor) is not None:
                        found = True
                        break
                if found:
                    break
            if found:
                try:
                    matches.append(wb.FullName)
                except Exception:
                    pass

        if len(matches) == 1:
            self._set_output_path(matches[0])

    def _select_output_xlsx(self):
        start_dir = str(Path(self._output_path).parent) if self._output_path else str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select output file", start_dir,
            "Excel files (*.xlsx *.xlsm *.xls)"
        )
        if not path:
            return
        self._set_output_path(path)

    def _save_to_xlsx(self):
        path = self._output_path
        if not path:
            return
        try:
            if Path(path).exists():
                wb = xw.Book(path)
            else:
                wb = xw.Book()
                wb.save(path)

            ws = wb.sheets[0]

            _LABELS = ['1 to 2', '2 to 3', '3 to 4', '1 to 4']
            _SLOTS  = [1, 2, 3, 4]

            def find_cell(target):
                used = ws.used_range
                vals = used.value
                if vals is None:
                    return None
                if not isinstance(vals, list):
                    vals = [[vals]]
                elif vals and not isinstance(vals[0], list):
                    vals = [vals]
                t = str(target).strip()
                for r, row in enumerate(vals):
                    for c, v in enumerate(row):
                        if v is not None and str(v).strip() == t:
                            return ws.range((used.row + r, used.column + c))
                return None

            def meas_val(slot):
                m = next((m for m in self._measurements if m['slot'] == slot), None)
                if m is None:
                    return None
                try:
                    return float(m['value'])
                except (ValueError, TypeError):
                    return m['value']

            label_cells = {lbl: find_cell(lbl) for lbl in _LABELS}
            found_labels = {lbl: c for lbl, c in label_cells.items() if c is not None}
            vobb_cell = find_cell('VOBB')

            if found_labels:
                target_cells = [
                    found_labels[lbl].offset(0, 1)
                    for lbl in _LABELS if lbl in found_labels
                ]
            elif vobb_cell is not None:
                target_cells = [vobb_cell.offset(i, 0) for i in range(1, 5)]
            else:
                QtWidgets.QMessageBox.warning(
                    self, "Cells not found",
                    "VOBB timing cells not found in selected .xlsx"
                )
                return

            # Confirm before overwriting any cell that already has data
            occupied = [c for c in target_cells if c.value is not None]
            if occupied:
                reply = QtWidgets.QMessageBox.question(
                    self, "Overwrite existing data?",
                    f"{len(occupied)} cell(s) already contain data. Overwrite?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if reply != QtWidgets.QMessageBox.Yes:
                    return

            # Write values into target cells in slot order
            slots_for_targets = (
                [s for lbl, s in zip(_LABELS, _SLOTS) if lbl in found_labels]
                if found_labels else _SLOTS
            )
            for cell, slot in zip(target_cells, slots_for_targets):
                val = meas_val(slot)
                if val is not None:
                    cell.value = val

            wb.save()

            original = self.save_xlsx_btn.text()
            self.save_xlsx_btn.setText("Saved!")
            QtCore.QTimer.singleShot(1500, lambda: self.save_xlsx_btn.setText(original))

        except Exception as e:
            self.output.appendPlainText(f"\nXLSX save error: {e}")

    def _open_manual(self):
        if not _MANUAL_PATH.is_file():
            QtWidgets.QMessageBox.warning(self, "Manual not found",
                f"Could not locate manual at:\n{_MANUAL_PATH}")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("VOBB Timing Tool — Manual")
        dlg.resize(860, 660)
        browser = QtWidgets.QTextBrowser(dlg)
        browser.setOpenExternalLinks(True)
        browser.setSource(QtCore.QUrl.fromLocalFile(str(_MANUAL_PATH)))
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(browser)
        dlg.exec_()

    def _show_about(self):
        QtWidgets.QMessageBox.about(
            self, "About VOBB Timing Tool",
            f"<b>VOBB Timing Tool</b><br>"
            f"Version {_VERSION}<br>"
            f"Written {_WRITE_DATE}<br><br>"
            f"Reads inter-channel delay measurements (MEAS1–4) from a Tektronix "
            f"oscilloscope over TCP/IP and writes results into an Excel template.<br><br>"
            f"DCS — Dynamic Compression Sector"
        )

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    if _ICON_PATH.exists():
        app.setWindowIcon(QtGui.QIcon(str(_ICON_PATH)))
    win = ScopeQueryTool()
    win.show()
    sys.exit(app.exec_())
