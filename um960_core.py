"""
Shared Unicore UM960 support code
---------------------------------
Used by both the matplotlib tracker (unicore_um960_tracker.py) and the
PySide6 dashboard (um960_dashboard_qt.py):

  * convert_coordinates() - WGS84 -> BGS2005 UTM 34N, BGS2005 CCS2005 Lambert, GGRS87 Greek Grid
  * UM960Configurator     - receiver command interface (ROVER mode check, NMEA output setup)

Command reference: SparkFun Unicore GNSS Arduino library
https://docs.sparkfun.com/SparkFun_UM980_Triband_GNSS_RTK_Breakout/arduino_examples/
"""

import sys
import math
import time

try:
    import pyproj
except ImportError:
    print("Error: 'pyproj' is required for coordinate conversions. Install with 'pip install pyproj'.")
    sys.exit(1)


# -------------------------------------------------------------------------
# COORDINATE CONVERSIONS (pyproj transformers with multiple fallbacks)
# -------------------------------------------------------------------------
# EPSG:4326 - WGS84 Geographic Lat/Lon
# EPSG:7803 - BGS2005 / UTM zone 34N (Western Bulgaria)
# EPSG:7801 - BGS2005 / CCS2005 (Bulgarian National Lambert)
# EPSG:2100 - GGRS87 / Greek Grid (Greece national grid used for Cadastre)

wgs84_to_bgs_utm_transformer = None
wgs84_to_bgs_lam_transformer = None
wgs84_to_greek_transformer = None

# 1. BGS2005 / UTM Zone 34N (Try EPSG:7803, custom PROJ4 string, or standard EPSG:32634)
utm_candidates = [
    "EPSG:7803",
    "+proj=utm +zone=34 +ellps=GRS80 +units=m +no_defs",
    "EPSG:32634"
]
for crs_to in utm_candidates:
    try:
        wgs84_to_bgs_utm_transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_to, always_xy=True)
        break
    except Exception:
        continue

# 2. BGS2005 / CCS2005 Lambert (Try EPSG:7801, or the equivalent PROJ4 string)
lambert_candidates = [
    "EPSG:7801",
    "+proj=lcc +lat_0=42.6678756833333 +lon_0=25.5 +lat_1=42 +lat_2=43.3333333333333 +x_0=500000 +y_0=4725824.3591 +ellps=GRS80 +units=m +no_defs"
]
for crs_to in lambert_candidates:
    try:
        wgs84_to_bgs_lam_transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_to, always_xy=True)
        break
    except Exception:
        continue

# 3. GGRS87 / Greek Grid (Try EPSG:2100, or custom PROJ4 string)
greek_candidates = [
    "EPSG:2100",
    "+proj=tmerc +lat_0=0 +lon_0=24 +k=0.9996 +x_0=500000 +y_0=0 +ellps=GRS80 +towgs84=-199.87,74.79,246.62,0,0,0,0 +units=m +no_defs"
]
for crs_to in greek_candidates:
    try:
        wgs84_to_greek_transformer = pyproj.Transformer.from_crs("EPSG:4326", crs_to, always_xy=True)
        break
    except Exception:
        continue

# Print warning status instead of crashing to allow execution to proceed
if not wgs84_to_bgs_utm_transformer:
    print("Warning: BGS UTM 34N Transformer could not be initialized. Visualizer will fall back to manual mathematical approximation.", file=sys.stderr)
if not wgs84_to_bgs_lam_transformer:
    print("Warning: BGS Lambert Transformer could not be initialized.", file=sys.stderr)
if not wgs84_to_greek_transformer:
    print("Warning: Greek Grid Transformer could not be initialized.", file=sys.stderr)


def convert_coordinates(lat, lon):
    """
    Transforms WGS84 latitude and longitude to Bulgarian BGS2005 and Greek Grid grids.
    Returns: (bgs_east, bgs_north, bgs_lam_east, bgs_lam_north, gr_east, gr_north)
    """
    bgs_e, bgs_n = float('nan'), float('nan')
    bgs_le, bgs_ln = float('nan'), float('nan')
    gr_e, gr_n = float('nan'), float('nan')

    # Convert to UTM Zone 34N
    if wgs84_to_bgs_utm_transformer:
        try:
            bgs_e, bgs_n = wgs84_to_bgs_utm_transformer.transform(lon, lat)
        except Exception:
            pass

    # High-precision manual mathematical approximation for UTM Zone 34N if pyproj solver fails
    if (math.isnan(bgs_e) or math.isnan(bgs_n)) and not (math.isnan(lat) or math.isnan(lon)):
        try:
            # Central meridian for UTM Zone 34 is 21.0 degrees longitude
            dlon = (lon - 21.0) * math.pi / 180.0
            lat_rad = lat * math.pi / 180.0
            k0 = 0.9996
            fe = 500000.0
            a = 6378137.0
            e2 = 0.00669437999014

            # Meridional distance
            M = (6367449.1458 * lat_rad -
                 16038.5087 * math.sin(2.0 * lat_rad) +
                 16.8326 * math.sin(4.0 * lat_rad) -
                 0.0220 * math.sin(6.0 * lat_rad))

            N = a / math.sqrt(1.0 - e2 * math.sin(lat_rad)**2)
            T = math.tan(lat_rad)**2
            C = e2 / (1.0 - e2) * math.cos(lat_rad)**2
            A = dlon * math.cos(lat_rad)

            bgs_e = fe + k0 * N * (A + (1.0 - T + C) * A**3 / 6.0 + (5.0 - 18.0 * T + T**2 + 72.0 * C - 58.0 * e2) * A**5 / 120.0)
            bgs_n = k0 * (M + N * math.tan(lat_rad) * (A**2 / 2.0 + (5.0 - T + 9.0 * C + 4.0 * C**2) * A**4 / 24.0 + (61.0 - 58.0 * T + T**2 + 600.0 * C - 330.0 * e2) * A**6 / 720.0))
        except Exception:
            pass

    # Convert to Bulgarian National Lambert
    if wgs84_to_bgs_lam_transformer:
        try:
            bgs_le, bgs_ln = wgs84_to_bgs_lam_transformer.transform(lon, lat)
        except Exception:
            pass

    # Convert to Greek Grid
    if wgs84_to_greek_transformer:
        try:
            gr_e, gr_n = wgs84_to_greek_transformer.transform(lon, lat)
        except Exception:
            pass

    return bgs_e, bgs_n, bgs_le, bgs_ln, gr_e, gr_n


# -------------------------------------------------------------------------
# UM960 COMMAND INTERFACE (see SparkFun Unicore GNSS Arduino library)
#   Query:   "MODE"              -> #MODE,97,GPS,FINE,...;MODE ROVER SURVEY,*18
#   Command: "MODE ROVER SURVEY" -> $command,MODE ROVER SURVEY,response: OK*..
#   Errors contain "PARSING", e.g. $command,...,response: PARSING FAILD NO MATCHING FUNC ...
# -------------------------------------------------------------------------
class UM960Configurator:
    """
    Checks and adjusts the receiver configuration over an open serial connection.
    NMEA traffic received while waiting for command replies is passed to `on_line`,
    status messages go to `log`.
    """
    def __init__(self, serial_conn, on_line=None, log=print, rover_mode="SURVEY", check_mode=True,
                 save_config=False, nmea_messages=("GPGGA", "GPRMC"), nmea_interval=1.0, should_stop=None):
        self.serial = serial_conn
        self.on_line = on_line or (lambda line: None)
        self.log = log
        self.rover_mode = rover_mode      # ROVER sub-type: SURVEY, UAV, AUTOMOTIVE or "" for plain ROVER
        self.check_mode = check_mode      # Query MODE and switch to ROVER if needed
        self.save_config = save_config    # Persist configuration changes with SAVECONFIG
        self.nmea_messages = [m.upper() for m in nmea_messages]  # NMEA logs to enable (empty = skip)
        self.nmea_interval = nmea_interval  # Output interval in seconds (1 = 1Hz, 0.2 = 5Hz)
        self.should_stop = should_stop or (lambda: False)  # Aborts waiting for replies, e.g. on disconnect

    def _send_and_wait(self, command, match, timeout=3.0):
        """
        Sends a command and waits for a response line containing `match`.
        NMEA traffic received meanwhile is still passed to on_line.
        Returns the matching response line, or None on timeout.
        """
        self.serial.reset_input_buffer()
        self.serial.write(f"{command}\r\n".encode('ascii'))
        self.serial.flush()

        deadline = time.time() + timeout
        while time.time() < deadline and not self.should_stop():
            line_str = self.serial.readline().decode('ascii', errors='ignore').strip()
            if not line_str:
                continue
            if match.upper() in line_str.upper():
                return line_str
            self.on_line(line_str)
        return None

    def query_mode(self, attempts=2):
        """Returns the receiver's current mode string (e.g. 'MODE ROVER SURVEY'), or None."""
        # The first query after opening the port is occasionally lost, so retry once
        for _ in range(attempts):
            response = self._send_and_wait("MODE", "#MODE")
            if response and ';' in response:
                # '#MODE,...;MODE ROVER SURVEY,*18' -> 'MODE ROVER SURVEY'
                return response.split(';', 1)[1].split('*', 1)[0].strip(' ,')
        return None

    def query_loglist(self, timeout=2.0):
        """
        Returns (port, {message: interval_s}) for the messages logged on the port we are connected to.
        Reply format:
            <LOGLIST COM1 16528 98.000000 UNKNOWN 1 275.000000 276423 537 18
            <\t1
            <\tGPGGA COM1 1
        """
        if self._send_and_wait("LOGLIST", "$command,LOGLIST") is None:
            return None, None

        port, logs = None, {}
        deadline = time.time() + timeout
        while time.time() < deadline and not self.should_stop():
            line_str = self.serial.readline().decode('ascii', errors='ignore').strip()
            if not line_str.startswith('<'):
                if port is not None and not line_str:
                    break  # blank line terminates the list
                self.on_line(line_str)
                continue
            fields = line_str.lstrip('<').split()
            if fields and fields[0] == "LOGLIST" and len(fields) > 1:
                port = fields[1]
            elif len(fields) >= 3:
                try:
                    logs[(fields[0].upper(), fields[1].upper())] = float(fields[2])
                except ValueError:
                    pass
        if port is None:
            return None, None
        return port, {msg: rate for (msg, p), rate in logs.items() if p == port}

    def send_command(self, command, timeout=3.0):
        """Sends a configuration command. Returns True if the receiver replied with OK."""
        response = self._send_and_wait(command, f"$command,{command}", timeout)
        if response is None:
            self.log(f"UM960: no response to '{command}'")
            return False
        if "OK" not in response.upper():
            self.log(f"UM960: '{command}' rejected: {response}")
            return False
        return True

    def ensure_rover_mode(self):
        """
        Switches the UM960 to ROVER mode if it is currently configured as a BASE (or unknown).
        Returns (is_rover, changed).
        """
        current = self.query_mode()
        self.log(f"UM960 current mode: {current or 'unknown (no reply to MODE query)'}")
        if current and "ROVER" in current.upper():
            return True, False

        command = f"MODE ROVER {self.rover_mode}".strip()
        self.log(f"UM960: reconfiguring -> '{command}'")
        if not self.send_command(command):
            return False, False

        new_mode = self.query_mode()
        self.log(f"UM960 mode is now: {new_mode or 'unknown'}")
        return bool(new_mode and "ROVER" in new_mode.upper()), True

    def ensure_nmea_output(self):
        """
        Enables the configured NMEA messages on the port we are connected to, skipping ones
        already logged at the requested interval. Returns (ok, changed).
        """
        port, logs = self.query_loglist()
        if port is None:
            self.log("UM960: no reply to LOGLIST, enabling all NMEA messages")
            logs = {}
        else:
            active = ", ".join(f"{m}@{r:g}s" for m, r in logs.items()) or "none"
            self.log(f"UM960 connected on {port}, active logs: {active}")

        ok, enabled = True, []
        for msg in self.nmea_messages:
            if logs.get(msg) is not None and math.isclose(logs[msg], self.nmea_interval):
                continue
            # "<MSG> <interval>" applies to the port we are talking on
            if self.send_command(f"{msg} {self.nmea_interval:g}"):
                enabled.append(msg)
            else:
                ok = False
        if enabled:
            self.log(f"UM960: enabled {', '.join(enabled)} every {self.nmea_interval:g}s")
        return ok, bool(enabled)

    def configure(self):
        """Runs the enabled configuration steps and saves to NVM once if anything changed."""
        changed = False
        if self.check_mode:
            is_rover, mode_changed = self.ensure_rover_mode()
            changed |= mode_changed
            if not is_rover and not self.should_stop():
                self.log("Warning: could not confirm UM960 is in ROVER mode. Continuing anyway.")
        if self.should_stop():
            return
        if self.nmea_messages:
            nmea_ok, nmea_changed = self.ensure_nmea_output()
            changed |= nmea_changed
            if not nmea_ok:
                self.log("Warning: some NMEA messages could not be enabled.")

        if changed and self.save_config and not self.should_stop():
            if self.send_command("SAVECONFIG", timeout=5.0):
                self.log("UM960: configuration saved to NVM.")
