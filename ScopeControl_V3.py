# V3: Scope list, presets, platforms, and archiving config loaded from scopes.json.
#     File menu to load alternate configs. Scope Manager dialog to add/edit/delete scopes.
#     Preset dropdown in scope selector. Dynamic platform combo. Per-scope channel count.
#     Archiving toggle disables shot-directory logic for use in external labs.
# Based on V2_4 architecture (threaded queries, QMainWindow, pyqtgraph waveform viewer).

import ctypes
import sys
import time
import json
import threading
import numpy as np
import pyvisa as visa
import os
from pathlib import Path
import datetime
import re
from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtWidgets import QMainWindow
import shutil
import pyqtgraph as pg
from functools import partial
from pyqtgraph.graphicsItems.ViewBox.ViewBox import ViewBox
from typing import List, Tuple, Optional
from scipy.signal import decimate, resample_poly
from PyQt5.QtCore import QRunnable, QObject, pyqtSignal, pyqtSlot, QThreadPool

scope_connection_diagnostic_outputs = False


# ---------------------------------------------------------------------------
# Scope selector dialog (quick add/remove from active set, with preset load)
# ---------------------------------------------------------------------------
class SelectScopesDialog(QtWidgets.QDialog):
    def __init__(self, all_scopes, all_scopes_purpose, selected, presets=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Scopes")
        self.all_scopes = all_scopes
        self.all_scopes_purpose = all_scopes_purpose
        self.selected = set(selected)
        self.presets = presets or {}
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        if self.presets:
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Load Preset:"))
            self.preset_combo = QtWidgets.QComboBox()
            self.preset_combo.addItem("-- select --")
            for name in self.presets:
                self.preset_combo.addItem(name)
            self.preset_combo.currentIndexChanged.connect(self._apply_preset)
            row.addWidget(self.preset_combo)
            layout.addLayout(row)

        tbl = QtWidgets.QTableWidget(self)
        tbl.setColumnCount(4)
        tbl.setHorizontalHeaderLabels(["Scope", "Purpose", "IP", ""])
        tbl.verticalHeader().setVisible(False)
        tbl.setRowCount(len(self.all_scopes))

        for r, (name, ip) in enumerate(self.all_scopes.items()):
            item_name = QtWidgets.QTableWidgetItem(name)
            item_name.setFlags(item_name.flags() & ~QtCore.Qt.ItemIsEditable)
            item_purpose = QtWidgets.QTableWidgetItem(self.all_scopes_purpose.get(name, ""))
            item_purpose.setFlags(item_purpose.flags() & ~QtCore.Qt.ItemIsEditable)
            item_ip = QtWidgets.QTableWidgetItem(ip)
            item_ip.setFlags(item_ip.flags() & ~QtCore.Qt.ItemIsEditable)
            tbl.setItem(r, 0, item_name)
            tbl.setItem(r, 1, item_purpose)
            tbl.setItem(r, 2, item_ip)
            btn = QtWidgets.QPushButton("Add " + name, self)
            btn.setEnabled(name not in self.selected)
            btn.clicked.connect(partial(self._add_scope, name))
            tbl.setCellWidget(r, 3, btn)

        tbl.resizeColumnsToContents()
        tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(tbl)
        self.table = tbl
        self.resize(500, 800)

    def _apply_preset(self, index):
        if index == 0:
            return
        preset_name = self.preset_combo.currentText()
        self.selected = set(self.presets.get(preset_name, [])) & set(self.all_scopes.keys())
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 0).text()
            btn = self.table.cellWidget(r, 3)
            if btn:
                btn.setEnabled(name not in self.selected)

    def _add_scope(self, name):
        self.selected.add(name)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                self.table.cellWidget(r, 3).setEnabled(False)
                break

    def exec_(self):
        super().exec_()
        return self.selected


# ---------------------------------------------------------------------------
# Scope Manager dialog — add / edit / delete scope definitions, save to JSON
# ---------------------------------------------------------------------------
class ScopeManagerDialog(QtWidgets.QDialog):
    def __init__(self, config, config_path='', parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scope Manager")
        self.config = config
        self.config_path = config_path
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        self.tbl = QtWidgets.QTableWidget()
        self.tbl.setColumnCount(3)
        self.tbl.setHorizontalHeaderLabels(["Name", "IP", "Purpose"])
        self.tbl.verticalHeader().setVisible(False)

        scopes = self.config.get("scopes", {})
        self.tbl.setRowCount(len(scopes))
        for r, (name, info) in enumerate(scopes.items()):
            self.tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
            self.tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(info.get("ip", "")))
            self.tbl.setItem(r, 2, QtWidgets.QTableWidgetItem(info.get("purpose", "")))

        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.tbl)

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add Row")
        add_btn.clicked.connect(self._add_row)
        del_btn = QtWidgets.QPushButton("Delete Selected")
        del_btn.clicked.connect(self._del_row)
        open_btn = QtWidgets.QPushButton("Open File…")
        open_btn.setToolTip(self.config_path or "No file loaded")
        open_btn.setEnabled(bool(self.config_path))
        open_btn.clicked.connect(self._open_file)
        reload_btn = QtWidgets.QPushButton("Reload from File")
        reload_btn.setToolTip("Discard edits and reload from disk")
        reload_btn.setEnabled(bool(self.config_path))
        reload_btn.clicked.connect(self._reload_file)
        save_btn = QtWidgets.QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        settings_btn = QtWidgets.QPushButton("Settings…")
        settings_btn.setToolTip("Edit app title, presets, platforms, and archiving settings")
        settings_btn.clicked.connect(self._open_settings)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(open_btn)
        btn_row.addWidget(reload_btn)
        btn_row.addWidget(settings_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self.resize(650, 700)

    def _open_file(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.config_path))

    def _reload_file(self):
        try:
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Reload Failed", str(e))
            return
        scopes = self.config.get("scopes", {})
        self.tbl.setRowCount(0)
        self.tbl.setRowCount(len(scopes))
        for r, (name, info) in enumerate(scopes.items()):
            self.tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
            self.tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(info.get("ip", "")))
            self.tbl.setItem(r, 2, QtWidgets.QTableWidgetItem(info.get("purpose", "")))

    def _add_row(self):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QtWidgets.QTableWidgetItem("ScopeNew"))
        self.tbl.setItem(r, 1, QtWidgets.QTableWidgetItem("0.0.0.0"))
        self.tbl.setItem(r, 2, QtWidgets.QTableWidgetItem(""))

    def _del_row(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    def _open_settings(self):
        dlg = ConfigSettingsDialog(self.config, parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._extra_config = dlg.get_updated_config()

    def get_updated_scopes(self):
        scopes = {}
        for r in range(self.tbl.rowCount()):
            name = (self.tbl.item(r, 0) or QtWidgets.QTableWidgetItem("")).text().strip()
            ip   = (self.tbl.item(r, 1) or QtWidgets.QTableWidgetItem("")).text().strip()
            purp = (self.tbl.item(r, 2) or QtWidgets.QTableWidgetItem("")).text().strip()
            if name:
                scopes[name] = {"ip": ip, "purpose": purp}
        return scopes

    def get_extra_config(self):
        return getattr(self, '_extra_config', {})


# ---------------------------------------------------------------------------
# Config settings dialog — edit app_title, presets, platforms, archiving
# ---------------------------------------------------------------------------
class ConfigSettingsDialog(QtWidgets.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration Settings")
        self.config = config
        self._build_ui()
        self.resize(620, 520)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()

        # ── General ──────────────────────────────────────────────────────────
        gen = QtWidgets.QWidget()
        gl = QtWidgets.QFormLayout(gen)
        gl.setContentsMargins(12, 12, 12, 12)
        self.title_edit = QtWidgets.QLineEdit(self.config.get("app_title", ""))
        gl.addRow("App Title:", self.title_edit)
        tabs.addTab(gen, "General")

        # ── Presets ───────────────────────────────────────────────────────────
        pre = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pre)
        self.presets_tbl = QtWidgets.QTableWidget()
        self.presets_tbl.setColumnCount(2)
        self.presets_tbl.setHorizontalHeaderLabels(["Preset Name", "Scopes (comma-separated)"])
        self.presets_tbl.verticalHeader().setVisible(False)
        presets = self.config.get("presets", {})
        self.presets_tbl.setRowCount(len(presets))
        for r, (name, scopes) in enumerate(presets.items()):
            self.presets_tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(name))
            self.presets_tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(", ".join(scopes)))
        self.presets_tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        pl.addWidget(self.presets_tbl)
        pr = QtWidgets.QHBoxLayout()
        add_p = QtWidgets.QPushButton("Add")
        add_p.clicked.connect(lambda: self._add_row(self.presets_tbl, ["New Preset", ""]))
        del_p = QtWidgets.QPushButton("Delete Selected")
        del_p.clicked.connect(lambda: self._del_row(self.presets_tbl))
        pr.addWidget(add_p); pr.addWidget(del_p); pr.addStretch()
        pl.addLayout(pr)
        tabs.addTab(pre, "Presets")

        # ── Platforms ─────────────────────────────────────────────────────────
        pla = QtWidgets.QWidget()
        ptl = QtWidgets.QVBoxLayout(pla)
        self.platforms_tbl = QtWidgets.QTableWidget()
        self.platforms_tbl.setColumnCount(4)
        self.platforms_tbl.setHorizontalHeaderLabels(["Label", "Code", "Subdir", "Type"])
        self.platforms_tbl.verticalHeader().setVisible(False)
        platforms = self.config.get("platforms", [])
        self.platforms_tbl.setRowCount(len(platforms))
        for r, p in enumerate(platforms):
            self.platforms_tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(p.get("label",    "")))
            self.platforms_tbl.setItem(r, 1, QtWidgets.QTableWidgetItem(p.get("code",     "")))
            self.platforms_tbl.setItem(r, 2, QtWidgets.QTableWidgetItem(p.get("subdir",   "")))
            self.platforms_tbl.setItem(r, 3, QtWidgets.QTableWidgetItem(p.get("dir_type", "gun")))
        self.platforms_tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        ptl.addWidget(self.platforms_tbl)
        type_note = QtWidgets.QLabel("Type must be 'gun' or 'laser'")
        type_note.setStyleSheet("color: gray; font-size: 11px;")
        ptl.addWidget(type_note)
        ptr = QtWidgets.QHBoxLayout()
        add_pt = QtWidgets.QPushButton("Add")
        add_pt.clicked.connect(lambda: self._add_row(self.platforms_tbl, ["New Platform", "", "", "gun"]))
        del_pt = QtWidgets.QPushButton("Delete Selected")
        del_pt.clicked.connect(lambda: self._del_row(self.platforms_tbl))
        ptr.addWidget(add_pt); ptr.addWidget(del_pt); ptr.addStretch()
        ptl.addLayout(ptr)
        tabs.addTab(pla, "Platforms")

        # ── Archiving ─────────────────────────────────────────────────────────
        arc = QtWidgets.QWidget()
        al = QtWidgets.QFormLayout(arc)
        al.setContentsMargins(12, 12, 12, 12)
        arch = self.config.get("archiving", {})
        self.arch_enabled = QtWidgets.QCheckBox()
        self.arch_enabled.setChecked(arch.get("enabled", False))
        al.addRow("Enabled:", self.arch_enabled)
        self.arch_drive = QtWidgets.QLineEdit(arch.get("engineering_drive",    ""))
        al.addRow("Engineering Drive:", self.arch_drive)
        self.arch_gun   = QtWidgets.QLineEdit(arch.get("gun_shot_data_path",   ""))
        al.addRow("Gun Shot Data Path:", self.arch_gun)
        self.arch_laser = QtWidgets.QLineEdit(arch.get("laser_shot_data_path", ""))
        al.addRow("Laser Shot Data Path:", self.arch_laser)
        self.arch_user  = QtWidgets.QLineEdit(arch.get("user_data_path",       ""))
        al.addRow("User Data Path:", self.arch_user)
        tabs.addTab(arc, "Archiving")

        layout.addWidget(tabs)

        btns = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    @staticmethod
    def _add_row(tbl, defaults):
        r = tbl.rowCount()
        tbl.insertRow(r)
        for c, val in enumerate(defaults):
            tbl.setItem(r, c, QtWidgets.QTableWidgetItem(val))

    @staticmethod
    def _del_row(tbl):
        r = tbl.currentRow()
        if r >= 0:
            tbl.removeRow(r)

    def get_updated_config(self):
        presets = {}
        for r in range(self.presets_tbl.rowCount()):
            name = (self.presets_tbl.item(r, 0) or QtWidgets.QTableWidgetItem("")).text().strip()
            raw  = (self.presets_tbl.item(r, 1) or QtWidgets.QTableWidgetItem("")).text()
            if name:
                presets[name] = [s.strip() for s in raw.split(",") if s.strip()]

        platforms = []
        for r in range(self.platforms_tbl.rowCount()):
            label = (self.platforms_tbl.item(r, 0) or QtWidgets.QTableWidgetItem("")).text().strip()
            code  = (self.platforms_tbl.item(r, 1) or QtWidgets.QTableWidgetItem("")).text().strip()
            subdir= (self.platforms_tbl.item(r, 2) or QtWidgets.QTableWidgetItem("")).text().strip()
            dtype = (self.platforms_tbl.item(r, 3) or QtWidgets.QTableWidgetItem("gun")).text().strip()
            if label:
                platforms.append({"label": label, "code": code, "subdir": subdir, "dir_type": dtype})

        return {
            "app_title": self.title_edit.text().strip(),
            "presets":   presets,
            "platforms": platforms,
            "archiving": {
                "enabled":               self.arch_enabled.isChecked(),
                "engineering_drive":     self.arch_drive.text().strip(),
                "gun_shot_data_path":    self.arch_gun.text().strip(),
                "laser_shot_data_path":  self.arch_laser.text().strip(),
                "user_data_path":        self.arch_user.text().strip(),
            },
        }


# ---------------------------------------------------------------------------
# Worker signals
# ---------------------------------------------------------------------------
class WorkerSignals(QObject):
    started  = pyqtSignal(str)
    finished = pyqtSignal(str, dict)
    error    = pyqtSignal(str, Exception)


# ---------------------------------------------------------------------------
# TektronixScope — waveform acquisition helper (unchanged from V2_4)
# ---------------------------------------------------------------------------
class TektronixScope:
    def __init__(self, ip):
        self.ip = ip
        try:
            self.rm = visa.ResourceManager()
        except Exception:
            self.rm = visa.ResourceManager('@py')
            
        if '.dll' not in repr(self.rm):
            print('NI-VISA not found, using pyvisa-py backend')

        try:
            self.scope = self.rm.open_resource(f'TCPIP0::{ip}::inst0::INSTR')
            self.scope.timeout = 10000
            self.connected = True
        except Exception:
            print(f'IO Error: Cannot Connect to {ip}')
            self.connected = False

    def query_scope(self, command):
        return self.scope.query(command).strip()

    def write_scope(self, command):
        self.scope.write(command)

    def arm_scope(self):
        status = self.query_scope('ACQuire:STATE?')
        if status == '0':
            self.write_scope('ACQuire:STATE 1')
            time.sleep(1)

    def force_trigger(self):
        self.write_scope('TRIGGER FORCE')

    def check_scope(self):
        initialstatus = self.query_scope('ACQuire:STATE?')
        if initialstatus == '0':
            print('Scope NOT Armed')
        elif initialstatus == '1':
            print('Scope Armed')
        else:
            print(f'Unexpected state: {initialstatus}')

        OKtoClearData = input(f"OK to clear Scope {self.ip} Data? (Yes/No): ").strip()
        if OKtoClearData.lower().startswith('yes'):
            self.arm_scope()
            trigLev_str = self.query_scope('TRIG:A:lev?')
            try:
                trigLev = float(trigLev_str)
            except ValueError:
                trigLev = None
                print("Could not parse trigger level.")
            self.force_trigger()
            t, ycell, _ = self.get_scope_data([1], 100000 * 1024)
            mean_val = np.mean(ycell[0]) if ycell else float('nan')
            print(f'Mean BOBB: {mean_val:.3f} Volts, Trig Level: {trigLev:.3f} V')
            self.arm_scope()
            print('Scope Armed')
        else:
            print('Cancelled')

    def get_scope_data(self, channels, buffer_size=100000 * 1024):
        self.scope.write("header 0")
        self.scope.write("wfmo:byt_n 1")
        self.scope.write("data:encdg rib")
        self.scope.write("data:start 1")
        record = int(self.query_scope("hor:reco?"))
        self.scope.write(f"data:stop {record}")
        bytno_check = int(self.query_scope("wfmo:byt_n?"))
        print(f"Byte number: {bytno_check}")

        t, y_data, enabled_channels = None, [], []
        for ch in channels:
            if int(self.scope.query('SELect:CH%s?' % ch)) != 1:
                print(f"Channel {ch} not enabled")
                continue
            self.write_scope(f"data:source CH{ch}")
            samples = np.array(
                self.scope.query_binary_values('CURV?', datatype='b',
                                               is_big_endian=True,
                                               chunk_size=buffer_size)
            )
            if t is None:
                x_incr = float(self.query_scope("wfmo:xincr?"))
                x_zero = float(self.query_scope("wfmo:xzero?"))
                x_off  = float(self.query_scope("wfmo:PT_off?"))
                t = x_zero + x_incr * (np.arange(len(samples)) + 1 - x_off)
            y_mult = float(self.query_scope("wfmo:ymult?"))
            y_off  = float(self.query_scope("wfmo:yoff?"))
            y_zero = float(self.query_scope("wfmo:yzero?"))
            y_data.append((samples - y_off) * y_mult + y_zero)
            enabled_channels.append(ch - 1)
        return t, y_data, enabled_channels

    def close(self):
        if self.connected:
            self.scope.close()


# ---------------------------------------------------------------------------
# Waveform fetch worker — runs TektronixScope in a thread pool thread
# ---------------------------------------------------------------------------
class WaveformWorkerSignals(QObject):
    finished = pyqtSignal(object, object, object)  # t, y_data, enabled_channels
    error    = pyqtSignal(str)


class WaveformFetchWorker(QRunnable):
    def __init__(self, ip, channels):
        super().__init__()
        self.ip       = ip
        self.channels = channels
        self.signals  = WaveformWorkerSignals()

    @pyqtSlot()
    def run(self):
        try:
            scope = TektronixScope(self.ip)
            try:
                if not scope.connected:
                    self.signals.error.emit(f"Could not connect to {self.ip}")
                    return
                t, y_data, enabled_channels = scope.get_scope_data(self.channels)
                self.signals.finished.emit(t, y_data, enabled_channels)
            finally:
                scope.close()
        except Exception as e:
            self.signals.error.emit(str(e))


# ---------------------------------------------------------------------------
# ScopeViewer — pyqtgraph waveform window (unchanged from V2_4)
# ---------------------------------------------------------------------------
class ScopeViewer(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tektronix Oscilloscope Viewer")
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QtWidgets.QVBoxLayout(self.central_widget)

        hlayout = QtWidgets.QHBoxLayout()
        self.layout.addLayout(hlayout)

        self.DecimateCheck = QtWidgets.QCheckBox('Decimate')
        self.DecimateCheck.setCheckState(2)
        self.DecimateCheck.stateChanged.connect(self.updateplot)
        hlayout.addWidget(self.DecimateCheck)

        self.Ndecimate = QtWidgets.QLineEdit()
        self.Ndecimate.setMaximumWidth(200)
        self.Ndecimate.setText('20000')
        self.Ndecimate.editingFinished.connect(self.updateplot)
        hlayout.addWidget(self.Ndecimate)

        self.datat = None
        self.datay = None
        self.plot_widget = pg.PlotWidget()
        self.layout.addWidget(self.plot_widget)
        self.plot_widget.addLegend()

        self.cursor_label = QtWidgets.QLabel("Cursor Position: (x, y)")
        self.layout.addWidget(self.cursor_label)

        self.data_select_button = QtWidgets.QPushButton("Data Select Mode")
        self.data_select_button.setCheckable(True)
        self.data_select_button.clicked.connect(self.toggle_data_select_mode)
        self.layout.addWidget(self.data_select_button)

        self.plot_widget.scene().sigMouseMoved.connect(self.update_cursor_position)
        self.plot_widget.scene().sigMouseClicked.connect(self.handle_mouse_click)
        self.plot_widget.setInteractive(True)
        self.plot_widget.getViewBox().setMouseMode(ViewBox.PanMode)

        self.data_select_mode = False
        self.data_marker = None
        self.data_marker_X = None

    def update_cursor_position(self, pos):
        vb = self.plot_widget.getViewBox()
        if vb.sceneBoundingRect().contains(pos):
            mp = vb.mapSceneToView(pos)
            self.cursor_label.setText(f"Cursor Position: ({mp.x():.3f}, {mp.y():.3f})")

    def toggle_data_select_mode(self):
        self.data_select_mode = self.data_select_button.isChecked()
        if not self.data_select_mode and self.data_marker:
            self.plot_widget.removeItem(self.data_marker)
            self.data_marker = None

    def handle_mouse_click(self, event):
        if not self.data_select_mode:
            return
        vb = self.plot_widget.getViewBox()
        mp = vb.mapSceneToView(event.scenePos())
        x_click, y_click = mp.x(), mp.y()
        closest_x, closest_y = None, None
        min_distance = float('inf')
        for item in self.plot_widget.listDataItems():
            data_x, data_y = item.getData()
            distances = np.sqrt((data_x - x_click) ** 2 + (data_y - y_click) ** 2)
            idx = np.argmin(distances)
            if distances[idx] < min_distance:
                min_distance = distances[idx]
                closest_x, closest_y = data_x[idx], data_y[idx]
        if closest_x is not None:
            if self.data_marker:
                self.plot_widget.removeItem(self.data_marker)
            if self.data_marker_X:
                self.plot_widget.removeItem(self.data_marker_X)
            self.data_marker_X = pg.ScatterPlotItem(shape='x')
            self.data_marker_X.addPoints([closest_x], [closest_y])
            self.data_marker = pg.TextItem(f"({closest_x:.7e}, {closest_y:.3f})", anchor=(0.5, -1.0))
            self.data_marker.setPos(closest_x, closest_y)
            self.plot_widget.addItem(self.data_marker)
            self.plot_widget.addItem(self.data_marker_X)

    def plot_data(self, time, y_data, enabled_channels=None):
        self.datat = time
        self.datay = y_data
        self.enabled_channels = enabled_channels
        self.updateplot()

    def updateplot(self):
        self.plot_widget.clear()
        if self.datay is None:
            return
        colors = ['y', 'c', 'r', 'g']
        labels = ['Ch1', 'Ch2', 'Ch3', 'Ch4']
        for idx, y in enumerate(self.datay):
            ch_idx = self.enabled_channels[idx] if hasattr(self, "enabled_channels") and self.enabled_channels else idx
            display_idx = ch_idx % 4
            time_new = self.datat
            if self.DecimateCheck.isChecked():
                try:
                    decimationN = int(self.Ndecimate.text())
                except ValueError:
                    decimationN = 10000
                    self.Ndecimate.setText('10000')
                factor = int(len(y) / decimationN)
                if factor > 1:
                    time_new, y, _ = decimate_time_series(self.datat, y, factor, axis=-1)
            self.plot_widget.plot(time_new, y, pen=colors[display_idx], name=labels[display_idx])


# ---------------------------------------------------------------------------
# Decimation helpers (unchanged from V2_4)
# ---------------------------------------------------------------------------
def _prime_factors(n: int) -> List[int]:
    if n < 2:
        return []
    f, d = [], 2
    while d * d <= n:
        while n % d == 0:
            f.append(d)
            n //= d
        d += 1 if d == 2 else 2
    if n > 1:
        f.append(n)
    return f


def factorize_decimation(q: int, max_stage: int = 13) -> Optional[List[int]]:
    if q < 1:
        raise ValueError("q must be a positive integer.")
    if q == 1:
        return []
    primes = _prime_factors(q)
    if any(p > max_stage for p in primes):
        return None
    bins: List[int] = []
    for p in sorted(primes, reverse=True):
        best_i, best_prod = -1, 0
        for i, prod in enumerate(bins):
            new_prod = prod * p
            if new_prod <= max_stage and new_prod > best_prod:
                best_i, best_prod = i, new_prod
        if best_i == -1:
            bins.append(p)
        else:
            bins[best_i] = best_prod
    prod = 1
    for b in bins:
        prod *= b
    return bins if prod == q else None


def staged_decimate(x, q, axis=-1, max_stage=13, ftype="iir", zero_phase=True,
                    allow_resample_poly=True, return_factors=False, **kw) -> Tuple:
    if not isinstance(q, int) or q < 1:
        raise ValueError("q must be a positive integer.")
    if q == 1:
        return (np.array(x, copy=True), [] if return_factors else None)
    factors = factorize_decimation(q, max_stage=max_stage)
    if factors is None:
        if not allow_resample_poly:
            raise ValueError(f"Cannot factor q={q} into stages <= {max_stage}.")
        y = resample_poly(x, up=1, down=q, axis=axis,
                          **{k: v for k, v in kw.items() if k in {"window"}})
        return (y, None)
    y = np.asarray(x)
    for f in factors:
        y = decimate(y, f, axis=axis, ftype=ftype, zero_phase=zero_phase, **kw)
    return (y, factors if return_factors else None)


def decimate_time_series(t, y, q, axis=-1, **kwargs):
    t = np.asarray(t)
    if t.ndim != 1:
        raise ValueError("t must be 1D.")
    n = y.shape[axis]
    if t.size != n:
        raise ValueError(f"t.size ({t.size}) != y.shape[axis] ({n}).")
    y_dec, factors = staged_decimate(y, q=q, axis=axis, return_factors=True, **kwargs)
    t_dec = t[::q]
    if t_dec.shape[0] != y_dec.shape[axis]:
        t_dec = t_dec[:y_dec.shape[axis]]
    return t_dec, y_dec, factors


# ---------------------------------------------------------------------------
# Threaded scope query worker (unchanged from V2_4)
# ---------------------------------------------------------------------------
class ScopeQueryWorker(QRunnable):
    def __init__(self, row, scope_name, ip, rm, parent=None):
        super().__init__()
        self.row = row
        self.scope_name = scope_name
        self.ip = ip
        self.rm = rm
        self.signals = WorkerSignals(parent)

    @pyqtSlot()
    def run(self):
        self.signals.started.emit(self.scope_name)
        data = {}
        oscope = None
        try:
            try:
                oscope = self.rm.open_resource(f"TCPIP::{self.ip}::INSTR", open_timeout=1500)
            except visa.VisaIOError:
                if scope_connection_diagnostic_outputs:
                    print(f"  {self.scope_name}: VXI-11 failed, retrying via socket (port 4000)")
                oscope = self.rm.open_resource(f"TCPIP::{self.ip}::4000::SOCKET")
                oscope.write_termination = '\n'
                oscope.read_termination  = '\n'
            oscope.timeout = 3000
            oscope.write('HEADer OFF')
            oscope.write('DATa:START 1')
            oscope.write('DATa:STOP 1000000000')
            QtCore.QThread.msleep(100)

            def q(cmd):
                try:
                    resp = oscope.query(cmd).strip()
                    # Strip SCPI header prefix if present (e.g. ":HORIZONTAL:RECORDLENGTH 5000000" → "5000000")
                    if ' ' in resp:
                        resp = resp.split()[-1]
                    return resp
                except Exception as qe:
                    if scope_connection_diagnostic_outputs:
                        print(f"  {self.scope_name} '{cmd}': {type(qe).__name__}: {qe}")
                    return 'ERR'

            data['armed']  = q('TRIG:STATE?')
            data['state']  = q('ACQuire:STOPAFTER?')
            start          = q('DATa:START?')
            stop           = q('DATa:STOP?')
            # Strip any SCPI header prefix (e.g. ":DATA:START 1 " → "1")
            if ' ' in start:
                start = start.split()[-1]
            if ' ' in stop:
                stop = stop.split()[-1]
            data['range']  = f"{start} - {stop}"
            data['length'] = q('HORizontal:RECOrdlength?')
            width          = q('DATa:WIDTH?')
            data['width']  = ''.join(c for c in width if c.isdigit())
            self.signals.finished.emit(self.scope_name, data)
        except Exception as e:
            self.signals.error.emit(self.scope_name, e)
        finally:
            if oscope is not None:
                try:
                    oscope.close()
                except Exception:
                    pass
                try:
                    oscope.session = None
                except Exception:
                    pass
                oscope = None


# ---------------------------------------------------------------------------
# Save worker — one per scope, runs in thread pool (item 5)
# ---------------------------------------------------------------------------
class SaveWorkerSignals(QObject):
    # scope_name, list of "ScopeX Ch Y" failure strings
    finished = pyqtSignal(str, list)


class ScopeSaveWorker(QRunnable):
    def __init__(self, scope_name, ip, shot_name, save_dir, app, cancel_event):
        super().__init__()
        self.scope_name   = scope_name
        self.ip           = ip
        self.shot_name    = shot_name
        self.save_dir     = save_dir
        self.app          = app
        self.cancel_event = cancel_event
        self.signals      = SaveWorkerSignals()

    @pyqtSlot()
    def run(self):
        # Each save worker gets its own ResourceManager so a prior VI_ERROR_TMO
        # on the shared RM can't corrupt this session.
        # Do NOT call local_rm.close() — NI-VISA shares the underlying library
        # session across all RM instances; closing one invalidates them all.
        local_rm = visa.ResourceManager()

        def _local_open(ip, timeout=5000, open_timeout=2000):
            rsrc = local_rm.open_resource(f"TCPIP::{ip}::INSTR", open_timeout=open_timeout)
            rsrc.timeout = timeout
            return rsrc

        # Query enabled channels via SELect?; fall back to CH1-4
        try:
            with _local_open(self.ip) as oscope:
                sel = oscope.query('SELect?').strip()
                matches = re.findall(r'CH(\d+)\s+([01])', sel)
                channels = [int(ch) for ch, st in matches if st == '1'] or [1, 2, 3, 4]
        except Exception as e:
            print(f"Channel query failed for {self.ip}, falling back to CH1-4: {e}")
            channels = [1, 2, 3, 4]

        failures = []
        scope_num = self.scope_name.strip('Scope')
        for ch in channels:
            if self.cancel_event.is_set():
                break
            fname  = f"{self.shot_name}__S{scope_num}C{ch}.isf"
            result = self.app.savedata(self.ip, ch, fname, save_dir=self.save_dir,
                                       rm=local_rm, cancel_event=self.cancel_event)
            if result == 1:
                failures.append(f"{self.scope_name} Ch {ch}")
        self.signals.finished.emit(self.scope_name, failures)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class OscApp(QtWidgets.QMainWindow):

    # ── Table helpers ────────────────────────────────────────────────────────
    def setRowColor(self, row, qcolor):
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setBackground(qcolor)

    _NUMERIC_COLS = {6, 7}   # right-aligned
    _CENTER_COLS  = {2}      # center-aligned (Connect Y/N)

    def _item(self, text, col):
        item = QtWidgets.QTableWidgetItem(str(text))
        if col in self._NUMERIC_COLS:
            item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        elif col in self._CENTER_COLS:
            item.setTextAlignment(QtCore.Qt.AlignCenter)
        else:
            item.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        return item

    def _find_row(self, scope_name):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == scope_name:
                return r
        return -1

    # ── Worker slots ─────────────────────────────────────────────────────────
    @pyqtSlot(str)
    def onScopeStarted(self, scope_name):
        row = self._find_row(scope_name)
        if row >= 0:
            self.setRowColor(row, QtGui.QColor(200, 200, 200))

    @pyqtSlot(str, dict)
    def onScopeFinished(self, scope_name, data):
        row = self._find_row(scope_name)
        if row < 0:
            return
        trigstate = data.get('armed', 'ERR')
        armedcolor = QtGui.QColor(255, 0, 0)
        if trigstate.lower() == 'ready':
            armedcolor = QtGui.QColor(81, 158, 99)
            trigstate = 'READY'
            self.table.setItem(row, 2, self._item('Y', 2))
        elif trigstate.lower() == 'save':
            armedcolor = QtGui.QColor(255, 0, 0)
            trigstate = 'SAVE'
            self.table.setItem(row, 2, self._item('Y', 2))
        else:
            trigstate = 'ERROR'
        self.table.setItem(row, 1, self._item(trigstate, 1))

        statedat = data.get('state', 'ERR')
        if statedat.lower() == 'sequence':
            state = 'Single-Shot'
        elif statedat.lower() == 'runstop':
            state = 'Run-Stop'
        else:
            state = 'Error'
        mode_error = (state == 'Error')
        self.table.setItem(row, 3, self._item(state, 3))

        SaveDataRange = data.get('range', 'ERR')
        RecLength     = data.get('length', 'ERR')
        self.table.setItem(row, 6, self._item(RecLength, 6))

        range_error = False
        try:
            rangestart = int(SaveDataRange.split('-')[0])
            rangeend   = int(SaveDataRange.split('-')[-1])
            if rangestart == 1 and rangeend >= int(RecLength):
                RangeText = 'OK'
            else:
                RangeText = 'ERROR'
                range_error = True
        except Exception as e:
            RangeText = SaveDataRange
            print(e)

        self.table.setItem(row, 5, self._item(RangeText, 5))
        self.table.setItem(row, 7, self._item(data.get('width', 'ERR'), 7))

        if mode_error:
            self.setRowColor(row, QtGui.QColor(255, 100, 100))
        elif range_error:
            self.setRowColor(row, QtGui.QColor(255, 160, 0))
        else:
            self.setRowColor(row, QtGui.QColor(255, 255, 255))

        armeditem = self.table.item(row, 1)
        if armeditem is not None:
            armeditem.setBackground(QtGui.QBrush(armedcolor))

    @pyqtSlot(str, Exception)
    def onScopeError(self, scope_name, exc):
        row = self._find_row(scope_name)
        if row < 0:
            return
        self.table.setItem(row, 2, self._item('N', 2))
        self.setRowColor(row, QtGui.QColor(255, 160, 0))
        print(f"Error updating {scope_name}: {exc}")

    # ── Init ─────────────────────────────────────────────────────────────────
    def __init__(self):
        self.settings = QtCore.QSettings('DCS', 'ScopeProgram')

        self.threadpool = QThreadPool()
        self.custom_title = 'Oscilloscope Control'
        self.version = '3.0'

        super().__init__()

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DCS.SCOPECONTROL")
        except Exception:
            pass
        self.icon = QtGui.QIcon(str(Path(__file__).parent / "ui" / "scope.jpeg"))
        self.setWindowIcon(self.icon)

        now = datetime.datetime.now()
        self.year = str(now.year)
        self.platform_name = ''
        self.shot_number = 'Not set'
        self.shot_name = 'curr shot name'
        self.shot_dir = 'not set'

        # Load scope/platform config from JSON (falls back to generic defaults)
        saved_path = self.settings.value('config_path', '')
        if saved_path and os.path.isfile(saved_path):
            self.config_path = saved_path
            self._load_config(saved_path)
        else:
            default = Path(__file__).parent / 'scopes.json'
            if default.is_file():
                self.config_path = str(default)
                self._load_config(self.config_path)
            else:
                self.config_path = ''
                self._load_default_config()

        # Init shared settings: explicit path from config → sibling .ini next to scopes.json → registry
        if self.cfg_path:
            ini_path = QtCore.QDir.fromNativeSeparators(self.cfg_path)
        elif self.config_path:
            ini_path = QtCore.QDir.fromNativeSeparators(
                str(Path(self.config_path).parent / "ScopeControl.ini")
            )
        else:
            ini_path = ""
        if ini_path:
            self.sharedsettings = QtCore.QSettings(ini_path, QtCore.QSettings.IniFormat)
            if self.sharedsettings.status() != QtCore.QSettings.NoError:
                self.sharedsettings = QtCore.QSettings('DCS', 'ScopeProgram')
                print('Using local settings (shared .ini unavailable)')
            else:
                print('Using shared settings:', ini_path)
        else:
            self.sharedsettings = QtCore.QSettings('DCS', 'ScopeProgram')
            print('Using local settings (no config file loaded)')
        print("Shared Settings file:", self.sharedsettings.fileName())

        self.save_dir = self.user_dir_base
        self.save_dir_lower = self.save_dir
        self.standard_name = True

        self._build_menu()

        try:
            self.rm = visa.ResourceManager()
        except Exception:
            self.rm = visa.ResourceManager('@py')
            print('NI-VISA not found, using pyvisa-py backend')

        prev = self.settings.value('scopes_selected') if self.settings.contains('scopes_selected') else False
        if prev:
            self.Sel_Scope_Names = set(prev)
        else:
            self.Sel_Scope_Names = set(self._config.get("presets", {}).get("Default", []))
            if not self.Sel_Scope_Names:
                self.Sel_Scope_Names = set(self.All_Scopes.keys())

        self.Sel_Scope_Names = {s for s in self.Sel_Scope_Names if s in self.scope_ips}
        self.scopes = {k: self.scope_ips[k] for k in self.scope_ips if k in self.Sel_Scope_Names}

        self.Filename  = "s175xxx"
        self.Filename2 = "s175xxx"

        self._build_central_widget()
        if self.archiving_enabled:
            self.change_platform()
        self.Clean_SelScopes()

        try:
            last_user_dir = self.sharedsettings.value('save_dir') if self.sharedsettings.contains('save_dir') else False
            if last_user_dir and os.path.exists(last_user_dir):
                self.get_user_dir(new_dir=last_user_dir)
        except Exception:
            print("Could not access previous User Dir")

    # ── Config loading ───────────────────────────────────────────────────────
    def _load_config(self, path):
        try:
            with open(path, 'r') as f:
                self._config = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Config Error", f"Could not load config:\n{e}")
            self._config = {}
        self._apply_config()

    def _load_default_config(self):
        self._config = {
            "scopes": {
                "Scope1": {"ip": "192.168.1.10", "purpose": "Example scope 1"},
                "Scope2": {"ip": "192.168.1.11", "purpose": "Example scope 2"},
            },
            "presets": {
                "Default": ["Scope1", "Scope2"],
            },
            "platforms": self._default_platforms(),
            "archiving": {
                "enabled": False,
                "engineering_drive": "",
                "gun_shot_data_path": "Shot Data",
                "laser_shot_data_path": "Laser Shot Data",
                "user_data_path": "User_Data",
            },
            "settings": {
                "shared_ini_path": "",
            },
        }
        self._apply_config()

    def _apply_config(self):
        scopes_raw = self._config.get("scopes", {})
        self.All_Scopes        = {n: info["ip"]               for n, info in scopes_raw.items()}
        self.all_scopes_purpose= {n: info.get("purpose", "") for n, info in scopes_raw.items()}
        self.scope_ips         = dict(self.All_Scopes)
        self.presets           = self._config.get("presets", {})
        self.custom_title      = self._config.get("app_title", "Oscilloscope Control")
        self.platforms_config  = self._config.get("platforms", self._default_platforms())
        self.cfg_path          = self._config.get("settings", {}).get("shared_ini_path", "")

        archiving = self._config.get("archiving", {})
        self.archiving_enabled = archiving.get("enabled", True)
        eng_drive = archiving.get("engineering_drive", "")
        self.engineering_drive    = self.settings.value('EngineeringDrive', eng_drive)
        gun_path                  = archiving.get("gun_shot_data_path",   "Impact Facilities/Shot Data")
        laser_path                = archiving.get("laser_shot_data_path", "Laser Shock/Shot Data")
        user_path                 = archiving.get("user_data_path",       "User_Data")
        self.shot_dir_base        = '/'.join([self.engineering_drive.rstrip('/'), gun_path])
        self.laser_dir_base       = '/'.join([self.engineering_drive.rstrip('/'), laser_path])
        self.user_dir_base        = '/'.join([self.engineering_drive.rstrip('/'), user_path, self.year])

    @staticmethod
    def _default_platforms():
        return [
            {"label": "100J Laser (C)",      "code": "C", "subdir": "ID-C",   "dir_type": "laser"},
            {"label": "Gas Gun (D)",      "code": "2", "subdir": "ID-D-2", "dir_type": "gun"},
            {"label": "Powder Gun (D)",   "code": "1", "subdir": "ID-D-1", "dir_type": "gun"},
            {"label": "SSGG (E)",         "code": "3", "subdir": "ID-E-3", "dir_type": "gun"},
            {"label": "Powder Gun (E)",   "code": "4", "subdir": "ID-E-4", "dir_type": "gun"},
            {"label": "2-Stage Gun (E)",  "code": "5", "subdir": "ID-E-5", "dir_type": "gun"},
        ]

    def _save_config(self, path):
        try:
            with open(path, 'w') as f:
                json.dump(self._config, f, indent=2)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save Error", f"Could not save config:\n{e}")

    def _save_as_preset(self):
        selected = list(self.Sel_Scope_Names)
        if not selected:
            QtWidgets.QMessageBox.information(self, "Save Preset", "No scopes are currently selected.")
            return

        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save Current Selection as Preset",
            "Preset name (will be uppercased):"
        )
        if not ok or not name.strip():
            return

        name = name.strip().upper()
        presets = self._config.setdefault("presets", {})

        if name in presets:
            reply = QtWidgets.QMessageBox.question(
                self, "Overwrite Preset?",
                f'Preset "{name}" already exists.\nOverwrite it with the current selection?',
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        presets[name] = sorted(selected)
        if self.config_path and os.path.isfile(self.config_path):
            self._save_config(self.config_path)
        QtWidgets.QMessageBox.information(
            self, "Preset Saved",
            f'Preset "{name}" saved with {len(selected)} scope(s).'
        )

    def _load_config_file_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Scope Configuration",
            str(Path(self.config_path).parent) if self.config_path else str(Path(__file__).parent),
            "JSON Files (*.json)"
        )
        if path:
            self.config_path = path
            self.settings.setValue('config_path', path)
            self._load_config(path)
            self._rebuild_after_config_load()

    def _rebuild_after_config_load(self):
        if hasattr(self, 'cmbPlatform'):
            self._rebuild_platform_combo()
        self._rebuild_scope_selection()
        if self.archiving_enabled and hasattr(self, 'cmbPlatform'):
            self.cmbPlatform.setVisible(True)
            self.change_platform()
        elif hasattr(self, 'cmbPlatform'):
            self.cmbPlatform.setVisible(False)
        self.update_table()

    def _rebuild_platform_combo(self):
        self.cmbPlatform.blockSignals(True)
        self.cmbPlatform.clear()
        for p in self.platforms_config:
            self.cmbPlatform.addItem(p["label"], p["code"])
        default_idx = next((i for i, p in enumerate(self.platforms_config) if p["code"] == "4"), 0)
        self.cmbPlatform.setCurrentIndex(default_idx)
        self.cmbPlatform.blockSignals(False)

    def _rebuild_scope_selection(self):
        self.Sel_Scope_Names = {s for s in self.Sel_Scope_Names if s in self.scope_ips}
        self.scopes = {k: self.scope_ips[k] for k in self.scope_ips if k in self.Sel_Scope_Names}

    # ── Menu ─────────────────────────────────────────────────────────────────
    def _build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        set_dir_act = QtWidgets.QAction("Set Directory…", self)
        set_dir_act.triggered.connect(self.get_user_dir)
        file_menu.addAction(set_dir_act)
        set_name_act = QtWidgets.QAction("Set Filename…", self)
        set_name_act.triggered.connect(self.get_custom_name)
        file_menu.addAction(set_name_act)
        save_preset_act = QtWidgets.QAction("Save Current Selection as Preset…", self)
        save_preset_act.triggered.connect(self._save_as_preset)
        file_menu.addAction(save_preset_act)
        file_menu.addSeparator()
        load_cfg_act = QtWidgets.QAction("Set Scope Configuration File…", self)
        load_cfg_act.triggered.connect(self._load_config_file_dialog)
        file_menu.addAction(load_cfg_act)
        edit_cfg_act = QtWidgets.QAction("Edit Scope Configuration…", self)
        edit_cfg_act.triggered.connect(self.openScopeManagerDialog)
        file_menu.addAction(edit_cfg_act)

        scopes_menu = menubar.addMenu("Scopes")
        select_act = QtWidgets.QAction("Select Scopes…", self)
        select_act.triggered.connect(self.openSelectScopesDialog)
        scopes_menu.addAction(select_act)

        commands_menu = menubar.addMenu("Commands")
        # Scope19Runst_act = QtWidgets.QAction("RunStop Scope 3/19", self)
        # Scope19Runst_act.triggered.connect(self.SetscEvent)
        # commands_menu.addAction(Scope19Runst_act)

        commands_all_menu = commands_menu.addMenu("Send to All")
        SoftTrig_act = QtWidgets.QAction("Soft Trigger", self)
        SoftTrig_act.triggered.connect(self.SoftTrigger)
        commands_all_menu.addAction(SoftTrig_act)
        SS_act = QtWidgets.QAction("Single Shot", self)
        SS_act.triggered.connect(self.SetSSEvent)
        commands_all_menu.addAction(SS_act)
        RunSt_act = QtWidgets.QAction("Run/Stop", self)
        RunSt_act.triggered.connect(self.SetRSEvent)
        commands_all_menu.addAction(RunSt_act)

        help_menu = menubar.addMenu("Help")
        manual_act = QtWidgets.QAction("Open Manual…", self)
        manual_act.triggered.connect(self._open_manual)
        help_menu.addAction(manual_act)
        repo_act = QtWidgets.QAction("Open Repository…", self)
        repo_act.triggered.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://github.com/Dynamic-Compression-Sector-Codes/shared-scopes")))
        help_menu.addAction(repo_act)
        help_menu.addSeparator()
        about_act = QtWidgets.QAction("About…", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    def _open_manual(self):
        manual = Path(__file__).parent / "ui" / "manual.html"
        if not manual.is_file():
            QtWidgets.QMessageBox.warning(self, "Manual Not Found",
                                          f"Could not find manual at:\n{manual}")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("ScopeControl Manual")
        dlg.resize(820, 640)
        browser = QtWidgets.QTextBrowser(dlg)
        browser.setOpenExternalLinks(True)
        browser.setSource(QtCore.QUrl.fromLocalFile(str(manual)))
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(browser)
        dlg.exec_()

    def _show_about(self):
        cfg_line = (f"<br><br><small><b>Config:</b> {self.config_path}</small>"
                    if self.config_path else
                    "<br><br><small><b>Config:</b> (defaults — no file loaded)</small>")
        QtWidgets.QMessageBox.about(self, "About ScopeControl",
            f"<b>DCS Oscilloscope Control</b><br>"
            f"Version {self.version}<br><br>"
            f"Multi-scope, multi-channel oscilloscope control<br>"
            f"for Tektronix instruments over TCP/IP.<br><br>"
            f"Dynamic Compression Sector — Argonne National Laboratory"
            f"{cfg_line}")

    # ── Central widget ───────────────────────────────────────────────────────
    def _build_central_widget(self):
        self.centralWidget = QtWidgets.QWidget()
        self.gridLayout = QtWidgets.QGridLayout()
        self.centralWidget.setLayout(self.gridLayout)
        self.setCentralWidget(self.centralWidget)

        self.setWindowTitle('Oscilloscope Control ' + self.version + '    ' + self.custom_title)
        geom = self.settings.value('window_geometry')
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(420, 500)

        self.table = QtWidgets.QTableWidget()
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context)
        self.table.setWindowTitle("Scope Control")
        self.column_headers = ['Scope', 'Trigger', 'Conn', "Mode", 'IP', 'Range', "#Pts", 'Bytes', 'X']
        self.table.setColumnCount(len(self.column_headers))
        self.table.setHorizontalHeaderLabels(self.column_headers)
        self.table.verticalHeader().setVisible(False)
        self.table.resize(300, 250)
        self.table.setRowCount(len(self.All_Scopes))
        self.gridLayout.addWidget(self.table, 1, 0, 1, -1)

        # All columns interactive (user-draggable); Scope column stretches to
        # fill remaining width so the table always spans the full window width.
        hdr = self.table.horizontalHeader()
        hdr.setMinimumSectionSize(20)
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        # col:  Trig  Conn  Mode  IP   Range  #Pts  Bytes   X
        for col, w in zip(range(1, 9), [50, 45, 60, 35, 45, 55, 40, 28]):
            hdr.resizeSection(col, w)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        shotlabelwidg = QtWidgets.QWidget()
        labellayout = QtWidgets.QVBoxLayout()
        shotlabelwidg.setLayout(labellayout)

        self.lblShotName = QtWidgets.QLabel(self.shot_name)
        self.shotFont = QtGui.QFont()
        self.shotFont.setBold(True)
        self.lblShotName.setFont(self.shotFont)
        self.gridLayout.addWidget(shotlabelwidg, 2, 0)
        labellayout.addWidget(self.lblShotName)

        # Platform combo — populated from config, hidden when archiving is disabled
        self.cmbPlatform = QtWidgets.QComboBox(self)
        for p in self.platforms_config:
            self.cmbPlatform.addItem(p["label"], p["code"])
        default_idx = next((i for i, p in enumerate(self.platforms_config) if p["code"] == "4"), 0)
        self.cmbPlatform.setCurrentIndex(default_idx)
        self.cmbPlatform.currentIndexChanged.connect(self.change_platform)
        self.cmbPlatform.setVisible(self.archiving_enabled)
        self.gridLayout.addWidget(self.cmbPlatform, 2, 1, 1, 1)

        self.lblShotDir = QtWidgets.QLabel(self.save_dir)
        self.lblShotDir.setWordWrap(True)
        labellayout.addWidget(self.lblShotDir)

        Clear = QtWidgets.QPushButton("Update Table")
        Clear.clicked.connect(partial(self.update_table))
        self.gridLayout.addWidget(Clear, 3, 2, 1, 1)

        qbtnf = QtWidgets.QPushButton("ARM Scopes", self)
        qbtnf.clicked.connect(self.ArmEvent)
        self.gridLayout.addWidget(qbtnf, 2, 2, 1, 1)

        self.qbtnw = QtWidgets.QPushButton("Save Scope Data", self)
        self.qbtnw.clicked.connect(self.on_save)
        self.gridLayout.addWidget(self.qbtnw, 2, 3, 1, -1)

        container = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout()
        container.setLayout(lay)
        self.tupdate = QtWidgets.QLabel(self)
        self.gridLayout.addWidget(container, 3, 0, 1, 2)
        lay.addWidget(self.tupdate)

        self.show()
        self.update_table()

    def closeEvent(self, event):
        self.settings.setValue('window_geometry', self.saveGeometry())
        super().closeEvent(event)

    # ── Scope manager dialog ─────────────────────────────────────────────────
    def openScopeManagerDialog(self):
        dlg = ScopeManagerDialog(self._config, config_path=self.config_path, parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self._config["scopes"] = dlg.get_updated_scopes()
            for key, val in dlg.get_extra_config().items():
                self._config[key] = val
            if self.config_path:
                self._save_config(self.config_path)
            self._apply_config()
            self._rebuild_after_config_load()
            self.update_table()

    # ── Scope selector dialog ────────────────────────────────────────────────
    def openSelectScopesDialog(self):
        dlg = SelectScopesDialog(
            self.scope_ips, self.all_scopes_purpose,
            set(self.Sel_Scope_Names),
            presets=self.presets,
            parent=self
        )
        new_selected = dlg.exec_()
        self.Sel_Scope_Names = new_selected
        self.scopes = {k: self.scope_ips[k] for k in self.scope_ips if k in self.Sel_Scope_Names}
        self.settings.setValue('scopes_selected', self.Sel_Scope_Names)
        self.update_table()

    # ── Table context menu ───────────────────────────────────────────────────
    def _on_table_context(self, point):
        idx = self.table.indexAt(point)
        if not idx.isValid():
            return
        item = self.table.item(idx.row(), idx.column())
        if item is None:
            return
        menu = QtWidgets.QMenu(self)
        act1 = menu.addAction("Send Single Command")
        act1.triggered.connect(partial(self.send_single_cmd, idx.row()))
        act2 = menu.addAction("Set Run-Stop")
        act2.triggered.connect(partial(self.send_single_Runstop, idx.row()))
        act3 = menu.addAction("Set Single")
        act3.triggered.connect(partial(self.send_single_SingleShot, idx.row()))
        act_show = menu.addAction("Show Current Waveform…")
        act_show.triggered.connect(partial(self._show_scope_waveform_for_row, idx.row()))
        menu.exec_(self.table.viewport().mapToGlobal(point))

    # ── Utility ──────────────────────────────────────────────────────────────
    def _open(self, ip, timeout=3000, open_timeout=1500, rm=None):
        _rm = rm if rm is not None else self.rm
        rsrc = _rm.open_resource(f"TCPIP::{ip}::INSTR", open_timeout=open_timeout)
        rsrc.timeout = timeout
        return rsrc

    def get_custom_name(self):
        text, ok = QtWidgets.QInputDialog.getText(self, "Custom Filename", "Enter Filename",
                                                   QtWidgets.QLineEdit.Normal, "")
        if ok and text:
            self.shot_name = text
            self.save_dir_lower = self.save_dir
            self.standard_name = False
            self.update_lower_table()

    def update_lower_table(self):
        if self.save_dir and os.path.isdir(self.save_dir):
            self.lblShotDir.setText(self.save_dir)
            self.lblShotDir.setStyleSheet("")
        elif not self.save_dir or (self.archiving_enabled and not self.engineering_drive):
            self.lblShotDir.setText("(no directory configured)")
            self.lblShotDir.setStyleSheet("color: gray;")
        else:
            self.lblShotDir.setText(f"{self.save_dir}  ⚠ not found")
            self.lblShotDir.setStyleSheet("color: #c0392b;")
        self.lblShotName.setText(str(self.shot_name))

    # ── Platform / directory logic ───────────────────────────────────────────
    def change_platform(self):
        if not self.archiving_enabled:
            return
        self.standard_name = True
        self.get_shot_dir()
        print(self.shot_dir)
        self.shot_name = self.year[2:] + '-' + self.platform_name + '-' + str(self.shot_number).zfill(3)
        curr_shot_dir = '/'.join([self.shot_dir.rstrip('/'), self.shot_name])
        if not os.path.isdir(curr_shot_dir) and self.standard_name:
            os.makedirs(curr_shot_dir)
        if self.save_dir != self.user_dir_base:
            self.create_dir()
        self.update_lower_table()

    def get_shot_dir(self):
        code = self.cmbPlatform.currentData()
        platform = next((p for p in self.platforms_config if p["code"] == code), None)
        if platform is None:
            return
        subdir   = platform["subdir"]
        dir_type = platform.get("dir_type", "gun")
        if dir_type == "laser":
            curr_shot_dir = '/'.join([self.laser_dir_base.rstrip('/'), subdir, self.year])
        else:
            curr_shot_dir = '/'.join([self.shot_dir_base.rstrip('/'), subdir, self.year])

        code_to_idx = {p["code"]: i for i, p in enumerate(self.platforms_config)}

        if os.path.isdir(curr_shot_dir):
            self.platform_name = code
            self.shot_dir = curr_shot_dir
            dir_string_check = '-' + self.platform_name + '-'
            try:
                highest_num = sorted(
                    [f for f in os.listdir(curr_shot_dir)
                     if os.path.isdir(os.path.join(curr_shot_dir, f)) and dir_string_check in f]
                )
                if len(highest_num) == 0:
                    highest_num = 1
                else:
                    highest_num = self.check_if_next_shot(curr_shot_dir, highest_num)
                self.shot_number = int(highest_num)
            except Exception:
                print('Unable to locate next shot')
        else:
            msgNoDir = QtWidgets.QMessageBox()
            msgNoDir.setIcon(QtWidgets.QMessageBox.Information)
            msgNoDir.setText("The following directory was not found:")
            msgNoDir.setInformativeText(curr_shot_dir + '\n\nCreate the directory or investigate further')
            msgNoDir.setWindowTitle("Error")
            set_drive_btn = msgNoDir.addButton("Set Engineering Drive Location", QtWidgets.QMessageBox.ActionRole)
            msgNoDir.addButton(QtWidgets.QMessageBox.Ok)
            msgNoDir.exec_()
            if msgNoDir.clickedButton() == set_drive_btn:
                new_drive = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Select Engineering Drive Location", self.engineering_drive)
                if new_drive:
                    self.engineering_drive = new_drive
                    self.settings.setValue('EngineeringDrive', self.engineering_drive)
                    archiving = self._config.get("archiving", {})
                    gun_path   = archiving.get("gun_shot_data_path",   "Impact Facilities/Shot Data")
                    laser_path = archiving.get("laser_shot_data_path", "Laser Shock/Shot Data")
                    user_path  = archiving.get("user_data_path",       "User_Data")
                    self.shot_dir_base  = '/'.join([self.engineering_drive.rstrip('/'), gun_path])
                    self.laser_dir_base = '/'.join([self.engineering_drive.rstrip('/'), laser_path])
                    self.user_dir_base  = '/'.join([self.engineering_drive.rstrip('/'), user_path, self.year])
                    self.save_dir = self.user_dir_base
                    self.get_shot_dir()
            else:
                self.cmbPlatform.blockSignals(True)
                self.cmbPlatform.setCurrentIndex(code_to_idx.get(self.platform_name, 0))
                self.cmbPlatform.blockSignals(False)

    def get_user_dir(self, new_dir=False):
        if not new_dir:
            start = self.save_dir if (self.save_dir and os.path.isdir(self.save_dir)) else str(Path(__file__).parent)
            new_dir = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select User Data Directory', start)
        if new_dir:
            self.save_dir = new_dir
            self.sharedsettings.setValue('save_dir', new_dir)
            self.create_dir()
        else:
            print('Directory selection canceled')
        self.update_lower_table()

    def create_dir(self):
        self.save_dir_lower = '/'.join([self.save_dir.rstrip('/'), self.shot_name])
        if not os.path.isdir(self.save_dir_lower) and self.save_dir != self.user_dir_base and self.standard_name:
            os.makedirs(self.save_dir_lower)

    def check_if_next_shot(self, path, folders):
        skip_words = ['.xls', '.csv', '.txt', '.spe', '.tif', '.pdf']
        for shot in reversed(folders):
            for item in os.listdir(os.path.join(path, shot)):
                if os.path.isfile(os.path.join(path, shot, item)):
                    if not any(word in item for word in skip_words):
                        return int(shot[-3:]) + 1
        if int(shot[-3:]) == 0:
            return 1
        else:
            return shot[-3:]

    def copy_user_to_gun(self, update_shot=False):
        for file in os.listdir(self.save_dir_lower):
            if os.path.isfile(os.path.join(self.save_dir_lower, file)):
                if self.save_dir_lower != os.path.join(self.shot_dir, self.shot_name):
                    if file[:2] != '~$':
                        shutil.copy(os.path.join(self.save_dir_lower, file),
                                    os.path.join(self.shot_dir, self.shot_name))
        if update_shot and self.standard_name:
            self.change_platform()

    # ── Scope management helpers ─────────────────────────────────────────────
    def Clean_SelScopes(self):
        for i in self.Sel_Scope_Names.copy():
            if i not in self.All_Scopes:
                self.Sel_Scope_Names.discard(i)
                print("Discarded Scope: " + str(i))

    def _remove_scope(self, name):
        if name in self.Sel_Scope_Names:
            self.Sel_Scope_Names.remove(name)
            self.settings.setValue('scopes_selected', self.Sel_Scope_Names)
            self.scopes = {k: self.scope_ips[k] for k in self.scope_ips if k in self.Sel_Scope_Names}
            self.update_table()

    # ── Table update (threaded) ──────────────────────────────────────────────
    def update_table(self):
        keys = sorted(self.Sel_Scope_Names, key=natural_sort_key)
        self.table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            ip = self.scope_ips[key]
            chk = self._item(key, 0)
            self.table.setItem(row, 0, chk)
            ip_item = self._item('.' + ip.split('.')[-1], 4)
            ip_item.setFlags(ip_item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(row, 4, ip_item)
            for col in (1, 2, 3, 5, 6, 7):
                self.table.setItem(row, col, self._item("…", col))
            btn = QtWidgets.QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setFlat(True)
            btn.setStyleSheet(
                "QPushButton { color: #cc3333; font-weight: bold; font-size: 13px; border: none; border-radius: 3px; }"
                "QPushButton:hover { background-color: #ffdddd; color: #990000; }"
            )
            btn.setToolTip(f"Remove {key}")
            btn.clicked.connect(partial(self._remove_scope, key))
            cell = QtWidgets.QWidget()
            cell_layout = QtWidgets.QHBoxLayout(cell)
            cell_layout.addWidget(btn)
            cell_layout.setAlignment(QtCore.Qt.AlignCenter)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, self.table.columnCount() - 1, cell)
            worker = ScopeQueryWorker(row, key, ip, self.rm, parent=self)
            worker.signals.started.connect(self.onScopeStarted)
            worker.signals.finished.connect(self.onScopeFinished)
            worker.signals.error.connect(self.onScopeError)
            self.threadpool.start(worker)
        self.tupdate.setText(f"Updating… {datetime.datetime.now():%I:%M%p (%m/%d/%y)}")

    # ── Waveform viewer ──────────────────────────────────────────────────────
    def _show_scope_waveform_for_row(self, row):
        try:
            scope_name = self.table.item(row, 0).text()
            ip = self.scope_ips.get(scope_name)
            if not ip:
                QtWidgets.QMessageBox.warning(self, "Scope", f"Could not resolve IP for {scope_name}")
                return
            self.show_scope_data(ip, [1, 2, 3, 4], scope_name)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Waveform Error", f"{e}")

    def show_scope_data(self, ip, channels, scope_name=""):
        dlg = QtWidgets.QProgressDialog(
            f"Fetching waveform{' from ' + scope_name if scope_name else ''}…",
            "Cancel", 0, 0, self)
        dlg.setWindowTitle("Waveform")
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        dlg.show()

        worker = WaveformFetchWorker(ip, channels)

        def on_finished(t, y_data, enabled_channels):
            dlg.close()
            viewer = ScopeViewer()
            viewer.setWindowTitle(scope_name if scope_name else "Tektronix Oscilloscope Viewer")
            viewer.plot_data(t, y_data, enabled_channels)
            viewer.show()
            if not hasattr(self, '_waveform_viewers'):
                self._waveform_viewers = []
            self._waveform_viewers.append(viewer)

        def on_error(msg):
            dlg.close()
            QtWidgets.QMessageBox.warning(self, "Waveform Error", msg)

        worker.signals.finished.connect(on_finished)
        worker.signals.error.connect(on_error)
        self.threadpool.start(worker)

    # ── Command senders ──────────────────────────────────────────────────────
    def on_send_command(self):
        text, ok = QtWidgets.QInputDialog.getText(self, 'Send Command', 'Enter Command:')
        if ok and text:
            for i in self.Sel_Scope_Names:
                try:
                    with self._open(self.scope_ips.get(i)) as oscope:
                        oscope.write(str(text))
                        print("Sent %s to %s" % (text, i))
                except Exception as e:
                    print(f"FAILED on {i}: {e}")
            print("Done Sending Commands. NOTE: Update table to see changes")

    def send_single_cmd(self, tgt):
        text, ok = QtWidgets.QInputDialog.getText(self, 'Send Command', 'Enter Command:')
        keys = sorted(self.Sel_Scope_Names, key=natural_sort_key)
        ip = self.scope_ips[keys[tgt]]
        if ok and text:
            try:
                with self._open(ip) as oscope:
                    oscope.write(str(text))
                    print("Sent %s to %s" % (text, ip))
                    print("Done Sending Command. NOTE: Update table to see changes")
            except Exception as e:
                print(f"FAILED on {ip}: {e}")

    def send_single_Runstop(self, tgt):
        keys = sorted(self.Sel_Scope_Names, key=natural_sort_key)
        ip = self.scope_ips[keys[tgt]]
        try:
            with self._open(ip) as oscope:
                oscope.write('ACQ:STOPAFTER RUNST')
                print("Sent RunStop to %s" % ip)
        except Exception as e:
            print(f"FAILED on {ip}: {e}")

    def send_single_SingleShot(self, tgt):
        keys = sorted(self.Sel_Scope_Names, key=natural_sort_key)
        ip = self.scope_ips[keys[tgt]]
        try:
            with self._open(ip) as oscope:
                oscope.write('ACQ:STOPAFTER SEQUENCE')
                print("Sent SingleShot to %s" % ip)
        except Exception as e:
            print(f"FAILED on {ip}: {e}")

    def ArmEvent(self, event):
        quit_msg = ("*** ARMING THE SCOPE WILL ERASE ALL DATA ON SCOPES*** \n"
                    "          Are you sure you want ARM the Scopes?")
        reply = QtWidgets.QMessageBox.question(self, 'WARNING!', quit_msg,
                                               QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            for _, ip in self.scopes.items():
                try:
                    with self._open(ip) as oscope:
                        oscope.write('ACQ:STATE RUN')
                except Exception as e:
                    print(f"Error arming {ip}: {e}")
            time.sleep(2)
            self.update_table()
        else:
            print("DID NOT Arm Scopes")

    def SetSSEvent(self, event):
        for _, ip in self.scopes.items():
            try:
                with self._open(ip) as oscope:
                    oscope.write('ACQ:STOPAFTER SEQUENCE')
            except Exception as e:
                print(f"Error on {ip}: {e}")
        time.sleep(2)
        self.update_table()
        print("Finished Setting Selected Scopes to Single Shot!")

    def SetRSEvent(self, event):
        for _, ip in self.scopes.items():
            try:
                with self._open(ip) as oscope:
                    oscope.write('ACQ:STOPAFTER RUnsTOP')
            except Exception as e:
                print(f"Error on {ip}: {e}")
        time.sleep(2)
        self.update_table()
        print("Finished Setting Selected Scopes to RUN/STOP!")

    def SetscEvent(self, event):
        scope_num = 19
        if self.platform_name in ('1', '2'):
            scope_num = 3
        scope_ip = self.scope_ips.get('Scope' + str(scope_num))
        if not scope_ip:
            print(f"Scope{scope_num} not in current config")
            return
        print("Setting Scope " + str(scope_num))
        try:
            with self._open(scope_ip) as oscope:
                oscope.write('ACQuire:STOPAFTER RUnsTOP')
        except Exception as e:
            print(f"Error Could not set Scope {scope_num}: {e}")
        time.sleep(0.1)
        self.update_table()
        print("Finished Setting Scope %s to RUN/STOP!" % scope_num)

    def SoftTrigger(self, event):
        quit_msg = ("*** Triggering the scopes will disarm them*** \n"
                    "This is only for testing, Are you sure you want trigger the Scopes?")
        reply = QtWidgets.QMessageBox.question(self, 'WARNING!', quit_msg,
                                               QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No)
        if reply == QtWidgets.QMessageBox.Yes:
            for _, ip in self.scopes.items():
                try:
                    with self._open(ip) as oscope:
                        oscope.write('TRIGGER FORCE')
                except Exception as e:
                    print(f"Error on {ip}: {e}")
            time.sleep(2)
            self.update_table()
        else:
            print("DID NOT Trigger Scopes")

    # ── Save ─────────────────────────────────────────────────────────────────
    def on_save(self):
        self.update_table()
        time.sleep(0.1)

        # Resolve save directory once in the main thread to avoid races.
        # Mirrors V2_4 logic: if a custom staging dir is set (save_dir_lower != user_dir_base),
        # save there first and let copy_user_to_gun copy to the shot archive.
        # Otherwise save directly to the shot archive folder.
        if self.standard_name:
            save_dir = self.save_dir_lower
            if save_dir == self.user_dir_base:
                save_dir = '/'.join([self.shot_dir.rstrip('/'), self.shot_name])
                self.save_dir_lower = save_dir
            try:
                os.makedirs(save_dir, exist_ok=True)
            except OSError as e:
                QtWidgets.QMessageBox.critical(self, "Save Error",
                                               f"Cannot create save directory:\n{save_dir}\n{e}")
                return
        else:
            save_dir = self.save_dir

        self._save_total     = len(self.scopes)
        self._save_done      = 0
        self._save_failures  = []
        self._save_cancel    = threading.Event()
        self._save_remaining = set(self.scopes.keys())

        if self._save_total == 0:
            self._finalize_save()
            return

        scope_list = "\n  ".join(sorted(self._save_remaining, key=natural_sort_key))
        initial_label = "Saved 0 of %d scopes\n\nSaving:\n  %s" % (self._save_total, scope_list)
        self._save_progress = QtWidgets.QProgressDialog(
            initial_label, "Cancel", 0, self._save_total, self)
        self._save_progress.setWindowTitle("Saving Waveforms")
        self._save_progress.setWindowModality(QtCore.Qt.WindowModal)
        self._save_progress.setMinimumDuration(0)
        self._save_progress.setMinimumWidth(320)
        self._save_progress.setAutoClose(False)
        self._save_progress.setAutoReset(False)
        self._save_progress.canceled.connect(self._cancel_save)
        self._save_progress.setValue(0)
        self._save_progress.show()

        for scope_name, ip in self.scopes.items():
            worker = ScopeSaveWorker(scope_name, ip, self.shot_name, save_dir, self,
                                     self._save_cancel)
            worker.signals.finished.connect(self._on_scope_save_done)
            self.threadpool.start(worker)

    def _cancel_save(self):
        self._save_cancel.set()
        # Close immediately so setValue() calls from finishing workers don't re-show the dialog.
        if hasattr(self, '_save_progress') and self._save_progress:
            self._save_progress.close()
            self._save_progress = None

    @pyqtSlot(str, list)
    def _on_scope_save_done(self, scope_name, failures):
        self._save_failures.extend(failures)
        self._save_done += 1
        self._save_remaining.discard(scope_name)
        if hasattr(self, '_save_progress') and self._save_progress:
            self._save_progress.setValue(self._save_done)
            if self._save_remaining:
                scope_list = "\n  ".join(sorted(self._save_remaining, key=natural_sort_key))
                label = "Saved %d of %d scopes\n\nStill saving:\n  %s" % (
                    self._save_done, self._save_total, scope_list)
            else:
                label = "Saved %d of %d scopes" % (self._save_done, self._save_total)
            self._save_progress.setLabelText(label)
        if self._save_done == self._save_total:
            self._finalize_save()

    def _finalize_save(self):
        if hasattr(self, '_save_progress') and self._save_progress:
            self._save_progress.close()
            self._save_progress = None
        cancelled = hasattr(self, '_save_cancel') and self._save_cancel.is_set()
        print("Done saving data! %s" % datetime.datetime.now().strftime("%I:%M%p (%m/%d/%y)"))
        if self._save_failures:
            failstr = "***These Scopes/Channels did not save!***:\n" + "\n".join(self._save_failures)
            QtWidgets.QMessageBox.warning(self, "Save Failures", failstr)
        if cancelled:
            return
        if self.standard_name and self.archiving_enabled:
            print('Copying to shots directory...')
            self.copy_user_to_gun(update_shot=True)
            print('Done copying to shots directory!')

    def savedata(self, IP, channel, filename, save_dir=None, rm=None, cancel_event=None):
        """Save one channel to .isf. Returns 0=success, 1=IO failure, 2=channel not enabled."""
        if save_dir is None:
            # Legacy synchronous path — resolve from instance state
            if self.standard_name:
                save_dir = self.save_dir_lower
                if save_dir == self.user_dir_base:
                    save_dir = os.path.join(self.shot_dir, self.shot_name)
                    self.save_dir_lower = save_dir
            else:
                save_dir = self.save_dir

        updated_filename = filename
        increment = 0
        while os.path.isfile(os.path.join(save_dir, updated_filename)):
            increment += 1
            print(updated_filename + ' Exists.')
            if increment == 1:
                updated_filename = updated_filename[:-4] + '_1.isf'
            else:
                updated_filename = updated_filename[:-(4 + len(str(increment - 1)))] + str(increment) + '.isf'

        for attempt in range(5):
            if cancel_event is not None and cancel_event.is_set():
                return 1
            try:
                with self._open(IP, rm=rm) as oscope:
                    cht = oscope.query('SELect:CH%s?' % channel).strip()
                    print(cht)
                    # Strip header prefix if scope has headers on (e.g. ":SELECT:CH1 1")
                    if ' ' in cht:
                        cht = cht.split()[-1]
                    if int(cht) != 1:
                        print("Channel %s is not enabled" % channel)
                        return 2

                    f = open(os.path.join(save_dir, updated_filename), "w")
                    print("Saving... %s" % str(updated_filename))
                    oscope.write("DATA:SOURCE CH%s" % str(channel))
                    oscope.write('VERBose ON')
                    oscope.write('HEADer ON')
                    time.sleep(0.1)
                    oinfo = oscope.query('WFMOutpre:NR_Pt?')
                    f.write(oinfo.strip("\n") + ";")

                    try:
                        oinfo = oscope.query('WFMpre?')
                    except Exception:
                        print("-------------------------------------------------")
                        print("'WFMpre?' command failed. Trying individual commands.")
                        askData = oscope.query("DATa:SOUrce?")
                        print("Check channel? %s" % askData)
                        lstWFMpre = []
                        lstChecks = ["WFMPre:BYT_Nr?", "WFMPre:BIT_Nr?", "WFMPre:ENCdg?",
                                     "WFMPre:BN_Fmt?", "WFMPre:BYT_Or?", "WFMPre:WFId?",
                                     "WFMPre:NR_PT?", "WFMPre:PT_FMT?", "WFMPre:XUNIT?",
                                     "WFMPre:XINCR?", "WFMPre:XZERO?", "WFMPre:PT_OFF?",
                                     "WFMPre:YUNIT?", "WFMPre:YMUL?", "WFMPre:YOFF?", "WFMPre:YZERO?"]
                        for each in lstChecks:
                            try:
                                ask = oscope.query(each)
                                lstWFMpre.append(str(ask.strip()))
                            except Exception:
                                print("  Error: %s" % each)
                        oinfo = ';'.join(lstWFMpre)
                        print("final 'WFMpre?' output: %s" % oinfo)
                        print("-------------------------------------------------")

                    f.write(oinfo.strip("\n"))
                    f.write(";")
                    f.close()
                    oscope.write('CURVE?')
                    time.sleep(0.1)
                    data = oscope.read_raw()
                    f = open(os.path.join(save_dir, updated_filename), "ab")
                    f.write(data)
                    f.close()
                    oscope.write('HEADer OFF')
                return 0
            except visa.VisaIOError as e:
                print("\nIOError Saving %s (attempt %d/5): %s" % (updated_filename, attempt + 1, e))
                time.sleep(0.2)

        print("FAILED TO SAVE %s" % updated_filename)
        return 1


# ---------------------------------------------------------------------------
# Natural sort helper
# ---------------------------------------------------------------------------
_nsre = re.compile('([0-9]+)')

def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(_nsre, s)]


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    OscillGUI = OscApp()
    sys.exit(app.exec_())
