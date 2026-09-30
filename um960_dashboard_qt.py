#!/usr/bin/env python3
"""
Unicore UM960 GIS Dashboard (PySide6)
-------------------------------------
Desktop version of the web app's "GIS Dashboard" page:

  * WGS84 / BGS2005 (UTM 34N + CCS2005 Lambert) / GGRS87 Greek Grid coordinate cards
  * Receiver connection with automatic reconnect, ROVER mode check and NMEA output setup
  * NTRIP client: RTK corrections from a caster are written to the receiver over the same serial port
  * Constellation counts, DOP, skyplot and carrier signal strength (C/N0)
  * Live trajectory on an OpenStreetMap / satellite basemap (Leaflet in a QWebEngineView,
    loaded once and updated through JavaScript, so it does not reload or flicker)
  * Raw NMEA stream feed

Prerequisites:
  pip install PySide6 pyserial pyproj
  pip install keyring      # optional: remember the NTRIP password in the system keyring

Usage:
  python3 um960_dashboard_qt.py
  python3 um960_dashboard_qt.py --port /dev/serial/by-id/usb-FTDI_... --connect
"""

import sys
import os
import glob
import html
import json
import math
import time
import queue
import argparse
from collections import Counter
from dataclasses import dataclass

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: 'pyserial' is required. Install with 'pip install pyserial'.")
    sys.exit(1)

try:
    from PySide6.QtCore import Qt, QSettings, QThread, QTimer, QUrl, QRectF, QPointF, Signal
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QPen
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
        QWidget,
    )
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
except ImportError:
    print("Error: 'PySide6' (with QtWebEngine) is required. Install with 'pip install PySide6'.")
    sys.exit(1)

try:
    import keyring
except ImportError:
    keyring = None

from um960_core import convert_coordinates, UM960Configurator, NtripClient, NtripError, NtripFatalError, Rtcm3Scanner


# -------------------------------------------------------------------------
# THEME (Tailwind slate palette, matching the web app)
# -------------------------------------------------------------------------
SLATE_950 = "#020617"
SLATE_900 = "#0f172a"
SLATE_800 = "#1e293b"
SLATE_700 = "#334155"
SLATE_500 = "#64748b"
SLATE_400 = "#94a3b8"
SLATE_200 = "#e2e8f0"
EMERALD = "#10b981"
ROSE = "#e11d48"

CONSTELLATION_COLORS = {
    'GPS': '#3b82f6',      # Blue
    'BeiDou': '#ef4444',   # Red
    'GLONASS': '#10b981',  # Green
    'Galileo': '#eab308',  # Amber
    'QZSS': '#8b5cf6',     # Purple
}
OTHER_COLOR = '#6b7280'

# GGA fix quality -> (label, color)
FIX_QUALITY = {
    0: ("No Solution", '#ef4444'),
    1: ("GNSS Single", '#f59e0b'),
    2: ("DGPS", '#eab308'),
    4: ("RTK Fixed (Precise)", '#10b981'),
    5: ("RTK Float", '#3b82f6'),
    6: ("Dead Reckoning", SLATE_400),
}

# Approximate validity regions of the national grids (same as the web app)
BG_BOUNDS = (41.2, 44.3, 22.1, 28.6)   # min lat, max lat, min lon, max lon
GR_BOUNDS = (34.8, 41.8, 19.3, 28.3)

# Messages enabled on the receiver: position, DOP / satellites used, satellites in view
NMEA_MESSAGES = ("GPGGA", "GPRMC", "GPGSA", "GPGSV")

STYLESHEET = f"""
QWidget {{ color: {SLATE_200}; font-size: 12px; }}
QWidget#content, QScrollArea {{ background: {SLATE_950}; }}
QFrame#header {{ background: {SLATE_950}; border: none; border-bottom: 1px solid {SLATE_900}; }}
QFrame#card {{ background: {SLATE_900}; border: 1px solid {SLATE_800}; border-radius: 12px; }}
QFrame#inset {{ background: {SLATE_950}; border: 1px solid {SLATE_800}; border-radius: 8px; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid rgba(30, 41, 59, 128); }}
QLabel#cardTitle {{ color: white; font-weight: 700; font-size: 12px; letter-spacing: 1px; }}
QLabel#gridTitle {{ color: {SLATE_400}; font-weight: 700; font-size: 12px; letter-spacing: 1px; }}
QLabel#muted {{ color: {SLATE_400}; font-size: 11px; }}
QLabel#faint {{ color: {SLATE_500}; font-size: 10px; }}
QLabel#pill {{ background: {SLATE_900}; border: 1px solid {SLATE_800}; border-radius: 8px; padding: 5px 10px; }}
QComboBox, QLineEdit {{ background: {SLATE_900}; border: 1px solid {SLATE_800}; border-radius: 4px; padding: 4px 6px; color: white; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {EMERALD}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {SLATE_500}; }}
QComboBox QAbstractItemView {{ background: {SLATE_900}; selection-background-color: #065f46; }}
QPushButton, QToolButton {{ background: {SLATE_950}; border: 1px solid {SLATE_800}; border-radius: 4px;
    padding: 5px 10px; color: {SLATE_400}; font-weight: 700; font-size: 10px; }}
QPushButton:hover, QToolButton:hover {{ color: white; background: {SLATE_800}; }}
QPushButton:checked {{ background: rgba(16, 185, 129, 51); color: #34d399; border-color: rgba(16, 185, 129, 102); }}
QPushButton#connect {{ background: #059669; border-color: {EMERALD}; color: {SLATE_950}; font-weight: 900; font-size: 12px; padding: 7px; }}
QPushButton#connect:hover {{ background: #047857; }}
QPushButton#connect[connected="true"] {{ background: {ROSE}; border-color: #f43f5e; color: white; }}
QPushButton#connect[connected="true"]:hover {{ background: #be123c; }}
QProgressBar {{ background: {SLATE_900}; border: none; border-radius: 3px; }}
QPlainTextEdit {{ background: {SLATE_950}; border: 1px solid {SLATE_800}; border-radius: 8px; color: #34d399; padding: 6px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {SLATE_950}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {SLATE_800}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


def rgba(color, alpha):
    """'#10b981', 0.1 -> 'rgba(16, 185, 129, 26)'. Qt reads 8-digit hex as #AARRGGBB, unlike CSS."""
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


def mono_family():
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def fmt(value, spec, unit="", missing="---"):
    """Formats a number, or returns `missing` for NaN."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return missing
    return f"{value:{spec}}{unit}"


def in_bounds(lat, lon, bounds):
    min_lat, max_lat, min_lon, max_lon = bounds
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


# -------------------------------------------------------------------------
# NMEA PARSER
# -------------------------------------------------------------------------
@dataclass
class Satellite:
    constellation: str
    prn: int
    elevation: float  # degrees, NaN if unknown
    azimuth: float    # degrees, NaN if unknown
    snr: float        # C/N0 dB-Hz, 0 if not tracked
    used: bool = False


TALKER_CONSTELLATIONS = {
    'GP': 'GPS', 'GL': 'GLONASS', 'GA': 'Galileo', 'GB': 'BeiDou', 'BD': 'BeiDou', 'GQ': 'QZSS', 'QZ': 'QZSS',
}
# NMEA 4.10 GSA system IDs
GSA_SYSTEMS = {1: 'GPS', 2: 'GLONASS', 3: 'Galileo', 4: 'BeiDou', 5: 'QZSS'}

# Satellite data older than this is dropped (e.g. GSV output disabled, constellation lost)
SAT_DATA_MAX_AGE_S = 5.0


def constellation_from_prn(prn):
    """Fallback for mixed 'GN' talker sentences, based on typical NMEA PRN ranges."""
    if 1 <= prn <= 32:
        return 'GPS'
    if 65 <= prn <= 96:
        return 'GLONASS'
    if 193 <= prn <= 200:
        return 'QZSS'
    if 201 <= prn <= 263:
        return 'BeiDou'
    if 301 <= prn <= 336:
        return 'Galileo'
    return 'Other'


class NMEAParser:
    """
    Stateful NMEA decoder: GGA, RMC, VTG, HDT, GSA, GSV (NMEA 4.10 with signal IDs) and Unicore $KSXT.
    Sentences with a missing or wrong checksum are rejected.
    """
    def __init__(self):
        self.latitude = float('nan')
        self.longitude = float('nan')
        self.altitude = float('nan')
        self.speed_kmh = float('nan')
        self.course_deg = float('nan')
        self.heading_deg = float('nan')  # true heading (HDT / KSXT, dual antenna only)
        self.pitch_deg = float('nan')    # KSXT only
        self.roll_deg = float('nan')     # KSXT only
        self.fix_quality = 0
        self.num_satellites = 0
        self.hdop = float('nan')
        self.vdop = float('nan')
        self.pdop = float('nan')
        self.utc_time = ""
        self.date = ""
        self.bgs_east = self.bgs_north = float('nan')
        self.bgs_lam_east = self.bgs_lam_north = float('nan')
        self.gr_east = self.gr_north = float('nan')
        self.diff_age = float('nan')  # age of the corrections used by the receiver (GGA), seconds
        self.diff_station = ""        # reference station ID of those corrections
        self.last_gga = ""            # last GGA sentence with a position (forwarded to the NTRIP caster)
        self.last_sentence = ""
        self.checksum_errors = 0

        self._gsv_pending = {}  # (talker, signal_id) -> satellites of the sweep in progress
        self._gsv_sweeps = {}   # (talker, signal_id) -> (monotonic time, satellites of the last complete sweep)
        self._used = {}         # constellation -> (monotonic time, set of PRNs used in the solution)

    @property
    def has_position(self):
        return not (math.isnan(self.latitude) or math.isnan(self.longitude))

    @staticmethod
    def _checksum_ok(line):
        if '*' not in line:
            return False
        payload, checksum = line[1:].split('*', 1)
        calculated = 0
        for char in payload:
            calculated ^= ord(char)
        try:
            return int(checksum[:2], 16) == calculated
        except ValueError:
            return False

    @staticmethod
    def _parse_lat_lon(val, dir_char):
        """Converts NMEA DDMM.MMMMM / DDDMM.MMMMM into decimal degrees."""
        dot = val.index('.')
        decimal = float(val[:dot - 2]) + float(val[dot - 2:]) / 60.0
        return -decimal if dir_char in ('S', 'W') else decimal

    @staticmethod
    def _float(val, default=float('nan')):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _update_grids(self):
        (self.bgs_east, self.bgs_north, self.bgs_lam_east, self.bgs_lam_north,
         self.gr_east, self.gr_north) = convert_coordinates(self.latitude, self.longitude)

    def parse(self, line):
        """
        Parses one sentence. Returns True when a new position epoch was decoded (GGA / KSXT),
        False for other valid sentences and None for lines that are not valid NMEA.
        """
        line = line.strip()
        if not line.startswith('$'):
            return None
        if not self._checksum_ok(line):
            self.checksum_errors += 1
            return None

        parts = line[1:].split('*', 1)[0].split(',')
        sentence = parts[0].upper()
        talker, kind = sentence[:2], sentence[2:]
        self.last_sentence = sentence
        try:
            if kind == 'GGA':
                has_position = self._parse_gga(parts)
                if has_position:
                    self.last_gga = line
                return has_position
            if kind == 'RMC':
                self._parse_rmc(parts)
            elif kind == 'VTG':
                self.course_deg = self._float(parts[1], self.course_deg)
                self.speed_kmh = self._float(parts[7], self.speed_kmh)
            elif kind == 'HDT':
                self.heading_deg = self._float(parts[1])
            elif kind == 'GSA':
                self._parse_gsa(talker, parts)
            elif kind == 'GSV':
                self._parse_gsv(talker, parts)
            elif sentence == 'KSXT':
                return self._parse_ksxt(parts)
        except (IndexError, ValueError):
            return None
        return False

    def _parse_gga(self, parts):
        # $GNGGA,hhmmss.ss,lat,N,lon,E,quality,sats,hdop,alt,M,sep,M,age,station
        self.utc_time = self._format_time(parts[1])
        self.fix_quality = int(parts[6]) if parts[6] else 0
        self.num_satellites = int(parts[7]) if parts[7] else 0
        if not (parts[2] and parts[4]):
            return False
        self.latitude = self._parse_lat_lon(parts[2], parts[3])
        self.longitude = self._parse_lat_lon(parts[4], parts[5])
        self.altitude = self._float(parts[9])
        self.hdop = self._float(parts[8], self.hdop)
        self.diff_age = self._float(parts[13]) if len(parts) > 13 else float('nan')
        self.diff_station = parts[14] if len(parts) > 14 else ""
        self._update_grids()
        return True

    def _parse_rmc(self, parts):
        # $GNRMC,hhmmss.ss,status,lat,N,lon,E,speed_knots,course,ddmmyy,...
        self.utc_time = self._format_time(parts[1])
        if parts[9]:
            self.date = f"20{parts[9][4:6]}-{parts[9][2:4]}-{parts[9][0:2]}"
        if parts[2] == 'A':
            self.speed_kmh = self._float(parts[7], 0.0) * 1.852
            self.course_deg = self._float(parts[8])

    def _parse_gsa(self, talker, parts):
        # $GNGSA,mode,fix,prn1..prn12,pdop,hdop,vdop[,system_id]
        self.pdop = self._float(parts[15], self.pdop)
        self.hdop = self._float(parts[16], self.hdop)
        self.vdop = self._float(parts[17], self.vdop)
        prns = {int(p) for p in parts[3:15] if p}
        if len(parts) > 18 and parts[18].isdigit():
            constellation = GSA_SYSTEMS.get(int(parts[18]), 'Other')
        else:
            constellation = TALKER_CONSTELLATIONS.get(talker)
        if constellation:
            self._used[constellation] = (time.monotonic(), prns)

    def _parse_gsv(self, talker, parts):
        # $GPGSV,total_msgs,msg_num,sats_in_view,(prn,elev,azim,snr)x1..4[,signal_id]
        total, num = int(parts[1]), int(parts[2])
        fields = parts[4:]
        signal_id = fields.pop() if len(fields) % 4 == 1 else ''
        key = (talker, signal_id)
        if num == 1:
            self._gsv_pending[key] = []
        pending = self._gsv_pending.setdefault(key, [])
        for i in range(0, len(fields) - 3, 4):
            if fields[i]:
                prn = int(fields[i])
                constellation = TALKER_CONSTELLATIONS.get(talker) or constellation_from_prn(prn)
                pending.append(Satellite(constellation, prn, self._float(fields[i + 1]),
                                         self._float(fields[i + 2]), self._float(fields[i + 3], 0.0)))
        if num == total:
            self._gsv_sweeps[key] = (time.monotonic(), self._gsv_pending.pop(key))

    def _parse_ksxt(self, parts):
        # $KSXT,yyyymmddhhmmss.ss,heading,pitch,roll,lat,lon,alt,ve,vn,vu,hdg_status,pos_status,sats,...
        lat, lon = self._float(parts[5]), self._float(parts[6])
        if math.isnan(lat) or math.isnan(lon):
            return False
        self.utc_time = self._format_time(parts[1][8:])
        self.latitude, self.longitude = lat, lon
        self.altitude = self._float(parts[7])
        self.heading_deg = self._float(parts[2], self.heading_deg)
        self.pitch_deg = self._float(parts[3])
        self.roll_deg = self._float(parts[4])
        self.num_satellites = int(parts[13]) if parts[13] else self.num_satellites
        # Unicore position status -> GGA quality: 4/62 = RTK Fixed, 5/61 = RTK Float
        status = int(parts[12]) if parts[12] else 0
        self.fix_quality = {4: 4, 62: 4, 5: 5, 61: 5, 2: 2}.get(status, 1 if status else 0)
        self._update_grids()
        return True

    @staticmethod
    def _format_time(val):
        return f"{val[0:2]}:{val[2:4]}:{val[4:6]} UTC" if len(val) >= 6 else ""

    def satellites(self):
        """Satellites in view, one entry per satellite (best C/N0 over all its signals)."""
        now = time.monotonic()
        used = {c: prns for c, (t, prns) in self._used.items() if now - t < SAT_DATA_MAX_AGE_S}
        sats = {}
        for t, sweep in self._gsv_sweeps.values():
            if now - t >= SAT_DATA_MAX_AGE_S:
                continue
            for sat in sweep:
                key = (sat.constellation, sat.prn)
                best = sats.get(key)
                if best is None or sat.snr > best.snr:
                    sats[key] = Satellite(sat.constellation, sat.prn, sat.elevation, sat.azimuth, sat.snr,
                                          sat.prn in used.get(sat.constellation, ()))
        return sorted(sats.values(), key=lambda s: -s.snr)

    def constellation_counts(self):
        """Returns {constellation: (used, in_view)}."""
        counts = {}
        for sat in self.satellites():
            used, in_view = counts.get(sat.constellation, (0, 0))
            counts[sat.constellation] = (used + sat.used, in_view + 1)
        return counts


# -------------------------------------------------------------------------
# SERIAL READER THREAD
# -------------------------------------------------------------------------
class SerialWorker(QThread):
    """
    Reads lines from the receiver in a background thread. If the port fails (e.g. the USB adapter
    drops out) it is reopened every RECONNECT_DELAY_S until stop() is called.
    Data queued with send_raw() (RTCM corrections) is written from the same thread between reads,
    so it never interleaves with configuration commands.
    """
    line_received = Signal(str)
    status = Signal(str)
    connection_changed = Signal(bool)

    RECONNECT_DELAY_S = 2.0

    def __init__(self, port, baud, configure=True, save_config=False, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self.configure = configure
        self.save_config = save_config
        self._stop = False
        self._tx = queue.Queue(maxsize=256)
        self._ready = False  # port open and configured, accepting data to write

    def stop(self):
        self._stop = True
        self.wait()

    def send_raw(self, data):
        """Queues bytes for the receiver (thread-safe). Returns False if they were dropped."""
        if not self._ready:
            return False
        try:
            self._tx.put_nowait(data)
            return True
        except queue.Full:
            return False

    def _flush_tx(self, conn):
        while True:
            try:
                conn.write(self._tx.get_nowait())
            except queue.Empty:
                return

    def run(self):
        while not self._stop:
            try:
                # Short timeout: queued corrections are written between reads
                with serial.Serial(self.port, self.baud, timeout=0.2) as conn:
                    self.status.emit(f"Opened {self.port} at {self.baud} baud.")
                    self.connection_changed.emit(True)
                    if self.configure:
                        UM960Configurator(
                            conn, on_line=self.line_received.emit, log=self.status.emit,
                            save_config=self.save_config, nmea_messages=NMEA_MESSAGES,
                            should_stop=lambda: self._stop,
                        ).configure()
                    self._ready = True
                    while not self._stop:
                        self._flush_tx(conn)
                        raw = conn.readline()
                        if raw:
                            line = raw.decode('ascii', errors='replace').strip()
                            if line:
                                self.line_received.emit(line)
            except (serial.SerialException, OSError) as e:
                self._ready = False
                self.connection_changed.emit(False)
                if self._stop:
                    break
                self.status.emit(f"Serial error: {e}. Retrying in {self.RECONNECT_DELAY_S:g} s...")
                deadline = time.monotonic() + self.RECONNECT_DELAY_S
                while not self._stop and time.monotonic() < deadline:
                    self.msleep(100)
        self.connection_changed.emit(False)


class NtripWorker(QThread):
    """
    Receives RTK corrections from an NTRIP caster and hands them to `on_data` (the serial worker's
    send_raw). The receiver's latest GGA (set through the `gga` attribute) is sent to the caster every
    GGA_INTERVAL_S, as network/VRS mountpoints require. Reconnects after errors, except for bad
    credentials or an unknown mountpoint.
    """
    status = Signal(str, str)    # level ('info', 'ok', 'warn', 'error'), message
    state_changed = Signal(str)  # 'connecting', 'streaming', 'reconnecting', 'error', 'stopped'
    stats = Signal(dict)

    GGA_INTERVAL_S = 10.0
    STALL_TIMEOUT_S = 30.0
    RECONNECT_DELAY_S = 5.0

    def __init__(self, host, port, mountpoint, user, password, send_gga, on_data, parent=None):
        super().__init__(parent)
        self.client = NtripClient(host, port, mountpoint, user, password, timeout=5.0)
        self.send_gga = send_gga
        self.on_data = on_data
        self.gga = ""
        self._stop = False
        self._bytes = 0
        self._forwarded = 0
        self._last_data = None
        self._messages = Counter()
        self._last_stats = 0.0

    def stop(self):
        self._stop = True
        self.wait()

    def _emit_stats(self, force=False):
        now = time.monotonic()
        if force or now - self._last_stats >= 1.0:
            self._last_stats = now
            self.stats.emit({
                'bytes': self._bytes, 'forwarded': self._forwarded, 'messages': dict(self._messages),
                'age': None if self._last_data is None else now - self._last_data,
            })

    def _sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            self.msleep(100)

    def run(self):
        scanner = Rtcm3Scanner()
        mount = self.client.mountpoint
        while not self._stop:
            try:
                self.state_changed.emit('connecting')
                self.status.emit('info', f"connecting to {self.client.host}:{self.client.port}/{mount}...")
                reply = self.client.connect()
                self.client.sock.settimeout(1.0)  # lets the loop check stop / GGA timer every second
                self.state_changed.emit('streaming')
                self.status.emit('ok', f"connected to {mount} ({reply})")
                first_data = True
                last_gga = 0.0
                last_rx = time.monotonic()
                while not self._stop:
                    now = time.monotonic()
                    if self.send_gga and self.gga and now - last_gga >= self.GGA_INTERVAL_S:
                        self.client.send_gga(self.gga)
                        if last_gga == 0.0:
                            self.status.emit('info', "sending receiver position (GGA) to caster")
                        last_gga = now
                    try:
                        data = self.client.read()
                    except TimeoutError:
                        if now - last_rx > self.STALL_TIMEOUT_S:
                            raise NtripError(f"no data for {self.STALL_TIMEOUT_S:g} s")
                        self._emit_stats()
                        continue
                    if data:
                        if first_data:
                            first_data = False
                            self.status.emit('ok', "receiving corrections")
                        last_rx = self._last_data = time.monotonic()
                        self._bytes += len(data)
                        if self.on_data(data):
                            self._forwarded += len(data)
                        self._messages.update(scanner.feed(data))
                    self._emit_stats()
            except NtripFatalError as e:
                self.status.emit('error', f"{e}")
                self.state_changed.emit('error')
                break
            except (NtripError, OSError) as e:
                if self._stop:
                    break
                self.state_changed.emit('reconnecting')
                self.status.emit('warn', f"{e}. Reconnecting in {self.RECONNECT_DELAY_S:g} s...")
                self._sleep(self.RECONNECT_DELAY_S)
            finally:
                self.client.close()
        self._emit_stats(force=True)
        if self._stop:
            self.state_changed.emit('stopped')


# -------------------------------------------------------------------------
# MAP (Leaflet inside QWebEngineView)
# -------------------------------------------------------------------------
MAP_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html, body, #map { margin: 0; height: 100%; background: #020617; }
  .panel { background: rgba(15,23,42,.92); border: 1px solid #1e293b; border-radius: 6px; color: #cbd5e1;
           font: 11px monospace; padding: 8px 10px; line-height: 1.5; }
  .panel .t { color: #94a3b8; font-size: 10px; font-weight: bold; text-transform: uppercase; }
  .panel b { color: #fff; }
  .panel .sep { border-top: 1px solid #1e293b; margin-top: 4px; padding-top: 4px; font-size: 10px; }
  .bgs { color: #34d399; } .ggr { color: #60a5fa; }
  .compass { display: flex; align-items: center; gap: 8px; }
  .compass svg { width: 26px; height: 26px; transition: transform .3s; }
  .vehicle svg { overflow: visible; transition: transform .3s; }
  .offline { color: #94a3b8; font: 13px sans-serif; padding: 40px; text-align: center; }
</style></head>
<body><div id="map"></div>
<script>
const FIX_COLORS = {0: '#ef4444', 1: '#f59e0b', 2: '#eab308', 4: '#10b981', 5: '#3b82f6', 6: '#94a3b8'};
const MAX_TRACK_POINTS = 20000;
let map, marker, segment = null, segments = [], trackPoints = 0, follow = true, hasFix = false;
let infoDiv, compassDiv;

function colorOf(q) { return FIX_COLORS[q] || '#94a3b8'; }

function init() {
  map = L.map('map', {zoomControl: true}).setView([41.9, 23.3], 7);
  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 20, maxNativeZoom: 19, attribution: '&copy; OpenStreetMap contributors'});
  const imagery = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxZoom: 20, maxNativeZoom: 19, attribution: 'Imagery &copy; Esri'});
  osm.addTo(map);
  L.control.layers({'OpenStreetMap': osm, 'Satellite (Esri)': imagery}, null, {position: 'topleft'}).addTo(map);
  L.control.scale({imperial: false}).addTo(map);

  const Info = L.Control.extend({onAdd: () => {
    infoDiv = L.DomUtil.create('div', 'panel');
    infoDiv.innerHTML = '<div class="t">Grid Metrics (WGS84)</div>Waiting for position...';
    return infoDiv;
  }});
  new Info({position: 'topright'}).addTo(map);

  const Compass = L.Control.extend({onAdd: () => {
    compassDiv = L.DomUtil.create('div', 'panel compass');
    compassDiv.innerHTML =
      '<svg viewBox="-12 -12 24 24"><circle r="11" fill="none" stroke="#334155"/>' +
      '<path d="M0,-9 L4,2 L0,0 L-4,2 Z" fill="#10b981"/><path d="M0,9 L4,2 L0,0 L-4,2 Z" fill="#475569"/></svg>' +
      '<div><div class="t" id="hdgLabel">Heading</div><b id="hdgValue">N/A</b></div>';
    return compassDiv;
  }});
  new Compass({position: 'bottomleft'}).addTo(map);

  const icon = L.divIcon({className: 'vehicle', iconSize: [28, 28], iconAnchor: [14, 14],
    html: '<svg width="28" height="28" viewBox="-14 -14 28 28">' +
          '<circle r="12" fill="#ef4444" opacity=".25"/><circle r="6" fill="#ef4444" stroke="#fff" stroke-width="2"/>' +
          '<path class="arrow" d="M0,-13 L4,-6 L-4,-6 Z" fill="#fff" visibility="hidden"/></svg>'});
  marker = L.marker([0, 0], {icon: icon, interactive: false});

  // Dragging the map by hand stops following the receiver
  map.on('dragstart', () => { if (follow) { follow = false; console.log('um960:follow:0'); } });
}

function fmt(v, digits, unit) { return (v === null || v === undefined) ? '---' : v.toFixed(digits) + (unit || ''); }

// Called from Python for every position epoch
function update(d) {
  const ll = [d.lat, d.lon];
  if (!hasFix) {
    hasFix = true;
    marker.setLatLng(ll).addTo(map);
    map.setView(ll, 18);
  }
  marker.setLatLng(ll);

  // Track, split into segments coloured by fix quality
  if (!segment || segment.q !== d.q) {
    const start = segment ? [segment.line.getLatLngs().slice(-1)[0], ll] : [ll];
    segment = {q: d.q, line: L.polyline(start, {color: colorOf(d.q), weight: 4, opacity: .9}).addTo(map)};
    segments.push(segment);
  } else {
    segment.line.addLatLng(ll);
  }
  if (++trackPoints > MAX_TRACK_POINTS && segments.length > 1) {
    trackPoints -= segments[0].line.getLatLngs().length;
    map.removeLayer(segments.shift().line);
  }

  // Direction arrow on the marker and compass
  const el = marker.getElement();
  if (el) {
    el.querySelector('svg').style.transform = d.dir === null ? '' : 'rotate(' + d.dir + 'deg)';
    el.querySelector('.arrow').setAttribute('visibility', d.dir === null ? 'hidden' : 'visible');
  }
  compassDiv.querySelector('svg').style.transform = 'rotate(' + (d.dir || 0) + 'deg)';
  compassDiv.querySelector('#hdgLabel').textContent = d.dirLabel;
  compassDiv.querySelector('#hdgValue').textContent = fmt(d.dir, 1, '°');

  infoDiv.innerHTML =
    '<div class="t">Grid Metrics (WGS84)</div>' +
    'Lat: <b>' + d.lat.toFixed(8) + '°</b><br>Lon: <b>' + d.lon.toFixed(8) + '°</b>' +
    '<div class="sep">BGS-Y (UTM34N): <span class="bgs">' + fmt(d.bgsN, 3, ' m') + '</span><br>' +
    'GGR-X (EPSG:2100): <span class="ggr">' + fmt(d.grE, 3, ' m') + '</span></div>' +
    '<div class="sep"><span style="color:' + colorOf(d.q) + '">●</span> ' + d.qLabel +
    ' • ' + (follow ? 'Following' : 'Viewport locked') + '</div>';

  if (follow) map.panTo(ll, {animate: true, duration: 0.3});
}

function setFollow(on) {
  follow = on;
  if (on && hasFix) map.panTo(marker.getLatLng());
}
function centerOnReceiver() { if (hasFix) map.setView(marker.getLatLng(), Math.max(map.getZoom(), 16)); }
function clearTrack() {
  segments.forEach(s => map.removeLayer(s.line));
  segments = []; segment = null; trackPoints = 0;
}

if (typeof L === 'undefined') {
  document.getElementById('map').innerHTML =
    '<div class="offline">Map library could not be loaded (no internet connection?).<br>Position data is still shown in the dashboard.</div>';
  window.update = window.setFollow = window.centerOnReceiver = window.clearTrack = () => {};
} else {
  init();
}
</script></body></html>
"""


class MapPage(QWebEnginePage):
    """Forwards 'um960:' console messages from the page (e.g. follow mode turned off by dragging)."""
    follow_changed = Signal(bool)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith('um960:follow:'):
            self.follow_changed.emit(message.endswith(':1'))
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            print(f"Map JS error: {message} (line {line})", file=sys.stderr)


class MapView(QWebEngineView):
    """
    Leaflet map loaded once; positions are pushed with runJavaScript so the page never reloads.
    Calls made before the page has finished loading are queued.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        # Own profile: tiles are cached on disk between runs, and tile servers get an identifying User-Agent
        # (parented to the application: it must outlive the page that uses it)
        self.profile = QWebEngineProfile("um960-dashboard", QApplication.instance())
        self.profile.setHttpUserAgent(self.profile.httpUserAgent() + " UM960-GNSS-Dashboard/1.0")
        self.map_page = MapPage(self.profile, self)
        self.setPage(self.map_page)
        self._ready = False
        self._pending = []
        self.loadFinished.connect(self._on_load_finished)
        # An http base URL lets the page load the Leaflet CDN and tiles, and gives tile requests a Referer
        self.setHtml(MAP_HTML, QUrl("http://localhost/um960-dashboard/"))

    def _on_load_finished(self, ok):
        self._ready = True
        for script in self._pending:
            self.page().runJavaScript(script)
        self._pending.clear()

    def _call(self, function, *args):
        script = f"{function}({', '.join(json.dumps(a) for a in args)});"
        if self._ready:
            self.page().runJavaScript(script)
        else:
            self._pending.append(script)

    def update_position(self, data):
        self._call("update", data)

    def set_follow(self, on):
        self._call("setFollow", on)

    def center_on_receiver(self):
        self._call("centerOnReceiver")

    def clear_track(self):
        self._call("clearTrack")


# -------------------------------------------------------------------------
# CUSTOM PAINTED INSTRUMENTS
# -------------------------------------------------------------------------
class SkyplotWidget(QWidget):
    """Polar plot of satellites in view: centre = zenith, outer ring = horizon, north up."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.satellites = []
        self.setFixedSize(210, 210)

    def set_satellites(self, satellites):
        self.satellites = satellites
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        radius = min(self.width(), self.height()) / 2 - 14

        p.setPen(QPen(QColor(SLATE_800), 1))
        p.setBrush(QColor(SLATE_950))
        p.drawEllipse(c, radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(SLATE_800), 1, Qt.PenStyle.DashLine))
        for elevation in (30, 60):
            r = radius * (90 - elevation) / 90
            p.drawEllipse(c, r, r)
        p.drawLine(QPointF(c.x() - radius, c.y()), QPointF(c.x() + radius, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - radius), QPointF(c.x(), c.y() + radius))

        font = QFont(mono_family(), 8, QFont.Weight.Bold)
        p.setFont(font)
        p.setPen(QColor(SLATE_500))
        for label, dx, dy in (("N", 0, -1), ("S", 0, 1), ("E", 1, 0), ("W", -1, 0)):
            pos = QPointF(c.x() + dx * (radius + 8), c.y() + dy * (radius + 8))
            p.drawText(QRectF(pos.x() - 8, pos.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, label)

        p.setFont(QFont(mono_family(), 6, QFont.Weight.Black))
        for sat in self.satellites:
            if math.isnan(sat.elevation) or math.isnan(sat.azimuth):
                continue
            r = radius * (90 - max(0.0, min(90.0, sat.elevation))) / 90
            angle = math.radians(sat.azimuth - 90)
            pos = QPointF(c.x() + r * math.cos(angle), c.y() + r * math.sin(angle))
            color = QColor(CONSTELLATION_COLORS.get(sat.constellation, OTHER_COLOR))
            if not sat.used:
                color.setAlpha(110)
            p.setPen(QPen(QColor(SLATE_950), 1))
            p.setBrush(color)
            p.drawEllipse(pos, 8, 8)
            p.setPen(QColor("white"))
            p.drawText(QRectF(pos.x() - 8, pos.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, str(sat.prn))
        p.end()


class SignalBarsWidget(QWidget):
    """C/N0 bars for the strongest satellites, two columns."""
    ROWS = 8
    ROW_HEIGHT = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.satellites = []
        self.setMinimumHeight(self.ROWS * self.ROW_HEIGHT + 10)

    def set_satellites(self, satellites):
        self.satellites = satellites
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.satellites:
            p.setPen(QColor(SLATE_500))
            f = QFont(); f.setItalic(True); p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for GSV sentences in raw stream...")
            p.end()
            return

        col_width = (self.width() - 12) / 2
        font = QFont(mono_family(), 8)
        bold = QFont(mono_family(), 8, QFont.Weight.Bold)
        for i, sat in enumerate(self.satellites[:self.ROWS * 2]):
            x = (i // self.ROWS) * (col_width + 12)
            y = (i % self.ROWS) * self.ROW_HEIGHT + 2
            color = QColor(CONSTELLATION_COLORS.get(sat.constellation, OTHER_COLOR))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(x + 3, y + 6), 3, 3)
            p.setFont(bold)
            p.setPen(QColor("white") if sat.used else QColor(SLATE_400))
            p.drawText(QRectF(x + 10, y, col_width, 12), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{sat.constellation} PRN {sat.prn}" + (" ✓" if sat.used else ""))
            p.setFont(font)
            p.setPen(QColor(SLATE_400))
            p.drawText(QRectF(x, y, col_width, 12), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{sat.snr:.0f} dB-Hz")
            bar = QRectF(x, y + 13, col_width, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(SLATE_900))
            p.drawRoundedRect(bar, 2, 2)
            level = '#10b981' if sat.snr > 38 else ('#eab308' if sat.snr > 28 else '#ef4444')
            p.setBrush(QColor(level))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * min(1.0, sat.snr / 50), 4), 2, 2)
        p.end()


# -------------------------------------------------------------------------
# LAYOUT HELPERS
# -------------------------------------------------------------------------
def make_card(title=None, object_name="card"):
    frame = QFrame()
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    if title:
        label = QLabel(title.upper())
        label.setObjectName("cardTitle")
        layout.addWidget(label)
    return frame, layout


def make_chip(text, fg, bg, border):
    chip = QLabel(text)
    chip.setStyleSheet(f"background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 4px;"
                       f" padding: 1px 6px; font-family: '{mono_family()}'; font-size: 10px; font-weight: 600;")
    return chip


class KeyValueRow(QFrame):
    def __init__(self, key, value_color="white", last=False, value_size=13):
        super().__init__()
        if not last:
            self.setObjectName("row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 7)
        key_label = QLabel(key)
        key_label.setObjectName("muted")
        self.value = QLabel("---")
        self.value.setStyleSheet(f"color: {value_color}; font-family: '{mono_family()}'; font-weight: 700;"
                                 f" font-size: {value_size}px;")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(key_label)
        layout.addStretch()
        layout.addWidget(self.value)

    def set(self, text):
        self.value.setText(text)


class GridCard(QFrame):
    """One of the three coordinate reference cards at the top of the dashboard."""
    def __init__(self, title, accent, chips, rows):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        bar = QFrame()
        bar.setFixedSize(5, 12)
        bar.setStyleSheet(f"background: {accent}; border-radius: 2px;")
        title_label = QLabel(title.upper())
        title_label.setObjectName("gridTitle")
        header.addWidget(bar)
        header.addWidget(title_label)
        header.addStretch()
        for chip in chips:
            header.addWidget(make_chip(*chip))
        layout.addLayout(header)
        layout.addSpacing(4)

        self.rows = []
        for i, (key, color, size) in enumerate(rows):
            row = KeyValueRow(key, color, last=(i == len(rows) - 1), value_size=size)
            self.rows.append(row)
            layout.addWidget(row)

        footer = QHBoxLayout()
        self.status = QLabel()
        self.note = QLabel()
        self.note.setObjectName("faint")
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(self.note)
        layout.addLayout(footer)

    def set_status(self, ok, ok_text, bad_text):
        if ok:
            self.status.setText(ok_text)
            self.status.setStyleSheet(f"background: {rgba(EMERALD, 0.1)}; color: #34d399; border: 1px solid"
                                      f" {rgba(EMERALD, 0.2)}; border-radius: 4px; padding: 1px 6px; font-size: 10px;"
                                      " font-weight: 600;")
        else:
            self.status.setText(bad_text)
            self.status.setStyleSheet(f"background: {SLATE_800}; color: {SLATE_500}; border-radius: 4px;"
                                      " padding: 1px 6px; font-size: 10px; font-weight: 600;")


class ConstellationBar(QWidget):
    def __init__(self, name, label, scale):
        super().__init__()
        self.scale = scale
        color = CONSTELLATION_COLORS[name]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel(f"<span style='color:{color}'>●</span> {label}")
        title.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.count = QLabel("0 / 0")
        self.count.setStyleSheet(f"color: white; font-family: '{mono_family()}'; font-weight: 700;")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.count)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
        layout.addLayout(top)
        layout.addWidget(self.bar)

    def set_counts(self, used, in_view):
        self.count.setText(f"{used} / {in_view} sats")
        self.bar.setMaximum(max(self.scale, in_view))
        self.bar.setValue(in_view)


class DopTile(QFrame):
    def __init__(self, name, color):
        super().__init__()
        self.setObjectName("inset")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(2)
        title = QLabel(name)
        title.setStyleSheet(f"color: {SLATE_400}; font-size: 10px; font-weight: 700;")
        self.value = QLabel("---")
        self.value.setStyleSheet(f"color: {color}; font-family: '{mono_family()}'; font-weight: 700; font-size: 14px;")
        self.rating = QLabel("")
        self.rating.setObjectName("faint")
        for w in (title, self.value, self.rating):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(w)

    def set(self, dop):
        self.value.setText(fmt(dop, ".1f"))
        if math.isnan(dop):
            self.rating.setText("")
        else:
            self.rating.setText(next(r for limit, r in ((1, "Ideal"), (2, "Excellent"), (5, "Good"), (10, "Moderate"),
                                                        (20, "Fair"), (math.inf, "Poor")) if dop <= limit))


# -------------------------------------------------------------------------
# MAIN WINDOW
# -------------------------------------------------------------------------
# USB-UART bridges typically used with GNSS modules, preferred when choosing a default port
UART_BRIDGE_HINTS = ("FTDI", "FT232", "UART", "CP210", "CH34", "PL2303", "Prolific")


def list_serial_ports():
    """
    Stable /dev/serial/by-id names first (they survive re-enumeration), then plain device names.
    Within each group, USB-UART bridges come before other devices (e.g. microcontroller debug ports).
    """
    is_bridge = lambda name: any(hint.lower() in name.lower() for hint in UART_BRIDGE_HINTS)
    by_id = sorted(glob.glob("/dev/serial/by-id/*"), key=lambda p: (not is_bridge(p), p))
    # Legacy /dev/ttyS* ports without hardware behind them report hwid 'n/a'
    devices = sorted((p for p in serial.tools.list_ports.comports() if p.hwid != 'n/a'),
                     key=lambda p: (not is_bridge(f"{p.description} {p.manufacturer}"), p.device))
    return by_id + [p.device for p in devices]


class DashboardWindow(QMainWindow):
    UI_REFRESH_MS = 250
    FEED_MAX_LINES = 500
    NTRIP_LOG_LINES = 50
    NTRIP_LOG_COLORS = {'info': SLATE_400, 'ok': '#34d399', 'warn': '#fbbf24', 'error': '#f87171'}

    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("Unicore UM960 – GIS Dashboard")
        self.resize(1500, 1000)

        self.parser = NMEAParser()
        self.settings = QSettings("um960", "gis-dashboard")
        self.worker = None
        self.ntrip = None
        self.ntrip_state = 'stopped'
        self.ntrip_stats = {}
        self.is_connected = False
        self._dirty = False
        self._feed_pending = []
        self._line_count = 0
        self._rate_t0 = time.monotonic()

        content = QWidget()
        content.setObjectName("content")
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        main = QVBoxLayout()
        main.setContentsMargins(24, 20, 24, 24)
        main.setSpacing(20)
        main.addLayout(self._build_grid_cards())
        bento = QHBoxLayout()
        bento.setSpacing(20)
        bento.addLayout(self._build_left_column(args), 4)
        bento.addWidget(self._build_map_card(), 8)
        main.addLayout(bento, 1)
        main.addWidget(self._build_feed_card())
        root.addLayout(main, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        content.setMinimumHeight(1050)
        self.setCentralWidget(scroll)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(self.UI_REFRESH_MS)
        self._refresh_all()

        if args.connect:
            QTimer.singleShot(0, self._toggle_connection)
        if args.ntrip:
            QTimer.singleShot(0, self._toggle_ntrip)

    # --- construction -----------------------------------------------------
    def _build_header(self):
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 14, 24, 14)

        icon = QLabel("📡")
        icon.setStyleSheet(f"background: {rgba(EMERALD, 0.1)}; border: 1px solid {rgba(EMERALD, 0.2)};"
                           " border-radius: 10px; padding: 6px; font-size: 20px;")
        title = QLabel("Unicore UM960")
        title.setStyleSheet("color: white; font-size: 19px; font-weight: 700;")
        tag = make_chip("RTK / GNSS", SLATE_400, SLATE_900, SLATE_800)
        subtitle = QLabel("Geographic Grids & Western Bulgaria-Northern Greece Cadastral Dashboard")
        subtitle.setStyleSheet(f"color: {SLATE_400}; font-size: 11px;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addWidget(tag)
        title_row.addStretch()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addLayout(title_row)
        text.addWidget(subtitle)
        layout.addWidget(icon)
        layout.addSpacing(6)
        layout.addLayout(text)
        layout.addStretch()

        self.hdr_parser = QLabel()
        self.hdr_connection = QLabel()
        self.hdr_sats = QLabel()
        self.hdr_heading = QLabel()
        self.hdr_ntrip = QLabel()
        self.hdr_fix = QLabel()
        for w in (self.hdr_parser, self.hdr_connection, self.hdr_ntrip, self.hdr_sats, self.hdr_heading):
            w.setObjectName("pill")
            layout.addWidget(w)
        layout.addWidget(self.hdr_fix)
        return header

    def _build_grid_cards(self):
        row = QHBoxLayout()
        row.setSpacing(20)
        self.card_wgs = GridCard(
            "WGS84 Reference", "#06b6d4", [("Geodetic", "#22d3ee", "#083344", rgba("#155e75", 0.4))],
            [("LATITUDE", "white", 14), ("LONGITUDE", "white", 14), ("ELLIPSOIDAL HT", "#22d3ee", 14)])
        self.card_bgs = GridCard(
            "БГС2005 (Bulgaria)", "#14b8a6",
            [("EPSG:7803", "#2dd4bf", "#042f2e", rgba("#115e59", 0.3)), ("EPSG:7801", SLATE_200, SLATE_800, SLATE_700)],
            [("UTM 34N EASTING (X)", "white", 14), ("UTM 34N NORTHING (Y)", "white", 14),
             ("CCS2005 LAMBERT E / N", "#5eead4", 12)])
        self.card_bgs.note.setText("UTM Zone 34")
        self.card_gr = GridCard(
            "HGRS87 / Greek Grid", "#3b82f6", [("EPSG:2100", "#60a5fa", "#172554", rgba("#1e3a8a", 0.4))],
            [("GREEK EASTING (DX)", "white", 14), ("GREEK NORTHING (DY)", "white", 14),
             ("CENTRAL MERIDIAN", "#60a5fa", 14)])
        self.card_gr.rows[2].set("24° 00' 00\" E")
        self.card_gr.note.setText("k0 = 0.9996")
        for card in (self.card_wgs, self.card_bgs, self.card_gr):
            row.addWidget(card, 1)
        return row

    def _build_left_column(self, args):
        column = QVBoxLayout()
        column.setSpacing(20)

        # 1. Receiver connection
        card, layout = make_card("⚙  Receiver Connection")
        inset = QFrame()
        inset.setObjectName("inset")
        inset_layout = QGridLayout(inset)
        inset_layout.setContentsMargins(12, 12, 12, 12)
        inset_layout.setVerticalSpacing(6)

        port_label = QLabel("SERIAL PORT")
        port_label.setObjectName("faint")
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        refresh = QToolButton()
        refresh.setText("⟳")
        refresh.setToolTip("Rescan serial ports")
        refresh.clicked.connect(self._populate_ports)
        self._populate_ports()
        # Command line port, otherwise the last port used, otherwise the first (preferred) one listed
        port = args.port or self.settings.value("port", "")
        if port:
            self.port_combo.setCurrentText(port)
        self._show_port_start()

        baud_label = QLabel("BAUD RATE")
        baud_label.setObjectName("faint")
        self.baud_combo = QComboBox()
        for baud in (9600, 115200, 230400, 460800, 921600):
            self.baud_combo.addItem(f"{baud} bps", baud)
        baud = args.baud or int(self.settings.value("baud", 115200))
        self.baud_combo.setCurrentIndex(max(0, self.baud_combo.findData(baud)))

        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connect")
        self.connect_button.clicked.connect(self._toggle_connection)

        self.configure_check = QCheckBox("Configure receiver on connect (ROVER mode, NMEA output)")
        self.configure_check.setChecked(not args.no_configure)
        self.save_check = QCheckBox("Save configuration changes to receiver (SAVECONFIG)")
        self.save_check.setChecked(args.save_config)
        for check in (self.configure_check, self.save_check):
            check.setStyleSheet(f"color: {SLATE_400}; font-size: 11px;")

        self.conn_status = QLabel("Not connected.")
        self.conn_status.setWordWrap(True)
        self.conn_status.setObjectName("muted")

        inset_layout.addWidget(port_label, 0, 0, 1, 2)
        inset_layout.addWidget(self.port_combo, 1, 0)
        inset_layout.addWidget(refresh, 1, 1)
        inset_layout.addWidget(baud_label, 2, 0, 1, 2)
        inset_layout.addWidget(self.baud_combo, 3, 0, 1, 2)
        inset_layout.addWidget(self.configure_check, 4, 0, 1, 2)
        inset_layout.addWidget(self.save_check, 5, 0, 1, 2)
        inset_layout.addWidget(self.connect_button, 6, 0, 1, 2)
        inset_layout.addWidget(self.conn_status, 7, 0, 1, 2)
        layout.addWidget(inset)
        column.addWidget(card)

        column.addWidget(self._build_ntrip_card(args))

        # 3. Constellations, DOP, motion
        card, layout = make_card("📶  BDS / GNSS Constellations")
        inset = QFrame()
        inset.setObjectName("inset")
        bars = QVBoxLayout(inset)
        bars.setContentsMargins(12, 12, 12, 12)
        bars.setSpacing(8)
        legend = QLabel("used in fix / in view")
        legend.setObjectName("faint")
        legend.setAlignment(Qt.AlignmentFlag.AlignRight)
        bars.addWidget(legend)
        self.constellation_bars = {}
        for name, label, scale in (("BeiDou", "BeiDou (BDS)", 14), ("GPS", "GPS (USA)", 12),
                                   ("GLONASS", "GLONASS (RU)", 10), ("Galileo", "Galileo (EU)", 8)):
            bar = ConstellationBar(name, label, scale)
            self.constellation_bars[name] = bar
            bars.addWidget(bar)
        layout.addWidget(inset)

        dops = QHBoxLayout()
        dops.setSpacing(8)
        self.dop_tiles = {}
        for name, color in (("HDOP", "#2dd4bf"), ("VDOP", "#22d3ee"), ("PDOP", "#818cf8")):
            tile = DopTile(name, color)
            self.dop_tiles[name] = tile
            dops.addWidget(tile)
        layout.addLayout(dops)

        self.motion_label = QLabel()
        self.attitude_label = QLabel()
        for w in (self.motion_label, self.attitude_label):
            w.setStyleSheet(f"background: {SLATE_950}; border: 1px solid {SLATE_800}; border-radius: 8px;"
                            f" padding: 8px 10px; font-family: '{mono_family()}'; font-size: 11px; color: {SLATE_200};")
            layout.addWidget(w)
        self.attitude_label.hide()  # only shown when $KSXT (dual antenna) data is received
        column.addWidget(card)
        column.addStretch()
        return column

    def _build_ntrip_card(self, args):
        card, layout = make_card("📡  NTRIP Corrections (RTK)")
        inset = QFrame()
        inset.setObjectName("inset")
        grid = QGridLayout(inset)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setVerticalSpacing(6)

        def label(text):
            w = QLabel(text)
            w.setObjectName("faint")
            return w

        self.ntrip_host = QLineEdit(self.settings.value("ntrip/host", ""))
        self.ntrip_host.setPlaceholderText("caster, e.g. igs-ip.net")
        self.ntrip_port = QLineEdit(str(self.settings.value("ntrip/port", "2101")))
        self.ntrip_port.setFixedWidth(70)
        self.ntrip_mount = QLineEdit(self.settings.value("ntrip/mountpoint", ""))
        self.ntrip_mount.setPlaceholderText("mountpoint")
        self.ntrip_user = QLineEdit(self.settings.value("ntrip/user", ""))
        self.ntrip_user.setPlaceholderText("user name")
        self.ntrip_password = QLineEdit()
        self.ntrip_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.ntrip_password.setPlaceholderText("password")
        self.ntrip_password.setText(self._load_ntrip_password())

        self.ntrip_gga_check = QCheckBox("Send receiver position (GGA) to caster")
        self.ntrip_gga_check.setChecked(self.settings.value("ntrip/send_gga", True, type=bool))
        self.ntrip_remember_check = QCheckBox("Remember password (system keyring)")
        if keyring is None:
            self.ntrip_remember_check.setEnabled(False)
            self.ntrip_remember_check.setToolTip("Install the 'keyring' package to enable, or set the "
                                                 "UM960_NTRIP_PASSWORD environment variable")
        else:
            self.ntrip_remember_check.setChecked(self.settings.value("ntrip/remember_password", False, type=bool))
        for check in (self.ntrip_gga_check, self.ntrip_remember_check):
            check.setStyleSheet(f"color: {SLATE_400}; font-size: 11px;")

        self.ntrip_button = QPushButton("Start Corrections")
        self.ntrip_button.setObjectName("connect")
        self.ntrip_button.clicked.connect(self._toggle_ntrip)
        self.ntrip_status = QLabel("Corrections off.")
        self.ntrip_status.setWordWrap(True)
        self.ntrip_status.setObjectName("muted")
        self.ntrip_info = QLabel()
        self.ntrip_info.setWordWrap(True)
        self.ntrip_info.setStyleSheet(f"font-family: '{mono_family()}'; font-size: 11px; color: {SLATE_200};")
        log_header = QHBoxLayout()
        log_header.addWidget(label("NTRIP LOG"))
        log_header.addStretch()
        clear_log = QToolButton()
        clear_log.setText("Clear")
        log_header.addWidget(clear_log)
        self.ntrip_log = QPlainTextEdit()
        self.ntrip_log.setReadOnly(True)
        self.ntrip_log.setMaximumBlockCount(self.NTRIP_LOG_LINES)
        self.ntrip_log.setFont(QFont(mono_family(), 8))
        self.ntrip_log.setFixedHeight(110)
        self.ntrip_log.setStyleSheet(f"color: {SLATE_200}; padding: 4px;")
        self.ntrip_log.setPlaceholderText("Connection events and caster errors appear here.")
        clear_log.clicked.connect(self.ntrip_log.clear)

        grid.addWidget(label("CASTER / PORT"), 0, 0, 1, 2)
        grid.addWidget(self.ntrip_host, 1, 0)
        grid.addWidget(self.ntrip_port, 1, 1)
        grid.addWidget(label("MOUNTPOINT"), 2, 0, 1, 2)
        grid.addWidget(self.ntrip_mount, 3, 0, 1, 2)
        grid.addWidget(label("USER / PASSWORD"), 4, 0, 1, 2)
        credentials = QHBoxLayout()
        credentials.addWidget(self.ntrip_user)
        credentials.addWidget(self.ntrip_password)
        grid.addLayout(credentials, 5, 0, 1, 2)
        grid.addWidget(self.ntrip_gga_check, 6, 0, 1, 2)
        grid.addWidget(self.ntrip_remember_check, 7, 0, 1, 2)
        grid.addWidget(self.ntrip_button, 8, 0, 1, 2)
        grid.addWidget(self.ntrip_status, 9, 0, 1, 2)
        grid.addWidget(self.ntrip_info, 10, 0, 1, 2)
        grid.addLayout(log_header, 11, 0, 1, 2)
        grid.addWidget(self.ntrip_log, 12, 0, 1, 2)
        layout.addWidget(inset)
        return card

    def _build_map_card(self):
        card, layout = make_card()
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("🗺  LIVE TRAJECTORY TRACKING CANVAS")
        title.setObjectName("cardTitle")
        subtitle = QLabel("OpenStreetMap / satellite basemap, track coloured by fix quality")
        subtitle.setObjectName("muted")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()

        self.follow_button = QPushButton("⌖ Follow")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setToolTip("Keep the map centred on the receiver")
        center_button = QPushButton("📍 Center")
        center_button.setToolTip("Center the map on the receiver")
        clear_button = QPushButton("Clear Track")
        header.addWidget(self.follow_button)
        header.addWidget(center_button)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self.map_view = MapView()
        self.map_view.setMinimumHeight(420)
        layout.addWidget(self.map_view, 1)

        self.follow_button.toggled.connect(self.map_view.set_follow)
        self.map_view.map_page.follow_changed.connect(self.follow_button.setChecked)
        center_button.clicked.connect(self.map_view.center_on_receiver)
        clear_button.clicked.connect(self.map_view.clear_track)

        # Skyplot + signal strength
        instruments = QHBoxLayout()
        instruments.setSpacing(20)
        sky = QVBoxLayout()
        sky_title = QLabel("SKYPLOT SATELLITE DOME")
        sky_title.setStyleSheet(f"color: {SLATE_400}; font-size: 11px; font-weight: 700;")
        sky_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.skyplot = SkyplotWidget()
        sky.addWidget(sky_title)
        sky.addWidget(self.skyplot, 0, Qt.AlignmentFlag.AlignHCenter)
        instruments.addLayout(sky, 5)

        signal = QVBoxLayout()
        signal_title = QLabel("LIVE CARRIER SIGNAL POWER (C/N0 dB-Hz)")
        signal_title.setStyleSheet(f"color: {SLATE_400}; font-size: 11px; font-weight: 700;")
        inset = QFrame()
        inset.setObjectName("inset")
        inset_layout = QVBoxLayout(inset)
        inset_layout.setContentsMargins(12, 10, 12, 10)
        self.signal_bars = SignalBarsWidget()
        inset_layout.addWidget(self.signal_bars)
        signal.addWidget(signal_title)
        signal.addWidget(inset)
        instruments.addLayout(signal, 7)

        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {SLATE_800};")
        layout.addWidget(separator)
        layout.addLayout(instruments)
        return card

    def _build_feed_card(self):
        card, layout = make_card()
        header = QHBoxLayout()
        title = QLabel("<span style='color:#10b981'>●</span>  UNICORE UM960 RAW NMEA STREAM FEED")
        title.setObjectName("cardTitle")
        self.feed_stats = QLabel()
        self.feed_stats.setObjectName("faint")
        clear = QPushButton("CLEAR FEED")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.feed_stats)
        header.addSpacing(10)
        header.addWidget(clear)
        layout.addLayout(header)

        self.feed = QPlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setMaximumBlockCount(self.FEED_MAX_LINES)
        self.feed.setFont(QFont(mono_family(), 9))
        self.feed.setFixedHeight(150)
        clear.clicked.connect(self.feed.clear)
        layout.addWidget(self.feed)
        return card

    # --- connection -------------------------------------------------------
    def _populate_ports(self):
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItems(list_serial_ports())
        if current:
            self.port_combo.setCurrentText(current)
        self._show_port_start()

    def _show_port_start(self):
        # Long by-id names: show the distinctive start (adapter vendor) and the full name as tooltip
        self.port_combo.lineEdit().setCursorPosition(0)
        self.port_combo.setToolTip(self.port_combo.currentText())

    def _toggle_connection(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
            self._log_status("Disconnected by user.")
            self._set_connected(False)
            return

        port = self.port_combo.currentText().strip()
        if not port:
            self._log_status("Select a serial port first.")
            return
        self.settings.setValue("port", port)
        self.settings.setValue("baud", self.baud_combo.currentData())
        self.worker = SerialWorker(port, self.baud_combo.currentData(), configure=self.configure_check.isChecked(),
                                   save_config=self.save_check.isChecked(), parent=self)
        self.worker.line_received.connect(self._on_line)
        self.worker.status.connect(self._log_status)
        self.worker.connection_changed.connect(self._set_connected)
        self.worker.start()
        self.connect_button.setText("Disconnect")
        self.connect_button.setProperty("connected", True)
        self._repolish(self.connect_button)
        for w in (self.port_combo, self.baud_combo, self.configure_check, self.save_check):
            w.setEnabled(False)

    def _set_connected(self, connected):
        self.is_connected = connected
        if self.worker is None:
            self.connect_button.setText("Connect")
            self.connect_button.setProperty("connected", False)
            self._repolish(self.connect_button)
            for w in (self.port_combo, self.baud_combo, self.configure_check, self.save_check):
                w.setEnabled(True)
        self._dirty = True

    @staticmethod
    def _repolish(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # --- NTRIP --------------------------------------------------------------
    def _ntrip_inputs(self):
        return (self.ntrip_host, self.ntrip_port, self.ntrip_mount, self.ntrip_user, self.ntrip_password,
                self.ntrip_gga_check, self.ntrip_remember_check)

    def _load_ntrip_password(self):
        """Keyring (if enabled and available), otherwise the UM960_NTRIP_PASSWORD environment variable."""
        if keyring is not None and self.settings.value("ntrip/remember_password", False, type=bool):
            try:
                password = keyring.get_password("um960-gis-dashboard", self.settings.value("ntrip/user", ""))
                if password:
                    return password
            except Exception as e:
                print(f"Keyring unavailable: {e}", file=sys.stderr)
        return os.environ.get("UM960_NTRIP_PASSWORD", "")

    def _save_ntrip_settings(self):
        user = self.ntrip_user.text().strip()
        self.settings.setValue("ntrip/host", self.ntrip_host.text().strip())
        self.settings.setValue("ntrip/port", self.ntrip_port.text().strip())
        self.settings.setValue("ntrip/mountpoint", self.ntrip_mount.text().strip())
        self.settings.setValue("ntrip/user", user)
        self.settings.setValue("ntrip/send_gga", self.ntrip_gga_check.isChecked())
        self.settings.setValue("ntrip/remember_password", self.ntrip_remember_check.isChecked())
        if keyring is None:
            return
        try:
            if self.ntrip_remember_check.isChecked():
                keyring.set_password("um960-gis-dashboard", user, self.ntrip_password.text())
            else:
                keyring.delete_password("um960-gis-dashboard", user)
        except Exception:
            pass  # nothing stored, or no keyring backend

    def _toggle_ntrip(self):
        if self.ntrip:
            self.ntrip.stop()
            self.ntrip = None
            self._set_ntrip_state('stopped')
            self._log_ntrip('info', "stopped by user")
            return

        host, mount = self.ntrip_host.text().strip(), self.ntrip_mount.text().strip()
        try:
            port = int(self.ntrip_port.text().strip())
        except ValueError:
            port = 0
        if not host or not mount or not 0 < port < 65536:
            self._log_ntrip('warn', "enter caster, port and mountpoint first")
            return
        self._save_ntrip_settings()
        self.ntrip_stats = {}
        self.ntrip = NtripWorker(host, port, mount, self.ntrip_user.text().strip(), self.ntrip_password.text(),
                                 self.ntrip_gga_check.isChecked(), self._forward_corrections, parent=self)
        self.ntrip.gga = self.parser.last_gga
        self._rx_using_corrections = not math.isnan(self.parser.diff_age)
        self._logged_fix_quality = self.parser.fix_quality
        self.ntrip.status.connect(self._log_ntrip)
        self.ntrip.state_changed.connect(self._on_ntrip_state)
        self.ntrip.stats.connect(self._on_ntrip_stats)
        self.ntrip.start()
        self.ntrip_button.setText("Stop Corrections")
        self.ntrip_button.setProperty("connected", True)
        self._repolish(self.ntrip_button)
        for w in self._ntrip_inputs():
            w.setEnabled(False)

    def _forward_corrections(self, data):
        """Called from the NTRIP thread: queue corrections for the serial thread."""
        worker = self.worker
        return worker.send_raw(data) if worker else False

    def _is_current_ntrip(self):
        # Queued signals from a worker the user already stopped must not touch the current state
        return self.sender() is self.ntrip

    def _on_ntrip_state(self, state):
        if not self._is_current_ntrip():
            return
        if state == 'error':
            self.ntrip = None  # the worker ends itself on fatal errors
        self._set_ntrip_state(state)

    def _set_ntrip_state(self, state):
        self.ntrip_state = state
        if state in ('error', 'stopped'):
            self.ntrip_button.setText("Start Corrections")
            self.ntrip_button.setProperty("connected", False)
            self._repolish(self.ntrip_button)
            for w in self._ntrip_inputs():
                w.setEnabled(True)
            if keyring is None:
                self.ntrip_remember_check.setEnabled(False)
        self._dirty = True

    def _on_ntrip_stats(self, stats):
        if not self._is_current_ntrip():
            return
        self.ntrip_stats = stats
        self._dirty = True

    def _log_ntrip(self, level, message):
        """Shows an NTRIP event in the status line, the NTRIP log (timestamped) and the raw feed."""
        color = self.NTRIP_LOG_COLORS.get(level, SLATE_200)
        self.ntrip_status.setText(f"<span style='color:{color}'>{html.escape(message)}</span>")
        self.ntrip_log.appendHtml(f"<span style='color:{SLATE_500}'>{time.strftime('%H:%M:%S')}</span> "
                                  f"<span style='color:{color}'>{html.escape(message)}</span>")
        self._feed_pending.append(f"<span style='color:#a78bfa'>» NTRIP: {html.escape(message)}</span>")

    def _track_correction_use(self):
        """While corrections run, log when the receiver starts/stops using them and fix quality changes."""
        p = self.parser
        using = not math.isnan(p.diff_age)
        if using != self._rx_using_corrections:
            self._rx_using_corrections = using
            if using:
                station = f" (station {p.diff_station})" if p.diff_station else ""
                self._log_ntrip('ok', f"receiver is using the corrections{station}")
            else:
                self._log_ntrip('warn', "receiver stopped using corrections")
        if p.fix_quality != self._logged_fix_quality:
            self._logged_fix_quality = p.fix_quality
            label = FIX_QUALITY.get(p.fix_quality, (f"quality {p.fix_quality}", ""))[0]
            self._log_ntrip('ok' if p.fix_quality in (4, 5) else 'info', f"receiver fix: {label}")

    def _log_status(self, message):
        self.conn_status.setText(message)
        self._feed_pending.append(f"<span style='color:#fbbf24'>» {html.escape(message)}</span>")

    # --- data -------------------------------------------------------------
    def _on_line(self, line):
        self._line_count += 1
        result = self.parser.parse(line)
        if result is None and line.startswith('$'):
            self._feed_pending.append(f"<span style='color:#f87171'>{html.escape(line)}  ✗ checksum</span>")
        else:
            self._feed_pending.append(html.escape(line))
        if result is not None:
            self._dirty = True
        if result and self.parser.has_position:
            self._push_map_position()
            if self.ntrip:
                self.ntrip.gga = self.parser.last_gga
                self._track_correction_use()

    def _direction(self):
        """True heading if available, otherwise course over ground while moving."""
        p = self.parser
        if not math.isnan(p.heading_deg):
            return p.heading_deg, "Heading"
        if not math.isnan(p.course_deg) and not math.isnan(p.speed_kmh) and p.speed_kmh > 1.0:
            return p.course_deg, "Course"
        return None, "Heading"

    def _push_map_position(self):
        p = self.parser
        direction, label = self._direction()
        clean = lambda v: None if math.isnan(v) else v
        self.map_view.update_position({
            'lat': p.latitude, 'lon': p.longitude, 'q': p.fix_quality,
            'qLabel': FIX_QUALITY.get(p.fix_quality, (f"Quality {p.fix_quality}", ""))[0],
            'dir': direction, 'dirLabel': label,
            'bgsN': clean(p.bgs_north), 'grE': clean(p.gr_east),
        })

    def _refresh(self):
        if self._feed_pending:
            self.feed.appendHtml("<br>".join(self._feed_pending))
            self._feed_pending.clear()
        elapsed = time.monotonic() - self._rate_t0
        if elapsed >= 2.0:
            rate = self._line_count / elapsed
            self._line_count, self._rate_t0 = 0, time.monotonic()
            self.feed_stats.setText(f"{rate:.1f} lines/s • checksum errors: {self.parser.checksum_errors}")
        if self._dirty:
            self._dirty = False
            self._refresh_all()

    def _refresh_all(self):
        p = self.parser
        mono = mono_family()

        # Header badges
        self.hdr_parser.setText(f"<span style='color:{SLATE_400}'>NMEA Parser:</span> "
                                f"<b style='font-family:{mono}'>{p.last_sentence or 'WAITING'}</b>")
        if self.is_connected:
            self.hdr_connection.setText(f"<span style='color:{EMERALD}'>●</span> <b style='font-family:{mono}'>LIVE PORT</b>")
        elif self.worker:
            self.hdr_connection.setText(f"<span style='color:#f59e0b'>●</span> <b style='font-family:{mono}'>RECONNECTING</b>")
        else:
            self.hdr_connection.setText(f"<span style='color:{SLATE_500}'>●</span> <b style='font-family:{mono}'>OFFLINE</b>")
        ntrip_colors = {'streaming': EMERALD, 'connecting': '#f59e0b', 'reconnecting': '#f59e0b', 'error': '#ef4444'}
        ntrip_text = {'streaming': 'ON', 'connecting': 'CONNECTING', 'reconnecting': 'RECONNECTING', 'error': 'ERROR'}
        self.hdr_ntrip.setText(f"<span style='color:{ntrip_colors.get(self.ntrip_state, SLATE_500)}'>●</span> "
                               f"<span style='color:{SLATE_400}'>NTRIP:</span> "
                               f"<b style='font-family:{mono}'>{ntrip_text.get(self.ntrip_state, 'OFF')}</b>")
        self.hdr_sats.setText(f"<span style='color:{SLATE_400}'>Satellites:</span> "
                              f"<b style='font-family:{mono}'>{p.num_satellites}</b>")
        direction, label = self._direction()
        self.hdr_heading.setText(f"<span style='color:{SLATE_400}'>{label}:</span> "
                                 f"<b style='font-family:{mono}'>{fmt(direction, '.1f', '°', 'N/A')}</b>")
        q_label, q_color = FIX_QUALITY.get(p.fix_quality, (f"Quality {p.fix_quality}", SLATE_400))
        self.hdr_fix.setText(f"● {q_label}")
        self.hdr_fix.setStyleSheet(f"color: {q_color}; background: {rgba(q_color, 0.1)}; border: 1px solid"
                                   f" {rgba(q_color, 0.27)}; border-radius: 12px; padding: 5px 12px; font-weight: 600;")

        # Grid cards
        if p.has_position:
            self.card_wgs.rows[0].set(f"{abs(p.latitude):.8f}° {'N' if p.latitude >= 0 else 'S'}")
            self.card_wgs.rows[1].set(f"{abs(p.longitude):.8f}° {'E' if p.longitude >= 0 else 'W'}")
            self.card_wgs.rows[2].set(fmt(p.altitude, ".3f", " m"))
        self.card_wgs.set_status(p.has_position and p.fix_quality > 0, f"✓ {q_label.upper()}", "⚠ NO POSITION")
        self.card_wgs.note.setText(f"{p.date} {p.utc_time}".strip() or "Waiting for time")

        self.card_bgs.rows[0].set(fmt(p.bgs_east, ".3f", " m"))
        self.card_bgs.rows[1].set(fmt(p.bgs_north, ".3f", " m"))
        self.card_bgs.rows[2].set("---" if math.isnan(p.bgs_lam_east) else
                                  f"{p.bgs_lam_east:.1f} E, {p.bgs_lam_north:.1f} N")
        in_bg = p.has_position and in_bounds(p.latitude, p.longitude, BG_BOUNDS)
        self.card_bgs.set_status(in_bg, "✓ INSIDE BULGARIA BOUNDARY", "⚠ OUTSIDE BULGARIA")

        self.card_gr.rows[0].set(fmt(p.gr_east, ".3f", " m"))
        self.card_gr.rows[1].set(fmt(p.gr_north, ".3f", " m"))
        in_gr = p.has_position and in_bounds(p.latitude, p.longitude, GR_BOUNDS)
        self.card_gr.set_status(in_gr, "✓ INSIDE GREECE CADASTRE", "⚠ OUTSIDE REGIONAL VALIDITY")

        # Constellations, DOP, motion
        counts = p.constellation_counts()
        for name, bar in self.constellation_bars.items():
            bar.set_counts(*counts.get(name, (0, 0)))
        self.dop_tiles["HDOP"].set(p.hdop)
        self.dop_tiles["VDOP"].set(p.vdop)
        self.dop_tiles["PDOP"].set(p.pdop)
        self.motion_label.setText(f"<span style='color:{SLATE_400}'>Motion:</span>  Speed "
                                  f"<b>{fmt(p.speed_kmh, '.1f', ' km/h')}</b>  |  Course <b>{fmt(p.course_deg, '.1f', '°')}</b>")
        if not math.isnan(p.pitch_deg):
            self.attitude_label.show()
            self.attitude_label.setText(f"<span style='color:{SLATE_400}'>Antenna Sensors:</span>  Pitch "
                                        f"<b>{p.pitch_deg:.2f}°</b>  |  Roll <b>{fmt(p.roll_deg, '.2f', '°')}</b>")

        self._refresh_ntrip_info()

        satellites = p.satellites()
        self.skyplot.set_satellites(satellites)
        self.signal_bars.set_satellites(satellites)

    def _refresh_ntrip_info(self):
        p, st = self.parser, self.ntrip_stats
        muted = lambda text: f"<span style='color:{SLATE_400}'>{text}</span>"
        lines = []
        if st:
            kb = st['bytes'] / 1024
            dropped = st['bytes'] - st['forwarded']
            lines.append(f"{muted('Received:')} {kb:.1f} kB" +
                         (f"  <span style='color:#f87171'>({dropped / 1024:.1f} kB not sent - receiver"
                          f" not connected)</span>" if dropped else ""))
            if st['age'] is not None and self.ntrip_state in ('streaming', 'reconnecting'):
                age_color = EMERALD if st['age'] < 5 else ('#f59e0b' if st['age'] < 30 else '#ef4444')
                lines.append(f"{muted('Last data:')} <span style='color:{age_color}'>{st['age']:.1f} s ago</span>")
            if st['messages']:
                lines.append(f"{muted('RTCM:')} " + " ".join(f"{t}×{n}" for t, n in sorted(st['messages'].items())))
        if not math.isnan(p.diff_age):
            station = f", station {p.diff_station}" if p.diff_station else ""
            lines.append(f"{muted('Receiver:')} using corrections, age {p.diff_age:.1f} s{station}")
        elif self.ntrip_state == 'streaming' and st.get('forwarded'):
            lines.append(f"{muted('Receiver:')} not using corrections yet")
        self.ntrip_info.setText("<br>".join(lines))
        self.ntrip_info.setVisible(bool(lines))

    def closeEvent(self, event):
        if self.ntrip:
            self.ntrip.stop()
        if self.worker:
            self.worker.stop()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="Unicore UM960 GIS Dashboard (PySide6)")
    parser.add_argument('--port', type=str, default=None, help="Serial port, e.g. /dev/ttyUSB0 or a /dev/serial/by-id/ path")
    parser.add_argument('--baud', type=int, default=None, help="Serial baud rate for UM960 (default: last used, or 115200)")
    parser.add_argument('--connect', action='store_true', help="Connect immediately on start")
    parser.add_argument('--no-configure', action='store_true', help="Do not check/configure the receiver on connect")
    parser.add_argument('--save-config', action='store_true', help="Persist configuration changes with SAVECONFIG")
    parser.add_argument('--ntrip', action='store_true', help="Start NTRIP corrections on start (saved caster settings)")
    args = parser.parse_args()

    # Silence harmless Qt/QPA Wayland warnings (e.g., QWindow::requestActivate() messages)
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland=false")

    app = QApplication(sys.argv)
    app.setApplicationName("UM960 GIS Dashboard")
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(SLATE_950))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(SLATE_200))
    palette.setColor(QPalette.ColorRole.Base, QColor(SLATE_900))
    palette.setColor(QPalette.ColorRole.Text, QColor(SLATE_200))
    palette.setColor(QPalette.ColorRole.Button, QColor(SLATE_900))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(SLATE_200))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#059669"))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

    window = DashboardWindow(args)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
