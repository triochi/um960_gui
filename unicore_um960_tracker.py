#!/usr/bin/env python3
"""
Unicore UM960 GNSS RTK NMEA Decoder and Real-time Tracker
---------------------------------------------------------
https://github.com/triochi/um960_gui

This script reads real-time NMEA stream from the Unicore UM960 module,
decodes position, RTK fix quality, DOP, heading, and satellite details,
converts the WGS84 geographic coordinates to Bulgarian BGS2005 UTM Zone 34N
and Greek HGRS87 / Greek Grid, and plots the real-time tracking trajectory.

Prerequisites:
  pip install pyserial pyproj matplotlib

Usage:
  python3 unicore_um960_tracker.py --port /dev/ttyUSB0 --baud 115200
  (Or use --simulate to run a demo without hardware!)
"""

import sys
import os
import time
import argparse
import math
import random
import threading
from collections import deque

# Silence harmless Qt/QPA Wayland warnings (e.g., QWindow::requestActivate() messages)
os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland=false"

try:
    import serial
except ImportError:
    print("Warning: 'pyserial' not installed. Live serial connection will not work.", file=sys.stderr)
    serial = None

try:
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
except ImportError:
    print("Error: 'matplotlib' is required for real-time potting. Install with 'pip install matplotlib'.")
    sys.exit(1)


from um960_core import convert_coordinates, UM960Configurator


# -------------------------------------------------------------------------
# NMEA PARSER ENGINE
# -------------------------------------------------------------------------
class UM960Decoder:
    """
    Stateful decoder for parsing standard NMEA sentences and Unicore proprietary sentences.
    Supports: GGA, RMC, GSA, GSV, HDT and Unicore-specific $KSXT messages.
    """
    def __init__(self):
        self.latitude = 0.0
        self.longitude = 0.0
        self.altitude = 0.0
        self.speed_kmh = 0.0
        self.course_deg = 0.0
        self.heading_deg = float('nan')
        self.fix_quality = 0 # 0=Unfixed, 1=Single, 2=DGPS, 4=RTK Fixed, 5=RTK Float
        self.num_satellites = 0
        self.hdop = 1.0
        self.timestamp = ""
        self.bgs_east = float('nan')
        self.bgs_north = float('nan')
        self.bgs_lam_east = float('nan')
        self.bgs_lam_north = float('nan')
        self.gr_east = float('nan')
        self.gr_north = float('nan')

    def parse_nmea_lat_lon(self, val, dir_char):
        """Helper to convert NMEA DDMM.MMMMM into decimal degrees"""
        if not val or not dir_char:
            return 0.0
        try:
            parts = val.split('.')
            deg_digits = len(parts[0]) - 2
            deg = float(parts[0][:deg_digits])
            minutes = float(parts[0][deg_digits:] + "." + parts[1])
            decimal = deg + (minutes / 60.0)
            if dir_char in ['S', 'W']:
                decimal = -decimal
            return decimal
        except Exception:
            return 0.0

    def parse_line(self, line: str):
        """Parses a single line of NMEA. Returns True if position was updated."""
        line = line.strip()
        if not line.startswith('$'):
            return False

        # Validate checksum if possible
        if '*' in line:
            payload, checksum = line.split('*', 1)
            calculated = 0
            for char in payload[1:]:
                calculated ^= ord(char)
            try:
                if int(checksum, 16) != calculated:
                    # Ignore checksum failure occasionally but try to parse
                    pass
            except ValueError:
                pass

        parts = line.split(',')
        sentence_type = parts[0]

        updated = False

        # 1. GGA: Global Positioning System Fix Data
        if sentence_type.endswith('GGA') and len(parts) >= 10:
            self.timestamp = parts[1]
            raw_lat = parts[2]
            lat_dir = parts[3]
            raw_lon = parts[4]
            lon_dir = parts[5]
            quality = parts[6]
            num_sats = parts[7]
            hdop = parts[8]
            alt = parts[9]

            if raw_lat and raw_lon:
                self.latitude = self.parse_nmea_lat_lon(raw_lat, lat_dir)
                self.longitude = self.parse_nmea_lat_lon(raw_lon, lon_dir)
                self.altitude = float(alt) if alt else 0.0
                self.fix_quality = int(quality) if quality else 0
                self.num_satellites = int(num_sats) if num_sats else 0
                self.hdop = float(hdop) if hdop else 1.0
                updated = True

        # 2. RMC: Recommended Minimum Specific GNSS Data
        elif sentence_type.endswith('RMC') and len(parts) >= 9:
            self.timestamp = parts[1]
            status = parts[2]
            raw_lat = parts[3]
            lat_dir = parts[4]
            raw_lon = parts[5]
            lon_dir = parts[6]
            speed_knots = parts[7]
            course = parts[8]

            if status == 'A' and raw_lat and raw_lon:
                self.latitude = self.parse_nmea_lat_lon(raw_lat, lat_dir)
                self.longitude = self.parse_nmea_lat_lon(raw_lon, lon_dir)
                self.speed_kmh = float(speed_knots) * 1.852 if speed_knots else 0.0
                self.course_deg = float(course) if course else 0.0
                updated = True

        # 3. HDT / GPHDT: Heading (True)
        elif sentence_type.endswith('HDT') and len(parts) >= 2:
            hdt_val = parts[1]
            if hdt_val:
                try:
                    self.heading_deg = float(hdt_val)
                except ValueError:
                    pass

        # 4. KSXT: Unicore Proprietary integrated dual antenna positioning + orientation sentence
        # Format often resembles: $KSXT,yyyymmddhhmmss.ss,heading,pitch,roll,lat,lon,alt,ve,vn,vu,hdg_status,pos_status,sats...
        elif sentence_type == '$KSXT' and len(parts) >= 15:
            try:
                # Typically index 2 is heading, index 5 is lat, index 6 is lon, index 7 is altitude
                heading_str = parts[2]
                lat_str = parts[5]
                lon_str = parts[6]
                alt_str = parts[7]
                fix_stat = parts[12] # position status
                sat_num = parts[14]

                if lat_str and lon_str:
                    self.latitude = float(lat_str)
                    self.longitude = float(lon_str)
                    self.altitude = float(alt_str) if alt_str else 0.0
                    self.heading_deg = float(heading_str) if heading_str else float('nan')
                    self.num_satellites = int(sat_num) if sat_num else 0
                    
                    # Convert UM960 internal KSXT status code to standard GGA quality equivalent for logging
                    # 4=RTK Fixed, 5=RTK Float, etc.
                    try:
                        status_val = int(fix_stat)
                        if status_val == 4 or status_val == 62: # Typical RTK Fixed indicators
                            self.fix_quality = 4
                        elif status_val == 5 or status_val == 61: # Typical RTK Float indicators
                            self.fix_quality = 5
                        else:
                            self.fix_quality = 1
                    except Exception:
                        self.fix_quality = 1
                        
                    updated = True
            except Exception:
                pass

        if updated:
            # Perform projection coordinate transfers instantly
            self.bgs_east, self.bgs_north, self.bgs_lam_east, self.bgs_lam_north, self.gr_east, self.gr_north = convert_coordinates(
                self.latitude, self.longitude
            )

        return updated


# -------------------------------------------------------------------------
# HARDWARE / EMULATION STREAM GENERATORS
# -------------------------------------------------------------------------
class NMEAStreamer:
    """
    Manages the reading of NMEA data either from a real hardware serial port
    or from a high-quality simulated trajectory crossing Western Bulgaria & Greece.
    """
    def __init__(self, port, baud, simulate=False, rover_mode="SURVEY", check_mode=True, save_config=False,
                 nmea_messages=("GPGGA", "GPRMC"), nmea_interval=1.0):
        self.port = port
        self.baud = baud
        self.simulate = simulate
        self.rover_mode = rover_mode      # ROVER sub-type: SURVEY, UAV, AUTOMOTIVE or "" for plain ROVER
        self.check_mode = check_mode      # Query MODE on connect and switch to ROVER if needed
        self.save_config = save_config    # Persist configuration changes with SAVECONFIG
        self.nmea_messages = [m.upper() for m in nmea_messages]  # NMEA logs to enable on connect (empty = skip)
        self.nmea_interval = nmea_interval  # Output interval in seconds (1 = 1Hz, 0.2 = 5Hz)
        self.running = False
        self.decoder = UM960Decoder()
        self.queue = deque(maxlen=1000) # stores historical path: tuples of (lat, lon, bgs_e, bgs_n, gr_e, gr_n, quality)
        self.thread = None

        # Simulation path variables
        self.sim_tick = 0
        self.sim_route = "bulgaria_to_greece"

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def _handle_line(self, line_str):
        """Feeds one received line to the decoder and records the trajectory point if position updated."""
        if self.decoder.parse_line(line_str):
            # Store trajectory tuple: (lat, lon, bgs_e, bgs_n, bgs_lam_e, bgs_lam_n, gr_e, gr_n, fix_quality)
            self.queue.append((
                self.decoder.latitude, self.decoder.longitude,
                self.decoder.bgs_east, self.decoder.bgs_north,
                self.decoder.bgs_lam_east, self.decoder.bgs_lam_north,
                self.decoder.gr_east, self.decoder.gr_north,
                self.decoder.fix_quality
            ))

    def configure_receiver(self, serial_conn):
        """Checks ROVER mode and NMEA output on the receiver, see UM960Configurator."""
        UM960Configurator(
            serial_conn, on_line=self._handle_line, rover_mode=self.rover_mode, check_mode=self.check_mode,
            save_config=self.save_config, nmea_messages=self.nmea_messages, nmea_interval=self.nmea_interval
        ).configure()

    def _generate_simulated_ksxt(self, lat, lon, heading, alt, q, num_sats):
        """Generates a perfect Unicore proprietary $KSXT sentence"""
        time_str = time.strftime("%Y%m%d%H%M%S.00", time.gmtime())
        pitch = 1.2 * math.sin(self.sim_tick / 10.0)
        roll = 0.8 * math.cos(self.sim_tick / 8.0)
        # Position status: 4 for RTK fixed, 5 for RTK float, 1 for single
        pos_status = 4 if q == 4 else (5 if q == 5 else 1)
        # Calculate checksum
        payload = f"KSXT,{time_str},{heading:.2f},{pitch:.2f},{roll:.2f},{lat:.8f},{lon:.8f},{alt:.2f},0.05,0.08,0.12,3,{pos_status},{num_sats},{num_sats},1,0,0,0,10"
        
        calculated_xor = 0
        for char in payload:
            calculated_xor ^= ord(char)
        
        return f"${payload}*{calculated_xor:02X}"

    def _generate_simulated_nmea_lines(self):
        """
        Synthesizes standard and proprietary sentences traversing Western Bulgaria and Greece.
        Route starts around Pernik/Kyustendil (Western Bulgaria) and heads South to Thessaloniki (Greece).
        """
        # Linear trajectory coordinates from Sofia (42.69, 23.32) through Promachonas (BG/GR border - 41.37, 23.36) to Thessaloniki (40.64, 22.94)
        total_steps = 300
        step = self.sim_tick % total_steps
        
        # Traverse from Sofia to Thessaloniki
        t = step / float(total_steps)
        # Starting point (Sofia, BG)
        start_lat, start_lon = 42.6976, 23.3219
        # Ending point (Thessaloniki, GR)
        end_lat, end_lon = 40.6401, 22.9444
        
        lat = start_lat + (end_lat - start_lat) * t
        lon = start_lon + (end_lon - start_lon) * t
        
        # Heading calculation
        heading = (math.degrees(math.atan2(end_lon - start_lon, end_lat - start_lat)) + 360) % 360
        # Add slight wobble for realism
        heading += 5.0 * math.sin(step / 3.0)
        
        alt = 550.0 - 500.0 * t + 15.0 * math.cos(step / 5.0)
        num_sats = int(24 + 4 * math.sin(step / 10.0))
        
        # Simulated RTK state transitions (intermittent tree canopies = float, open skies = fixed)
        if 80 < step < 110 or 200 < step < 230:
            q = 5 # RTK Float
        elif 140 < step < 160:
            q = 1 # Single GPS (no RTK)
        else:
            q = 4 # RTK Fixed (Excellent!)

        self.sim_tick += 1

        # Format longitude and latitude into DDMM.MMMM for standard GGA/RMC
        def to_nmea_deg(val, is_lat):
            sign = '-' if val < 0 else ''
            abs_val = abs(val)
            deg = int(abs_val)
            minutes = (abs_val - deg) * 60.0
            dir_char = ('S' if is_lat else 'W') if sign else ('N' if is_lat else 'E')
            deg_str = f"{deg:02d}" if is_lat else f"{deg:03d}"
            return f"{deg_str}{minutes:08.5f}", dir_char

        nmea_lat, lat_dir = to_nmea_deg(lat, is_lat=True)
        nmea_lon, lon_dir = to_nmea_deg(lon, is_lat=False)

        time_nmea = time.strftime("%H%M%S.00", time.gmtime())
        date_nmea = time.strftime("%d%m%y", time.gmtime())
        speed_knots = 45.3 / 1.852 + 2.0 * math.sin(step)

        # Build NMEA messages
        # GGA
        gga_payload = f"GNGGA,{time_nmea},{nmea_lat},{lat_dir},{nmea_lon},{lon_dir},{q},{num_sats},0.9,{alt:.1f},M,35.2,M,,"
        calculated_gga_xor = 0
        for char in gga_payload:
            calculated_gga_xor ^= ord(char)
        gga_str = f"${gga_payload}*{calculated_gga_gga_xor:02X}" if 'calculated_gga_gga_xor' in locals() else f"${gga_payload}*{calculated_gga_xor:02X}"

        # RMC
        rmc_payload = f"GNRMC,{time_nmea},A,{nmea_lat},{lat_dir},{nmea_lon},{lon_dir},{speed_knots:.2f},{heading:.1f},{date_nmea},,,"
        calculated_rmc_xor = 0
        for char in rmc_payload:
            calculated_rmc_xor ^= ord(char)
        rmc_str = f"${rmc_payload}*{calculated_rmc_xor:02X}"

        # HDT
        hdt_payload = f"GNHDT,{heading:.2f},T"
        calculated_hdt_xor = 0
        for char in hdt_payload:
            calculated_hdt_xor ^= ord(char)
        hdt_str = f"${hdt_payload}*{calculated_hdt_xor:02X}"

        # KSXT
        ksxt_str = self._generate_simulated_ksxt(lat, lon, heading, alt, q, num_sats)

        return [gga_str, rmc_str, hdt_str, ksxt_str]

    def _run_loop(self):
        serial_conn = None
        if not self.simulate:
            if serial is None:
                print("Error: Serial driver not installed. Dropping into simulation mode.")
                self.simulate = True
            else:
                try:
                    serial_conn = serial.Serial(
                        port=self.port,
                        baudrate=self.baud,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=1.0
                    )
                    print(f"Connected successfully to UM960 serial port: {self.port} at {self.baud} baud.")
                except Exception as e:
                    print(f"Error opening serial port {self.port}: {e}. Dropping into simulation mode.")
                    self.simulate = True

                if serial_conn:
                    try:
                        self.configure_receiver(serial_conn)
                    except Exception as e:
                        print(f"Warning: UM960 configuration failed: {e}")

        while self.running:
            if self.simulate:
                lines = self._generate_simulated_nmea_lines()
                for raw_line in lines:
                    self._handle_line(raw_line)
                    time.sleep(0.05) # simulate output spacing
                time.sleep(1.0) # 1Hz positioning interval
            else:
                try:
                    if serial_conn and serial_conn.in_waiting:
                        line_bytes = serial_conn.readline()
                        line_str = line_bytes.decode('ascii', errors='ignore')
                        self._handle_line(line_str)
                except Exception as e:
                    print(f"Serial read error: {e}")
                    time.sleep(1)


# -------------------------------------------------------------------------
# REAL-TIME PLOTTING & DASHBOARD
# -------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Unicore UM960 GNSS GIS & Coordinate System Tracker")
    parser.add_argument('--port', type=str, default='/dev/ttyUSB0', help="Serial port identifier")
    parser.add_argument('--baud', type=int, default=115200, help="Serial baud rate for UM960")
    parser.add_argument('--simulate', action='store_true', default=False, help="Simulate data path without real receiver")
    parser.add_argument('--rover-mode', type=str.upper, default='SURVEY', choices=['SURVEY', 'UAV', 'AUTOMOTIVE', 'DEFAULT'],
                        help="ROVER sub-mode applied if the receiver is not already a rover (DEFAULT = plain 'MODE ROVER')")
    parser.add_argument('--skip-mode-check', action='store_true', default=False,
                        help="Do not query/reconfigure the receiver mode on connect")
    parser.add_argument('--nmea-messages', type=str, default='GPGGA,GPRMC',
                        help="Comma-separated NMEA logs to enable on the connected port ('' to skip)")
    parser.add_argument('--nmea-interval', type=float, default=1.0,
                        help="NMEA output interval in seconds (1 = 1Hz, 0.2 = 5Hz)")
    parser.add_argument('--save-config', action='store_true', default=False,
                        help="Persist configuration changes to receiver NVM with SAVECONFIG")
    args = parser.parse_args()

    # If serial library is missing, force simulation
    is_simulation = args.simulate or (serial is None)
    if is_simulation:
        print("\n--- RUNNING IN SIMULATION DEMO MODE ---")
        print("Simulating a GNSS platform driving from Western Bulgaria (Sofia) down into Northern Greece.")
        print("Calculating exact coordinates in BGS2005 UTM 34N, BGS2005 Lambert, and HGRS87 in real-time.\n")

    streamer = NMEAStreamer(port=args.port, baud=args.baud, simulate=is_simulation,
                            rover_mode='' if args.rover_mode == 'DEFAULT' else args.rover_mode,
                            check_mode=not args.skip_mode_check, save_config=args.save_config,
                            nmea_messages=[m.strip() for m in args.nmea_messages.split(',') if m.strip()],
                            nmea_interval=args.nmea_interval)
    streamer.start()

    # Set up real-time matplotlib plots with two subplots:
    # Left: BGS2005 grid trajectory, Right: Greek Grid trajectory
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7))
    fig.suptitle("Unicore UM960 Geographic Coordinate Convergence Dashboard", fontsize=14, fontweight='bold')

    # Styles
    for ax in (ax1, ax2):
        ax.grid(True, linestyle='--', alpha=0.5)
        ax.set_aspect('equal')

    ax1.set_title("БГС2005 / UTM zone 34N (EPSG:7803) - Western Bulgaria", color='#0f766e', fontweight='semibold')
    ax1.set_xlabel("Easting (X) [meters]")
    ax1.set_ylabel("Northing (Y) [meters]")

    ax2.set_title("HGRS87 / Greek Grid (EPSG:2100) - Northern Greece", color='#1d4ed8', fontweight='semibold')
    ax2.set_xlabel("Easting (X) [meters]")
    ax2.set_ylabel("Northing (Y) [meters]")

    # Trajectory lines and current location points
    bgs_line, = ax1.plot([], [], 'o-', color='#0f766e', label="Track", markersize=3, markevery=5)
    bgs_curr, = ax1.plot([], [], 'X', color='#dc2626', markersize=10, label="Vehicle")
    
    greek_line, = ax2.plot([], [], 's-', color='#1d4ed8', label="Track", markersize=3, markevery=5)
    greek_curr, = ax2.plot([], [], 'X', color='#dc2626', markersize=10, label="Vehicle")

    ax1.legend()
    ax2.legend()

    # Annotation text blocks
    text_info = fig.text(0.15, 0.02, "", fontsize=10, fontfamily='monospace', 
                         bbox=dict(facecolor='white', alpha=0.9, edgecolor='#ccc', boxstyle='round,pad=0.5'))

    def update_plot(frame):
        if not streamer.queue:
            return bgs_line, bgs_curr, greek_line, greek_curr

        # Unpack the current queue components
        history = list(streamer.queue)
        
        # Transposed lists
        lats = [h[0] for h in history]
        lons = [h[1] for h in history]
        bgs_es = [h[2] for h in history if not math.isnan(h[2])]
        bgs_ns = [h[3] for h in history if not math.isnan(h[3])]
        bgs_l_es = [h[4] for h in history if not math.isnan(h[4])]
        bgs_l_ns = [h[5] for h in history if not math.isnan(h[5])]
        gr_es = [h[6] for h in history if not math.isnan(h[6])]
        gr_ns = [h[7] for h in history if not math.isnan(h[7])]
        qualities = [h[8] for h in history]

        # Get latest parsed object
        decoder = streamer.decoder

        # Update Plot 1 (BGS2005 UTM 34)
        if bgs_es and bgs_ns:
            bgs_line.set_data(bgs_es, bgs_ns)
            bgs_curr.set_data([bgs_es[-1]], [bgs_ns[-1]])
            
            # Maintain dynamic zoom centering round current point
            margin = 3000  # meters padding
            ax1.set_xlim(min(bgs_es) - margin, max(bgs_es) + margin)
            ax1.set_ylim(min(bgs_ns) - margin, max(bgs_ns) + margin)

        # Update Plot 2 (Greek Grid)
        if gr_es and gr_ns:
            greek_line.set_data(gr_es, gr_ns)
            greek_curr.set_data([gr_es[-1]], [gr_ns[-1]])
            
            margin = 3000  # meters padding
            ax2.set_xlim(min(gr_es) - margin, max(gr_es) + margin)
            ax2.set_ylim(min(gr_ns) - margin, max(gr_ns) + margin)

        # Update quality text label
        quality_map = {0: "NO FIX", 1: "GNSS Single", 2: "DGPS", 4: "RTK FIXED (Precision)", 5: "RTK FLOAT"}
        quality_text = quality_map.get(decoder.fix_quality, f"CODE {decoder.fix_quality}")

        summary = (
            f"--- Unicore UM960 Realtime Live Stats ---\n"
            f"Time/UTC: {decoder.timestamp} | Constellation Satellites: {decoder.num_satellites} (HDOP: {decoder.hdop:.1f})\n"
            f"WGS84 Lat/Lon: {decoder.latitude:.8f}°N, {decoder.longitude:.8f}°E | Ellipsoidal Height: {decoder.altitude:.2f} m\n"
            f"Heading: {decoder.heading_deg:.1f}° | Speed: {decoder.speed_kmh:.1f} km/h | Fix Status: {quality_text}\n"
            f"\n"
            f"GRID CONVERSIONS:\n"
            f"-> BGS2005 / UTM 34N:  Easting: {decoder.bgs_east:11.3f} m | Northing: {decoder.bgs_north:12.3f} m\n"
            f"-> BGS2005 / Lambert:  Easting: {decoder.bgs_lam_east:11.3f} m | Northing: {decoder.bgs_lam_north:12.3f} m\n"
            f"-> Greek Grid (Cadastre): Easting: {decoder.gr_east:11.3f} m | Northing: {decoder.gr_north:12.3f} m"
        )
        text_info.set_text(summary)

        return bgs_line, bgs_curr, greek_line, greek_curr

    # Matplotlib animated loop running at 10Hz to parse incoming blocks immediately
    anim = FuncAnimation(fig, update_plot, interval=100, blit=False, cache_frame_data=False)
    
    try:
        plt.tight_layout(rect=[0, 0.05, 1, 0.95])
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping receiver reader threads...")
        streamer.stop()
        print("Done. Tracker terminated.")


if __name__ == "__main__":
    main()
