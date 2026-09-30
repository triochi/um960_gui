/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import { useState, useEffect, useRef } from 'react';
import { 
  Play, 
  Pause, 
  RotateCcw, 
  Cpu, 
  Download, 
  Copy, 
  Radio, 
  Settings, 
  Wifi, 
  FileText, 
  Compass, 
  MapPin, 
  Table, 
  Layers, 
  Check, 
  AlertCircle, 
  HelpCircle,
  ExternalLink,
  ZoomIn,
  ZoomOut,
  Crosshair
} from 'lucide-react';
import { NMEAParser } from './nmeaParser';
import { GNSSData, SatelliteInfo, TrackPoint, SerialConfig } from './types';
import { PYTHON_SCRIPT_TEXT } from './pythonScriptText';

// Typical simulated coordinates for Western Bulgaria & Northern Greece
const ROUTES = {
  interstate: {
    name: "Sofia to Thessaloniki Highway (Cross-Border)",
    description: "Diagonal highway run crossing Western Bulgaria into Aegean Macedonia, Greece.",
    points: [
      { lat: 42.6976, lon: 23.3219, alt: 550, name: "Sofia, BG", q: 4 },
      { lat: 42.6022, lon: 23.0305, alt: 720, name: "Pernik, BG", q: 4 },
      { lat: 42.3858, lon: 22.8881, alt: 510, name: "Kyustendil Jct, BG", q: 4 },
      { lat: 42.0206, lon: 23.0945, alt: 360, name: "Blagoevgrad, BG", q: 5 }, // tree canopy RTK Float
      { lat: 41.7645, lon: 23.1818, alt: 220, name: "Kresna Gorge, BG", q: 1 }, // deep gorge No RTK
      { lat: 41.5645, lon: 23.2818, alt: 140, name: "Sandanski, BG", q: 4 },
      { lat: 41.3789, lon: 23.3644, alt: 95,  name: "Kulata Checkpoint (BG)", q: 4 },
      { lat: 41.3758, lon: 23.3670, alt: 92,  name: "Promachonas Port (GR)", q: 4 },
      { lat: 41.2215, lon: 23.4110, alt: 85,  name: "Sidirokastro, GR", q: 4 },
      { lat: 41.0913, lon: 23.5484, alt: 70,  name: "Serres Jct, GR", q: 5 },
      { lat: 40.8541, lon: 23.1512, alt: 110, name: "Lachanas, GR", q: 4 },
      { lat: 40.6401, lon: 22.9444, alt: 10,  name: "Thessaloniki, GR", q: 4 }
    ]
  },
  bulgaria_cadastre: {
    name: "Western Bulgaria Farmland Survey",
    description: "High-precision RTK surveying grid patterns in rural Pernik/Radomir, BG.",
    points: [
      { lat: 42.5022, lon: 23.0105, alt: 750.42, name: "BM-01 Origin", q: 4 },
      { lat: 42.5045, lon: 23.0108, alt: 750.51, name: "Grid A-1", q: 4 },
      { lat: 42.5042, lon: 23.0152, alt: 751.10, name: "Grid A-2", q: 4 },
      { lat: 42.5019, lon: 23.0148, alt: 749.88, name: "Grid A-3", q: 4 },
      { lat: 42.5021, lon: 23.0106, alt: 750.41, name: "Grid Closed 1", q: 4 },
      { lat: 42.5061, lon: 23.0200, alt: 752.33, name: "Canopy Checkpoint", q: 5 }, // float near oak hedge
      { lat: 42.5085, lon: 23.0242, alt: 754.12, name: "Hillslope Crest", q: 4 },
      { lat: 42.5060, lon: 23.0280, alt: 753.05, name: "Grid B-1", q: 4 }
    ]
  },
  greece_cadastre: {
    name: "Northern Greece Urban Cadastral",
    description: "Densely collected city boundary points near Drama and Serres, Greece.",
    points: [
      { lat: 41.1492, lon: 24.1432, alt: 115.2, name: "S-501 Cadastre Drama", q: 4 },
      { lat: 41.1480, lon: 24.1450, alt: 114.8, name: "Drama City Park Corner", q: 4 },
      { lat: 41.1455, lon: 24.1412, alt: 112.1, name: "Intersection Mon-3", q: 5 },
      { lat: 41.1430, lon: 24.1390, alt: 110.5, name: "Drama Railway Station", q: 4 },
      { lat: 41.1441, lon: 24.1365, alt: 111.4, name: "Residential Parcel 492", q: 4 },
      { lat: 41.1472, lon: 24.1388, alt: 113.8, name: "Commercial Hub Mon-8", q: 4 }
    ]
  }
};

// Web Serial defaults to a 255 byte read buffer, which overruns easily
const SERIAL_BUFFER_SIZE = 16384;
const SERIAL_MAX_REOPEN_ATTEMPTS = 5;

export default function App() {
  const [activeTab, setActiveTab] = useState<'visualizer' | 'script' | 'manual'>('visualizer');
  
  // GNSS Parser & Decoder state
  const parserRef = useRef<NMEAParser>(new NMEAParser());
  const [gnssData, setGnssData] = useState<GNSSData>(parserRef.current.getInitialState());
  const [satellites, setSatellites] = useState<SatelliteInfo[]>([]);
  const [trackHistory, setTrackHistory] = useState<TrackPoint[]>([]);
  
  // Custom Serial configuration
  const [serialConfig, setSerialConfig] = useState<Omit<SerialConfig, 'flowControl'>>({
    baudRate: 115200,
    dataBits: 8,
    stopBits: 1,
    parity: 'none'
  });
  const [isPortConnected, setIsPortConnected] = useState(false);
  const [serialError, setSerialError] = useState<string | null>(null);
  const portReaderRef = useRef<any>(null);
  const portRef = useRef<any>(null);
  const keepReadingRef = useRef<boolean>(true);
  const readLoopRef = useRef<Promise<void> | null>(null);

  // Simulation controls
  const [selectedRouteKey, setSelectedRouteKey] = useState<keyof typeof ROUTES>('interstate');
  const [isPlaying, setIsPlaying] = useState(true);
  const [simSpeed, setSimSpeed] = useState<number>(1); // Interval seconds
  const [simStep, setSimStep] = useState<number>(0);
  const [totalTicks, setTotalTicks] = useState(0);

  // Manual NMEA paste state
  const [pastedNmea, setPastedNmea] = useState<string>("");
  const [manualParseStatus, setManualParseStatus] = useState<string | null>(null);

  // Output logs terminal
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const terminalContainerRef = useRef<HTMLDivElement>(null);

  // Custom Python Exporter configs
  const [pythonComPort, setPythonComPort] = useState<string>("/dev/ttyUSB0");
  const [pythonBaud, setPythonBaud] = useState<number>(115200);
  const [isCopied, setIsCopied] = useState(false);

  // Map Display Settings
  const [mapZoom, setMapZoom] = useState<number>(5.5);
  const [mapCenter, setMapCenter] = useState<[number, number]>([41.5, 23.3]); // Balkans
  const [mapMode, setMapMode] = useState<'hybrid_grid' | 'openstreetmap'>('hybrid_grid');

  // OSM Live map settings to prevent constant zoom/pan resets
  const [osmZoom, setOsmZoom] = useState<number>(14);
  const [mapFollow, setMapFollow] = useState<boolean>(true);
  const [mapCenterLat, setMapCenterLat] = useState<number>(42.1);
  const [mapCenterLon, setMapCenterLon] = useState<number>(23.1);
  const lastUpdateRef = useRef<number>(0);

  // Synchronize map center elements when receiving GNSS updates
  useEffect(() => {
    if (!gnssData.latitude || !gnssData.longitude) return;
    
    const now = Date.now();
    const latDiff = Math.abs(gnssData.latitude - mapCenterLat);
    const lonDiff = Math.abs(gnssData.longitude - mapCenterLon);
    
    const isFirstUpdate = mapCenterLat === 42.1 && mapCenterLon === 23.1;
    const hasMovedSignificantly = latDiff > 0.0001 || lonDiff > 0.0001;
    
    if (mapFollow) {
      if (isFirstUpdate || (hasMovedSignificantly && now - lastUpdateRef.current > 3000)) {
        setMapCenterLat(gnssData.latitude);
        setMapCenterLon(gnssData.longitude);
        lastUpdateRef.current = now;
      }
    }
  }, [gnssData.latitude, gnssData.longitude, mapFollow]);

  // Center map on receiver manually
  const centerMapOnReceiver = () => {
    if (gnssData.latitude && gnssData.longitude) {
      setMapCenterLat(gnssData.latitude);
      setMapCenterLon(gnssData.longitude);
      lastUpdateRef.current = Date.now();
      logToConsole(`Centered map on receiver coordinates: ${gnssData.latitude.toFixed(5)}, ${gnssData.longitude.toFixed(5)}`);
    } else {
      logToConsole("No active GNSS fix coordinates available to center map.");
    }
  };

  // Trigger auto logs scrolling inside the custom container without moving the browser viewport
  useEffect(() => {
    if (terminalContainerRef.current) {
      terminalContainerRef.current.scrollTop = terminalContainerRef.current.scrollHeight;
    }
  }, [consoleLogs]);

  // Append a message to the logging HUD
  const logToConsole = (msg: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setConsoleLogs(prev => [...prev.slice(-99), `[${timestamp}] ${msg}`]);
  };

  // Generate Simulated NMEA sentences based on lat, lon, heading, speed
  const generateSimulatedNmea = (lat: number, lon: number, alt: number, q: number, step: number) => {
    const time_nmea = new Date().toISOString().replace(/[-:T]/g, "").substring(8, 14) + ".00";
    const date_nmea = new Date().toLocaleDateString('en-GB').replace(/\//g, ""); // DDMMYY
    
    // Create slight variance
    const hdop = (0.7 + 0.3 * Math.sin(step / 3.0)).toFixed(1);
    const vdop = (1.0 + 0.4 * Math.sin(step / 3.0)).toFixed(1);
    const pdop = (1.2 + 0.5 * Math.sin(step / 3.0)).toFixed(1);
    const numSats = Math.floor(22 + 6 * Math.sin(step / 8.0));
    
    const heading = (202.5 + 4.0 * Math.sin(step / 2.0)) % 360;
    const speedKnots = (48.4 + 3.0 * Math.cos(step / 4.0));
    
    // Convert coordinate format to DDMM.MMMM for standard sentences
    const toNmeaCoords = (val: number, isLat: boolean) => {
      const absVal = Math.abs(val);
      const degree = Math.floor(absVal);
      const minutes = (absVal - degree) * 60;
      const minutesStr = minutes.toFixed(5).padStart(8, '0');
      const degStr = isLat ? degree.toString().padStart(2, '0') : degree.toString().padStart(3, '0');
      const dir = isLat ? (val >= 0 ? 'N' : 'S') : (val >= 0 ? 'E' : 'W');
      return { str: `${degStr}${minutesStr}`, dir };
    };

    const nLat = toNmeaCoords(lat, true);
    const nLon = toNmeaCoords(lon, false);

    // 1. GGA Sentence
    const ggaBody = `GNGGA,${time_nmea},${nLat.str},${nLat.dir},${nLon.str},${nLon.dir},${q},${numSats},${hdop},${alt.toFixed(2)},M,35.2,M,,`;
    let ggaSum = 0;
    for (let i = 0; i < ggaBody.length; i++) ggaSum ^= ggaBody.charCodeAt(i);
    const gga = `$${ggaBody}*${ggaSum.toString(16).toUpperCase().padStart(2, '0')}`;

    // 2. RMC Sentence
    const rmcBody = `GNRMC,${time_nmea},A,${nLat.str},${nLat.dir},${nLon.str},${nLon.dir},${speedKnots.toFixed(2)},${heading.toFixed(1)},${date_nmea},,,`;
    let rmcSum = 0;
    for (let i = 0; i < rmcBody.length; i++) rmcSum ^= rmcBody.charCodeAt(i);
    const rmc = `$${rmcBody}*${rmcSum.toString(16).toUpperCase().padStart(2, '0')}`;

    // 3. HDT True Heading sentence
    const hdtBody = `GNHDT,${heading.toFixed(2)},T`;
    let hdtSum = 0;
    for (let i = 0; i < hdtBody.length; i++) hdtSum ^= hdtBody.charCodeAt(i);
    const hdt = `$${hdtBody}*${hdtSum.toString(16).toUpperCase().padStart(2, '0')}`;

    // 4. GSA Satellite Dilution parameters
    const gsaBody = `GNGSA,A,3,01,03,04,07,08,10,14,16,21,22,27,,,${pdop},${hdop},${vdop}`;
    let gsaSum = 0;
    for (let i = 0; i < gsaBody.length; i++) gsaSum ^= gsaBody.charCodeAt(i);
    const gsa = `$${gsaBody}*${gsaSum.toString(16).toUpperCase().padStart(2, '0')}`;

    // 5. Unicore proprietary integrated navigation message $KSXT
    const pitch = (1.5 * Math.sin(step / 5.0)).toFixed(2);
    const roll = (0.75 * Math.cos(step / 4.0)).toFixed(2);
    const ksxtStatus = q === 4 ? 4 : (q === 5 ? 5 : 1);
    const ksxtBody = `KSXT,20260623${time_nmea.substring(0,6)}.00,${heading.toFixed(2)},${pitch},${roll},${lat.toFixed(8)},${lon.toFixed(8)},${alt.toFixed(2)},0.04,0.06,0.11,3,${ksxtStatus},${numSats},${numSats},1,0,0,0,10`;
    let ksxtSum = 0;
    for (let i = 0; i < ksxtBody.length; i++) ksxtSum ^= ksxtBody.charCodeAt(i);
    const ksxt = `$${ksxtBody}*${ksxtSum.toString(16).toUpperCase().padStart(2, '0')}`;

    // 6. GSV Satellite details generator (GPGSV, BDGSV, etc.)
    // Output standard high satellites signals
    const gsv1 = `$GPGSV,3,1,10,01,65,120,44,03,50,045,41,04,40,280,48,07,35,160,39*7A`;
    const gsv2 = `$GPGSV,3,2,10,08,25,090,38,10,18,310,35,14,15,220,40,16,12,045,32*72`;
    const gsv3 = `$GBGSV,2,1,08,140,60,180,49,141,55,270,45,142,42,030,42,143,30,120,38*43`;

    return [gga, rmc, hdt, gsa, ksxt, gsv1, gsv2, gsv3];
  };

  // Run the Simulation clock
  useEffect(() => {
    if (!isPlaying || isPortConnected) return;

    const interval = setInterval(() => {
      const route = ROUTES[selectedRouteKey];
      const maxIndex = route.points.length - 2;
      
      // Calculate current interpolated step
      const currentTick = simStep % 100; // Interpolate 100 mini-steps between nodes
      const nodeIndex = Math.floor(simStep / 100) % route.points.length;
      const nextNodeIndex = (nodeIndex + 1) % route.points.length;
      
      const p1 = route.points[nodeIndex];
      const p2 = route.points[nextNodeIndex];
      
      const t = currentTick / 100;
      const lat = p1.lat + (p2.lat - p1.lat) * t;
      const lon = p1.lon + (p2.lon - p1.lon) * t;
      const alt = p1.alt + (p2.alt - p1.alt) * t;
      const q = p1.q;

      // Produce logs
      const sentences = generateSimulatedNmea(lat, lon, alt, q, simStep);
      
      // Parse them
      let updatedData = gnssData;
      sentences.forEach(sentence => {
        const { data, updated } = parserRef.current.parseSentence(sentence);
        if (updated) {
          updatedData = data;
        }
      });
      
      setGnssData({ ...updatedData });
      setSatellites(parserRef.current.getSatellitesList());

      // Live tracks
      if (currentTick % 20 === 0) { // store every 20 mini ticks
        setTrackHistory(prev => {
          const newPt: TrackPoint = {
            latitude: updatedData.latitude,
            longitude: updatedData.longitude,
            altitude: updatedData.altitude,
            bgsEastUTM34: updatedData.bgsEastUTM34,
            bgsNorthUTM34: updatedData.bgsNorthUTM34,
            bgsEastLambert: updatedData.bgsEastLambert,
            bgsNorthLambert: updatedData.bgsNorthLambert,
            greekEasting: updatedData.greekEasting,
            greekNorthing: updatedData.greekNorthing,
            fixQuality: updatedData.fixQuality,
            timestamp: updatedData.timestamp,
            heading: updatedData.headingDeg || updatedData.courseDeg
          };
          // limit historical trail size
          const hist = [...prev, newPt];
          if (hist.length > 200) hist.shift();
          return hist;
        });
      }

      // Output logs to bottom console
      sentences.slice(0, 5).forEach(line => logToConsole(line));
      
      setSimStep(prev => prev + 1);
      setTotalTicks(prev => prev + 1);

    }, simSpeed * 1000);

    return () => clearInterval(interval);
  }, [isPlaying, selectedRouteKey, simStep, simSpeed, isPortConnected, gnssData]);

  // Handle Serial Ports via Web Serial API
  const handleConnectSerial = async () => {
    if (isPortConnected) {
      // Disconnect: stop the read loop, which closes the port on exit
      keepReadingRef.current = false;
      if (portReaderRef.current) {
        try {
          await portReaderRef.current.cancel();
        } catch (e) {}
      }
      await readLoopRef.current;
      logToConsole("Serial Port disconnected by user.");
      return;
    }

    setSerialError(null);
    if (!("serial" in navigator)) {
      setSerialError("Web Serial API is not supported in this browser. Please open the app in a new tab or use Chrome/Edge.");
      logToConsole("Error: Web Serial API not supported.");
      return;
    }

    try {
      logToConsole("Requesting hardware serial port access...");
      const port = await (navigator as any).serial.requestPort();
      // A previous session that ended abnormally may have left the port open
      if (port.readable || port.writable) {
        try {
          await port.close();
        } catch (e) {}
      }
      await port.open({ baudRate: serialConfig.baudRate, bufferSize: SERIAL_BUFFER_SIZE });
      
      portRef.current = port;
      setIsPortConnected(true);
      keepReadingRef.current = true;
      setIsPlaying(false); // Stop simulation

      logToConsole(`Opened COM connection successfully at ${serialConfig.baudRate} Baud.`);
      
      readLoopRef.current = readSerialLoop(port);
    } catch (err: any) {
      console.error(err);
      setSerialError(err.message || "Failed to open serial port.");
      logToConsole(`COM Exception: ${err.message || 'Access Denied'}`);
    }
  };

  const handleSerialLine = (line: string) => {
    if (line.trim().startsWith('$')) {
      const { data, updated } = parserRef.current.parseSentence(line);
      if (updated) {
         setGnssData({ ...data });
         setSatellites(parserRef.current.getSatellitesList());
         
         // Append tracks
         setTrackHistory(prev => {
           const newPt: TrackPoint = {
             latitude: data.latitude,
             longitude: data.longitude,
             altitude: data.altitude,
             bgsEastUTM34: data.bgsEastUTM34,
             bgsNorthUTM34: data.bgsNorthUTM34,
             bgsEastLambert: data.bgsEastLambert,
             bgsNorthLambert: data.bgsNorthLambert,
             greekEasting: data.greekEasting,
             greekNorthing: data.greekNorthing,
             fixQuality: data.fixQuality,
             timestamp: data.timestamp,
             heading: data.headingDeg || data.courseDeg
           };
           const hist = [...prev, newPt];
           if (hist.length > 200) hist.shift();
           return hist;
         });
      }
      logToConsole(line);
    }
  };

  // Reads the port until the user disconnects. Non-fatal errors (buffer overrun, framing,
  // parity, break) leave a fresh port.readable to continue with; fatal ones ("device lost")
  // null it, in which case the port is reopened up to SERIAL_MAX_REOPEN_ATTEMPTS times.
  // The port is always closed on exit so that it can be opened again.
  const readSerialLoop = async (port: any) => {
    const textDecoder = new TextDecoder();
    let buffer = "";
    let reopenAttempts = 0;

    try {
      while (keepReadingRef.current) {
        if (!port.readable) {
          if (reopenAttempts >= SERIAL_MAX_REOPEN_ATTEMPTS) {
            logToConsole("Serial port could not be reopened. Check the USB cable/adapter and connect again.");
            setSerialError("Serial port lost.");
            break;
          }
          reopenAttempts++;
          logToConsole(`Serial port lost, reopening (attempt ${reopenAttempts}/${SERIAL_MAX_REOPEN_ATTEMPTS})...`);
          try {
            await port.close();
          } catch (e) {}
          await new Promise(resolve => setTimeout(resolve, 1000));
          if (!keepReadingRef.current) break;
          try {
            await port.open({ baudRate: serialConfig.baudRate, bufferSize: SERIAL_BUFFER_SIZE });
            buffer = "";
            logToConsole("Serial port reopened.");
          } catch (err: any) {
            logToConsole(`Reopen failed: ${err.message}`);
            continue;
          }
        }

        const reader = port.readable.getReader();
        portReaderRef.current = reader;
        let lost = false;
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) {
              // Cancelled by the user, otherwise the stream closed underneath us
              lost = keepReadingRef.current;
              break;
            }
            reopenAttempts = 0;

            buffer += textDecoder.decode(value, { stream: true });
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop() || ""; // Keep incomplete line
            for (const line of lines) {
              handleSerialLine(line);
            }
          }
        } catch (err: any) {
          console.error(err);
          logToConsole(`Serial read error: ${err.name}: ${err.message}`);
          lost = !port.readable;
        } finally {
          reader.releaseLock();
          portReaderRef.current = null;
        }

        if (lost) {
          try {
            await port.close(); // makes port.readable null, triggering a reopen
          } catch (e) {}
        }
      }
    } finally {
      try {
        await port.close();
      } catch (e) {}
      portRef.current = null;
      setIsPortConnected(false);
    }
  };

  // Parse custom raw NMEA block pasted by user
  const handleParsePastedNmea = () => {
    if (!pastedNmea.trim()) {
      setManualParseStatus("Please paste some NMEA sentences first.");
      return;
    }

    const lines = pastedNmea.split('\n');
    let validCount = 0;
    let parsedCount = 0;
    
    // Clear old state for manual run
    const freshParser = new NMEAParser();
    parserRef.current = freshParser;
    setTrackHistory([]);
    
    lines.forEach(line => {
      const trimmed = line.trim();
      if (trimmed.startsWith('$')) {
        validCount++;
        const { data, updated } = freshParser.parseSentence(trimmed);
        if (updated) {
          parsedCount++;
          setGnssData({ ...data });
        }
      }
    });

    setSatellites(freshParser.getSatellitesList());
    
    if (parsedCount > 0) {
      setManualParseStatus(`Parsed ${parsedCount} positioning instances from ${validCount} valid sentences.`);
      logToConsole(`Manually imported ${validCount} custom NMEA lines.`);
    } else {
      setManualParseStatus(`Read ${validCount} lines but no positional sentences (GGA / RMC / KSXT) were found.`);
    }
  };

  // Restart Simulation
  const handleResetSimulation = () => {
    parserRef.current = new NMEAParser();
    setGnssData(parserRef.current.getInitialState());
    setSatellites([]);
    setTrackHistory([]);
    setSimStep(0);
    setTotalTicks(0);
    logToConsole("Simulation reset successfully.");
  };

  // Helper: Download the python tracker script dynamically
  const downloadPythonScript = () => {
    // Generate script with customized COM and Baud rate values embedded!
    let customizedScript = PYTHON_SCRIPT_TEXT;
    customizedScript = customizedScript.replace(`default='/dev/ttyUSB0'`, `default='${pythonComPort}'`);
    customizedScript = customizedScript.replace(`default=115200`, `default=${pythonBaud}`);

    const blob = new Blob([customizedScript], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'unicore_um960_tracker.py';
    link.click();
    URL.revokeObjectURL(url);
    logToConsole("Exported customized unicore_um960_tracker.py to desktop.");
  };

  // Copy to clipboard
  const copyPythonScriptToClipboard = () => {
    let customizedScript = PYTHON_SCRIPT_TEXT;
    customizedScript = customizedScript.replace(`default='/dev/ttyUSB0'`, `default='${pythonComPort}'`);
    customizedScript = customizedScript.replace(`default=115200`, `default=${pythonBaud}`);

    navigator.clipboard.writeText(customizedScript);
    setIsCopied(true);
    setTimeout(() => setIsCopied(false), 2000);
  };

  // Calculate coordinates grid boundary validity
  // BGS2005 UTM 34N is highly suited for western Bulgaria (X: around 200000 - 550000).
  // GGRS87 Greek Grid is valid inside Greece (usually Easting 100000 to 900000, Northing 3800000 to 4600000).
  const bgBounds = {
    minLat: 41.2, maxLat: 44.3,
    minLon: 22.1, maxLon: 28.6
  };
  const grBounds = {
    minLat: 34.8, maxLat: 41.8,
    minLon: 19.3, maxLon: 28.3
  };

  const isLatestInBulgaria = gnssData.latitude >= bgBounds.minLat && gnssData.latitude <= bgBounds.maxLat && gnssData.longitude >= bgBounds.minLon && gnssData.longitude <= bgBounds.maxLon;
  const isLatestInGreece = gnssData.latitude >= grBounds.minLat && gnssData.latitude <= grBounds.maxLat && gnssData.longitude >= grBounds.minLon && gnssData.longitude <= grBounds.maxLon;

  // Render Star Constellation color labels
  const getConstellationColor = (type: string) => {
    switch (type) {
      case 'GPS': return '#3b82f6'; // Blue
      case 'BeiDou': return '#ef4444'; // Red
      case 'GLONASS': return '#10b981'; // Green
      case 'Galileo': return '#eab308'; // Amber
      case 'QZSS': return '#8b5cf6'; // Purple
      default: return '#6b7280'; // Gray
    }
  };

  // Render Quality Badge
  const renderQualityBadge = (q: number) => {
    switch (q) {
      case 4:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full bg-emerald-550/10 text-emerald-400 border border-emerald-500/20">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            RTK Fixed (Precise)
          </span>
        );
      case 5:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/25">
            <span className="w-2 h-2 rounded-full bg-blue-400" />
            RTK Float
          </span>
        );
      case 2:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full bg-yellow-500/10 text-yellow-500 border border-yellow-500/25">
            <span className="w-2 h-2 rounded-full bg-yellow-500" />
            DGPS
          </span>
        );
      case 1:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full bg-amber-500/10 text-amber-500 border border-amber-500/25">
            <span className="w-2 h-2 rounded-full bg-amber-500" />
            GNSS Single
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-full bg-red-500/15 text-red-500 border border-red-500/30">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            No Solution
          </span>
        );
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans selection:bg-emerald-500 selection:text-slate-950">
      
      {/* HEADER SECTION */}
      <header className="border-b border-slate-900 bg-slate-950/80 backdrop-blur-md sticky top-0 z-50 px-6 py-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-emerald-500/10 rounded-xl border border-emerald-500/20 text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.07)]">
            <Radio className="w-6 h-6 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-white leading-none">Unicore UM960</h1>
              <span className="px-1.5 py-0.5 text-[10px] font-mono font-bold bg-slate-900 text-slate-400 border border-slate-800 rounded">RTK / GNSS</span>
            </div>
            <p className="text-xs text-slate-400 mt-1">Geographic Grids & Western Bulgaria-Northern Greece Cadastral Dashboard</p>
          </div>
        </div>

        {/* Top Status Badges */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Active Connection State */}
          <div className="p-1 pr-3 bg-slate-900 rounded-full border border-slate-800/80 flex items-center gap-2">
            <div className={`p-1.5 rounded-full ${isPortConnected ? 'bg-emerald-500 text-slate-950 animate-pulse' : 'bg-blue-600 text-blue-100'}`}>
              <Cpu className="w-3.5 h-3.5" />
            </div>
            <span className="text-xs font-mono font-medium">
              {isPortConnected ? 'LIVE PORT' : 'EMULATION FEEDS'}
            </span>
          </div>

          <div className="bg-slate-900 px-3 py-1.5 rounded-lg border border-slate-800 flex items-center gap-2 text-xs text-slate-300">
            <Wifi className="w-4 h-4 text-emerald-400" />
            <span className="font-medium text-slate-400">Satellites:</span>
            <span className="font-mono font-bold text-white">{gnssData.numSatellites}</span>
          </div>

          <div className="bg-slate-900 px-3 py-1.5 rounded-lg border border-slate-800 flex items-center gap-2 text-xs">
            <span className="font-medium text-slate-400">Heading:</span>
            <span className="font-mono font-bold text-white">
              {!isNaN(gnssData.headingDeg) ? `${gnssData.headingDeg.toFixed(1)}°` : 'N/A 🧭'}
            </span>
          </div>

          {renderQualityBadge(gnssData.fixQuality)}
        </div>
      </header>

      {/* TABS NAVIGATION */}
      <div className="bg-slate-950 px-6 py-2 border-b border-slate-900/60 flex items-center justify-between">
        <div className="flex items-center gap-1 bg-slate-900 p-1 rounded-lg border border-slate-850">
          <button 
            id="tab-visualizer"
            onClick={() => setActiveTab('visualizer')}
            className={`px-4 py-2 text-xs font-semibold rounded-md transition-all flex items-center gap-2 ${activeTab === 'visualizer' ? 'bg-emerald-550/15 text-emerald-400 border border-emerald-500/20' : 'text-slate-400 hover:text-slate-200'}`}
          >
            <Compass className="w-4 h-4" />
            GIS Dashboard
          </button>
          <button 
            id="tab-script"
            onClick={() => setActiveTab('script')}
            className={`px-4 py-2 text-xs font-semibold rounded-md transition-all flex items-center gap-2 ${activeTab === 'script' ? 'bg-emerald-555/15 text-emerald-400 border border-emerald-500/20' : 'text-slate-400 hover:text-slate-200'}`}
          >
            <Download className="w-4 h-4" />
            Python Script Exporter
          </button>
          <button 
            id="tab-manual"
            onClick={() => setActiveTab('manual')}
            className={`px-4 py-2 text-xs font-semibold rounded-md transition-all flex items-center gap-2 ${activeTab === 'manual' ? 'bg-emerald-555/15 text-emerald-400 border border-emerald-500/20' : 'text-slate-400 hover:text-slate-200'}`}
          >
            <FileText className="w-4 h-4" />
            NMEA Decoder Terminal
          </button>
        </div>

        <div className="hidden lg:flex items-center gap-2 text-xs text-slate-400 font-mono">
          <span className="w-2 h-2 rounded-full bg-emerald-500" />
          NMEA Parser Active: <span className="text-white bg-slate-900 font-bold px-1.5 rounded border border-slate-800">{gnssData.lastSentenceType || 'WAITING'}</span>
        </div>
      </div>

      {/* MAIN VIEW CONTROLLER */}
      <main className="p-6">
        {activeTab === 'visualizer' && (
          <div className="flex flex-col gap-6">
            
            {/* GIS DIGITAL REFERENCE ENGINE BAR */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              
              {/* CARD 1: WGS84 */}
              <div id="wgs84-card" className="bg-slate-900 p-5 rounded-xl border border-slate-800 relative overflow-hidden group shadow-lg">
                <div className="absolute right-3 top-3 opacity-10 group-hover:opacity-15 transition-opacity">
                  <MapPin className="w-20 h-20 text-white" />
                </div>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <span className="w-1.5 h-3 bg-cyan-500 rounded" />
                    WGS84 Reference
                  </h3>
                  <span className="px-2 py-0.5 text-[10px] font-mono font-semibold bg-cyan-950 text-cyan-400 rounded border border-cyan-800/40">Geodetic</span>
                </div>
                <div className="space-y-3 font-mono">
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">LATITUDE</span>
                    <span className="text-sm font-bold text-white">{gnssData.latitude.toFixed(8)}° N</span>
                  </div>
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">LONGITUDE</span>
                    <span className="text-sm font-bold text-white">{gnssData.longitude.toFixed(8)}° E</span>
                  </div>
                  <div className="flex justify-between pb-1">
                    <span className="text-xs text-slate-400">ELLIPSOIDAL HT</span>
                    <span className="text-sm font-bold text-cyan-400">{gnssData.altitude.toFixed(3)} m</span>
                  </div>
                </div>
                <p className="text-[10px] text-slate-400 font-medium mt-3 flex items-center gap-1.5">
                  <HelpCircle className="w-3 h-3 text-cyan-500" /> Standard global orbit model ellipsoid (WGS-84)
                </p>
              </div>

              {/* CARD 2: BGS2005 (Bulgarian Geodetic System) */}
              <div id="bgs2500-card" className="bg-slate-900 p-5 rounded-xl border border-slate-800 relative overflow-hidden group shadow-lg">
                <div className="absolute right-3 top-3 opacity-10 group-hover:opacity-15 transition-opacity">
                  <Layers className="w-20 h-20 text-teal-400" />
                </div>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <span className="w-1.5 h-3 bg-teal-500 rounded" />
                    БГС2005 (Bulgaria)
                  </h3>
                  <div className="flex gap-1.5">
                    <span className="px-1.5 py-0.5 text-[9px] font-mono font-semibold bg-teal-950 text-teal-400 rounded border border-teal-800/30">EPSG:7803</span>
                    <span className="px-1.5 py-0.5 text-[9px] font-mono font-semibold bg-slate-850 text-slate-300 rounded border border-slate-800">EPSG:7801</span>
                  </div>
                </div>
                <div className="space-y-3 font-mono">
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">UTM 34N EASTING (X)</span>
                    <span className="text-sm font-bold text-white">{isNaN(gnssData.bgsEastUTM34) ? '---' : `${gnssData.bgsEastUTM34.toFixed(3)} m`}</span>
                  </div>
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">UTM 34N NORTHING (Y)</span>
                    <span className="text-sm font-bold text-white">{isNaN(gnssData.bgsNorthUTM34) ? '---' : `${gnssData.bgsNorthUTM34.toFixed(3)} m`}</span>
                  </div>
                  <div className="flex justify-between pb-1">
                    <span className="text-xs text-slate-450">LAMBERT EAST / NORTH</span>
                    <span className="text-xs font-bold text-teal-300">
                      {isNaN(gnssData.bgsEastLambert) ? '---' : `${gnssData.bgsEastLambert.toFixed(1)} E, ${gnssData.bgsNorthLambert.toFixed(1)} N`}
                    </span>
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${isLatestInBulgaria ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-500'}`}>
                    {isLatestInBulgaria ? '✓ INSIDE BULGARIA BOUNDARY' : '⚠ TRANS-EASTING DISTORTION'}
                  </span>
                  <span className="text-[10px] text-slate-500 font-semibold font-mono">UTM Zone 34</span>
                </div>
              </div>

              {/* CARD 3: Greek Grid HGRS87 */}
              <div id="greekgrid-card" className="bg-slate-900 p-5 rounded-xl border border-slate-800 relative overflow-hidden group shadow-lg">
                <div className="absolute right-3 top-3 opacity-10 group-hover:opacity-15 transition-opacity">
                  <Table className="w-20 h-20 text-blue-400" />
                </div>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-sm font-bold uppercase tracking-wider text-slate-400 flex items-center gap-2">
                    <span className="w-1.5 h-3 bg-blue-500 rounded" />
                    HGRS87 / Greek Grid
                  </h3>
                  <span className="px-2 py-0.5 text-[10px] font-mono font-semibold bg-blue-950 text-blue-400 rounded border border-blue-900/40">EPSG:2100</span>
                </div>
                <div className="space-y-3 font-mono">
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">GREEK EASTING (DX)</span>
                    <span className="text-sm font-bold text-white">{isNaN(gnssData.greekEasting) ? '---' : `${gnssData.greekEasting.toFixed(3)} m`}</span>
                  </div>
                  <div className="flex justify-between border-b border-slate-800/40 pb-2">
                    <span className="text-xs text-slate-400">GREEK NORTHING (DY)</span>
                    <span className="text-sm font-bold text-white">{isNaN(gnssData.greekNorthing) ? '---' : `${gnssData.greekNorthing.toFixed(3)} m`}</span>
                  </div>
                  <div className="flex justify-between pb-1">
                    <span className="text-xs text-slate-400">CENTRAL MERIDIAN</span>
                    <span className="text-sm font-bold text-blue-400">24° 00&apos; 00&quot; E</span>
                  </div>
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${isLatestInGreece ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-500'}`}>
                    {isLatestInGreece ? '✓ INSIDE GREECE CADASTRE' : '⚠ OUTSIDE REGIONAL VALIDITY'}
                  </span>
                  <span className="text-[10px] text-slate-500 font-semibold font-mono font-bold">K0 = 0.9996</span>
                </div>
              </div>

            </div>

            {/* LOWER PORTION: MAP & CONTROLS BENTO GRID */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              
              {/* LEFT SIDEBAR: INSTRUMENT CONTROLS. 4 Cols */}
              <div className="lg:col-span-4 flex flex-col gap-6">
                
                {/* 1. SEED STREAM CONNECTOR */}
                <div className="bg-slate-900 rounded-xl border border-slate-850 p-5">
                  <h3 className="text-sm font-bold text-white mb-4 uppercase tracking-wider flex items-center gap-2">
                    <Settings className="w-4 h-4 text-emerald-400" />
                    Receiver Connection
                  </h3>

                  {/* WEBSERIAL CONNECT CONTROLS */}
                  <div className="space-y-4">
                    <div className="p-3 bg-slate-950 rounded-lg border border-slate-850">
                      <p className="text-[11px] text-slate-400 mb-2 leading-relaxed font-medium">
                        Connect an actual Unicore UM960 receiver module to your computer over USB Serial. Note: Web Serial requires frame permissions. If blocked, open in external tab.
                      </p>
                      
                      <div className="grid grid-cols-2 gap-2 mt-3">
                        <div>
                          <label className="text-[10px] text-slate-405 block uppercase text-slate-404 font-semibold mb-1">Baud Rate</label>
                          <select 
                            value={serialConfig.baudRate}
                            onChange={(e) => setSerialConfig(prev => ({ ...prev, baudRate: parseInt(e.target.value) }))}
                            className="w-full bg-slate-900 text-xs font-mono text-white p-1.5 rounded border border-slate-800 focus:outline-none focus:border-emerald-500"
                          >
                            <option value="9600">9600 bps</option>
                            <option value="115200">115200 bps</option>
                            <option value="230400">230400 bps</option>
                            <option value="460800">460800 bps</option>
                          </select>
                        </div>
                        <div>
                          <label className="text-[10px] block uppercase text-slate-404 font-semibold mb-1">COM Port</label>
                          <button 
                            id="btn-serial-connect"
                            onClick={handleConnectSerial}
                            className={`w-full py-1.5 rounded text-xs font-bold transition-all border flex items-center justify-center gap-1.5 ${isPortConnected ? 'bg-rose-600 border-rose-500 hover:bg-rose-700 text-white' : 'bg-emerald-600 border-emerald-500 hover:bg-emerald-700 text-slate-950 font-black'}`}
                          >
                            <Cpu className="w-3.5 h-3.5" />
                            {isPortConnected ? 'Disconnect' : 'Connect COM'}
                          </button>
                        </div>
                      </div>

                      {serialError && (
                        <div className="mt-3 p-2 bg-red-950/40 border border-red-900/40 rounded text-[10px] text-red-400 flex items-start gap-1.5">
                          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                          <span>{serialError}</span>
                        </div>
                      )}
                    </div>

                    {/* LIVE TRACK SIMULATION CONFIG */}
                    <div className="space-y-3 pt-2">
                      <div className="flex items-center justify-between">
                        <label className="text-xs uppercase font-bold text-slate-400">Emulated Route</label>
                        <span className="text-[10px] bg-slate-800 px-1.5 rounded text-slate-300 font-mono font-bold">SIMULATOR</span>
                      </div>
                      
                      <select 
                        value={selectedRouteKey}
                        disabled={isPortConnected}
                        onChange={(e) => {
                          setSelectedRouteKey(e.target.value as keyof typeof ROUTES);
                          setSimStep(0);
                        }}
                        className="w-full bg-slate-950 text-xs text-white p-2 rounded border border-slate-850 hover:border-slate-800 focus:outline-none focus:border-emerald-500"
                      >
                        <option value="interstate">📍 Cross-Border: Sofia ➔ Thessaloniki</option>
                        <option value="bulgaria_cadastre">🚜 Pernik: Western Bulgaria Farmland Grid</option>
                        <option value="greece_cadastre">🇬🇷 Drama: Northern Greece City Grid</option>
                      </select>

                      <p className="text-[10px] text-slate-400 leading-relaxed italic bg-slate-950/40 p-2 rounded border border-slate-850/60">
                        {ROUTES[selectedRouteKey].description}
                      </p>

                      <div className="flex items-center justify-between bg-slate-950 p-2 rounded border border-slate-850">
                        {/* Simulation clock controls */}
                        <div className="flex items-center gap-1.5">
                          <button
                            id="btn-play-pause"
                            disabled={isPortConnected}
                            onClick={() => setIsPlaying(prev => !prev)}
                            className={`p-2 rounded text-slate-900 ${isPlaying ? 'bg-yellow-500 hover:bg-yellow-600' : 'bg-emerald-500 hover:bg-emerald-650'}`}
                            title={isPlaying ? "Pause Stream" : "Resume Stream"}
                          >
                            {isPlaying ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                          </button>
                          
                          <button
                            id="btn-reset-sim"
                            onClick={handleResetSimulation}
                            className="p-2 bg-slate-800 text-slate-350 rounded hover:bg-slate-700 hover:text-white"
                            title="Reset Log"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        {/* Speed Adjust */}
                        <div className="flex items-center gap-1 text-[11px]">
                          <span className="text-slate-400">Interval:</span>
                          <select 
                            value={simSpeed}
                            onChange={(e) => setSimSpeed(parseFloat(e.target.value))}
                            className="bg-slate-900 text-white font-mono px-1 py-0.5 rounded focus:outline-none focus:border-emerald-500 border border-slate-800"
                          >
                            <option value="0.2">5 Hz</option>
                            <option value="0.5">2 Hz</option>
                            <option value="1">1 Hz</option>
                            <option value="3">0.3 Hz</option>
                          </select>
                        </div>
                      </div>
                    </div>

                  </div>
                </div>

                {/* 2. SATELLITE CONSTELLATIONS STATS */}
                <div className="bg-slate-900 rounded-xl border border-slate-850 p-5">
                  <h3 className="text-sm font-bold text-white mb-4 uppercase tracking-wider flex items-center gap-2">
                    <Wifi className="w-4 h-4 text-emerald-400" />
                    BDS / GNSS Constellations
                  </h3>
                  
                  <div className="space-y-4">
                    {/* Constellation Multi-bar */}
                    <div className="p-3 bg-slate-950 rounded-lg border border-slate-850 space-y-2">
                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-red-400 font-semibold flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-red-500" /> BeiDou (BDS)
                          </span>
                          <span className="font-mono font-bold text-white">{gnssData.beidouCount} sats</span>
                        </div>
                        <div className="w-full bg-slate-900 h-2 rounded overflow-hidden">
                          <div 
                            className="bg-red-500 h-full transition-all duration-500" 
                            style={{ width: `${Math.min(100, (gnssData.beidouCount / 14) * 100)}%` }}
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-blue-400 font-semibold flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-blue-500" /> GPS (USA)
                          </span>
                          <span className="font-mono font-bold text-white">{gnssData.gpsCount} sats</span>
                        </div>
                        <div className="w-full bg-slate-900 h-2 rounded overflow-hidden">
                          <div 
                            className="bg-blue-500 h-full transition-all duration-500" 
                            style={{ width: `${Math.min(100, (gnssData.gpsCount / 12) * 100)}%` }}
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-emerald-400 font-semibold flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-emerald-500" /> GLONASS (RU)
                          </span>
                          <span className="font-mono font-bold text-white">{gnssData.glonassCount} sats</span>
                        </div>
                        <div className="w-full bg-slate-900 h-2 rounded overflow-hidden">
                          <div 
                            className="bg-emerald-500 h-full transition-all duration-500" 
                            style={{ width: `${Math.min(100, (gnssData.glonassCount / 10) * 100)}%` }}
                          />
                        </div>
                      </div>

                      <div>
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-yellow-400 font-semibold flex items-center gap-1">
                            <span className="w-2 h-2 rounded-full bg-yellow-400" /> Galileo (EU)
                          </span>
                          <span className="font-mono font-bold text-white">{gnssData.galileoCount} sats</span>
                        </div>
                        <div className="w-full bg-slate-900 h-2 rounded overflow-hidden">
                          <div 
                            className="bg-yellow-400 h-full transition-all duration-500" 
                            style={{ width: `${Math.min(100, (gnssData.galileoCount / 8) * 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>

                    {/* Dilution of Precision metrics */}
                    <div className="grid grid-cols-3 gap-2">
                      <div className="bg-slate-950 p-2 rounded border border-slate-850/80 text-center">
                        <div className="text-[10px] text-slate-400 font-bold uppercase">HDOP</div>
                        <div className="text-sm font-mono font-bold text-teal-400 mt-1">{gnssData.hdop.toFixed(1)}</div>
                        <div className="text-[9px] text-slate-500 mt-1">Excellent</div>
                      </div>
                      <div className="bg-slate-950 p-2 rounded border border-slate-850/80 text-center">
                        <div className="text-[10px] text-slate-400 font-bold uppercase">VDOP</div>
                        <div className="text-sm font-mono font-bold text-cyan-400 mt-1">{gnssData.vdop.toFixed(1)}</div>
                        <div className="text-[9px] text-slate-500 mt-1">Ideal</div>
                      </div>
                      <div className="bg-slate-950 p-2 rounded border border-slate-850/80 text-center">
                        <div className="text-[10px] text-slate-400 font-bold uppercase">PDOP</div>
                        <div className="text-sm font-mono font-bold text-indigo-400 mt-1">{gnssData.pdop.toFixed(1)}</div>
                        <div className="text-[9px] text-slate-500 mt-1">Spheroid</div>
                      </div>
                    </div>

                    {/* Dual Antenna Orientation pitch/roll if KSXT parsed */}
                    <div className="p-2.5 bg-slate-950 rounded-lg border border-slate-850 text-xs flex items-center justify-between">
                      <span className="text-slate-400">Antenna Sensors:</span>
                      <span className="font-mono text-slate-300">
                        Pitch: <span className="font-bold text-white">{gnssData.pitchDeg.toFixed(2)}°</span> | Roll: <span className="font-bold text-white">{gnssData.rollDeg.toFixed(2)}°</span>
                      </span>
                    </div>

                  </div>
                </div>

              </div>

              {/* MIDDLE SECTION: MAIN GIS MAP & GEOGRAPHIC OVERLAY. 8 Cols */}
              <div className="lg:col-span-8 flex flex-col gap-6">
                
                {/* INTERACTIVE GEOGRAPHIC GIS AND COORDINATES PLOTTER */}
                <div className="bg-slate-900 rounded-xl border border-slate-850 p-5 flex flex-col h-full min-h-[500px]">
                  
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                    <div>
                      <h3 className="text-sm font-bold text-white uppercase tracking-wider flex items-center gap-2">
                        <Layers className="w-4 h-4 text-emerald-400" />
                        Live Trajectory Tracking Canvas
                      </h3>
                      <p className="text-[11px] text-slate-400 mt-0.5">Dual-grid mapping overlay with coordinate tic marks representation</p>
                    </div>

                    {/* Map Mode Buttons */}
                    <div className="flex items-center gap-1.5 bg-slate-950 p-1 rounded-lg border border-slate-850 self-start">
                      <button 
                        onClick={() => setMapMode('hybrid_grid')}
                        className={`px-2.5 py-1 text-[10px] rounded font-semibold transition-all ${mapMode === 'hybrid_grid' ? 'bg-emerald-500 text-slate-950 font-black' : 'text-slate-300 hover:text-white'}`}
                      >
                        Scientific GIS Grid
                      </button>
                      <button 
                        onClick={() => setMapMode('openstreetmap')}
                        className={`px-2.5 py-1 text-[10px] rounded font-semibold transition-all ${mapMode === 'openstreetmap' ? 'bg-emerald-500 text-slate-950 font-black' : 'text-slate-300 hover:text-white flex items-center gap-1'}`}
                      >
                        OSM Live Map
                        <ExternalLink className="w-2.5 h-2.5" />
                      </button>
                    </div>
                  </div>

                  {/* 1. VISUAL GRID OVERLAY DESIGN */}
                  {mapMode === 'hybrid_grid' ? (
                    <div className="flex-grow bg-slate-950 rounded-lg p-4 border border-slate-850 relative min-h-[380px] overflow-hidden flex flex-col justify-between">
                      
                      {/* Grid Lines Overlay representing coordinates */}
                      <div className="absolute inset-0 grid grid-cols-12 grid-rows-12 pointer-events-none opacity-[0.06]">
                        {Array.from({ length: 144 }).map((_, i) => (
                          <div key={i} className="border-r border-b border-white" />
                        ))}
                      </div>

                      {/* Boundary Labels representation */}
                      <div className="absolute top-3 left-4 text-[10px] font-mono p-1 bg-slate-900/85 border border-slate-800 text-slate-400 rounded">
                        BULGARIA GRID ZONE (BGS2005 UTM 34N / Lambert)
                      </div>
                      <div className="absolute bottom-3 right-4 text-[10px] font-mono p-1 bg-slate-900/85 border border-slate-800 text-slate-400 rounded">
                        GREEK GRID CADASTRAL (EPSG:2100)
                      </div>

                      {/* Canvas Graphics representation representing coordinates conversion */}
                      <div className="w-full flex-grow flex items-center justify-center relative bg-slate-950/20 rounded-md">
                        
                        {/* Dynamic Path Drawer SVG */}
                        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 100 100" preserveAspectRatio="none">
                          
                          {/* Siting the border line (Lat 41.38 represents the BG/GR border) */}
                          {/* In our SVG, let's represent vertical/horizontal borders */}
                          <line x1="0" y1="52" x2="100" y2="52" stroke="#ea580c" strokeWidth="0.8" strokeDasharray="3 3" opacity="0.6" />
                          <text x="3" y="50" fill="#ea580c" fontSize="3" fontFamily="monospace" fontWeight="semibold" opacity="0.8">BORDER LEVEL (LAT 41°22&apos;N)</text>
                          
                          {/* Sofia Center mark */}
                          <circle cx="35" cy="15" r="1.5" fill="#10b981" />
                          <text x="38" y="16" fill="#10b981" fontSize="2.5" fontFamily="sans-serif">Sofia (BG)</text>

                          {/* Thessaloniki mark */}
                          <circle cx="28" cy="88" r="1.5" fill="#3b82f6" />
                          <text x="31" y="89" fill="#3b82f6" fontSize="2.5" fontFamily="sans-serif">Thessaloniki (GR)</text>

                          {/* Trajectory connecting path */}
                          {trackHistory.length > 1 && (
                            <path
                              d={`M ${trackHistory.map((pt) => { // map latitude 42.69 - 40.64 to svg Y coord, longitude 22.9 - 23.3 to svg X coord
                                // lat maps Y from 10 to 90
                                const y = 10 + ((42.6976 - pt.latitude) / (42.6976 - 40.6401)) * 75;
                                // lon maps X from 15 to 85
                                const x = 35 - ((23.3219 - pt.longitude) / (23.3219 - 22.9444)) * 7;
                                return `${x} ${y}`;
                              }).join(' L ')}`}
                              fill="none"
                              stroke={gnssData.fixQuality === 4 ? '#10b981' : (gnssData.fixQuality === 5 ? '#3b82f6' : '#f59e0b')}
                              strokeWidth="1.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          )}

                          {/* Current Vehicle GPS location blinking marker */}
                          {trackHistory.length > 0 && (() => {
                            const lastPt = trackHistory[trackHistory.length - 1];
                            const y = 10 + ((42.6976 - lastPt.latitude) / (42.6976 - 40.6401)) * 75;
                            const x = 35 - ((23.3219 - lastPt.longitude) / (23.3219 - 22.9444)) * 7;
                            return (
                              <g>
                                <circle cx={x} cy={y} r="3" fill="#ef4444" opacity="0.3" className="animate-ping" />
                                <circle cx={x} cy={y} r="1.5" fill="#ef4444" />
                              </g>
                            );
                          })()}
                        </svg>

                        {/* Top left detailed coordinate display */}
                        <div className="absolute top-3 right-3 p-3 bg-slate-900/90 rounded-md border border-slate-800 text-xs font-mono space-y-1 z-10 select-none">
                          <div className="text-[10px] text-slate-400 font-bold uppercase">Grid Metrics (WGS84)</div>
                          <div>Lat: <span className="text-white font-bold">{gnssData.latitude.toFixed(6)}°</span></div>
                          <div>Lon: <span className="text-white font-bold">{gnssData.longitude.toFixed(6)}°</span></div>
                          <div className="pt-1.5 border-t border-slate-800 text-[9px] text-slate-400">
                            BGS-Y: <span className="text-emerald-400">{isNaN(gnssData.bgsNorthUTM34) ? '---' : Math.round(gnssData.bgsNorthUTM34)} m</span>
                          </div>
                          <div className="text-[9px] text-slate-400">
                            GGR-X: <span className="text-blue-400">{isNaN(gnssData.greekEasting) ? '---' : Math.round(gnssData.greekEasting)} m</span>
                          </div>
                        </div>

                        {/* Visual Compass Overlay inside plot */}
                        <div className="absolute bottom-3 left-3 flex items-center gap-2 p-2 bg-slate-900/90 border border-slate-800 rounded-lg">
                          <Compass className="w-5 h-5 text-emerald-400" style={{ transform: `rotate(${gnssData.headingDeg || gnssData.courseDeg || 0}deg)` }} />
                          <div className="text-[10px] font-mono leading-none">
                            <div className="text-slate-400">HEADING</div>
                            <div className="text-white font-bold mt-1">
                              {!isNaN(gnssData.headingDeg) ? `${gnssData.headingDeg.toFixed(1)}°` : 'N/A'}
                            </div>
                          </div>
                        </div>

                      </div>

                      {/* Display warning alerts if receiver goes off limits */}
                      {!isLatestInBulgaria && !isLatestInGreece ? (
                        <div className="p-2.5 bg-yellow-950/25 border border-yellow-800/25 rounded text-[10px] text-yellow-400 flex items-center gap-2 font-mono m-1">
                          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
                          <span>Out of range for BGS2005 (Bulgaria) and HGRS87 (Greece) grids. Simulations will reset.</span>
                        </div>
                      ) : null}

                    </div>
                  ) : (
                    /* 2. OPEN STREET MAP DIRECT EMBED FRAME */
                    <div className="flex-grow bg-slate-950 rounded-lg border border-slate-850 relative min-h-[380px] overflow-hidden flex flex-col">
                      {(() => {
                        const latSpan = 180 / Math.pow(2, osmZoom - 3);
                        const lonSpan = latSpan * 1.5;
                        const minLon = mapCenterLon - lonSpan / 2;
                        const minLat = mapCenterLat - latSpan / 2;
                        const maxLon = mapCenterLon + lonSpan / 2;
                        const maxLat = mapCenterLat + latSpan / 2;
                        const markerLat = gnssData.latitude || mapCenterLat;
                        const markerLon = gnssData.longitude || mapCenterLon;
                        const embedSrc = `https://www.openstreetmap.org/export/embed.html?bbox=${minLon}%2C${minLat}%2C${maxLon}%2C${maxLat}&layer=mapnik&marker=${markerLat}%2C${markerLon}`;
                        
                        return (
                          <>
                            <iframe 
                              title="OpenStreetMap Live Tracker"
                              src={embedSrc}
                              className="w-full h-full min-h-[380px] border-none opacity-85 hover:opacity-100 transition-opacity"
                            />
                            
                            {/* OSD Map Control Overlays */}
                            <div className="absolute top-3 right-3 flex flex-col gap-2 z-10">
                              {/* Zoom Group */}
                              <div className="flex flex-col rounded-lg bg-slate-900/95 border border-slate-800 shadow-lg overflow-hidden">
                                <button
                                  onClick={() => setOsmZoom(z => Math.min(18, z + 1))}
                                  className="p-2 text-slate-300 hover:text-white hover:bg-slate-800 border-b border-slate-800 transition-colors"
                                  title="Zoom In"
                                  type="button"
                                >
                                  <ZoomIn className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => setOsmZoom(z => Math.max(8, z - 1))}
                                  className="p-2 text-slate-300 hover:text-white hover:bg-slate-800 transition-colors"
                                  title="Zoom Out"
                                  type="button"
                                >
                                  <ZoomOut className="w-4 h-4" />
                                </button>
                              </div>

                              {/* Follow Group */}
                              <button
                                onClick={() => {
                                  const nextFollow = !mapFollow;
                                  setMapFollow(nextFollow);
                                  if (nextFollow) {
                                    if (gnssData.latitude && gnssData.longitude) {
                                      setMapCenterLat(gnssData.latitude);
                                      setMapCenterLon(gnssData.longitude);
                                      lastUpdateRef.current = Date.now();
                                    }
                                  }
                                }}
                                className={`p-2 rounded-lg border transition-all flex items-center justify-center shadow-lg ${
                                  mapFollow 
                                    ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40 hover:bg-emerald-500/30' 
                                    : 'bg-slate-900/95 text-slate-400 border-slate-800 hover:text-white hover:bg-slate-800'
                                }`}
                                title={mapFollow ? "Disable Follow Mode" : "Enable Follow Mode"}
                                type="button"
                              >
                                <Crosshair className={`w-4 h-4 ${mapFollow ? 'animate-pulse text-emerald-400' : ''}`} />
                              </button>

                              {/* Manual Center Pin */}
                              <button
                                onClick={centerMapOnReceiver}
                                className="p-2 rounded-lg bg-slate-900/95 text-slate-300 border border-slate-800 hover:text-white hover:bg-slate-800 transition-colors shadow-lg flex items-center justify-center hover:border-emerald-500/50"
                                title="Center Map on Receiver"
                                type="button"
                              >
                                <MapPin className="w-4 h-4" />
                              </button>
                            </div>

                            <div className="absolute bottom-2.5 left-2.5 bg-slate-900/90 px-3 py-1.5 border border-slate-800 rounded-lg text-[9px] text-slate-400 font-mono flex flex-col gap-0.5 pointer-events-none shadow-md">
                              <span className="text-white font-bold text-[10px] flex items-center gap-1">
                                <span className={`w-1.5 h-1.5 rounded-full ${mapFollow ? 'bg-emerald-500 animate-ping' : 'bg-amber-500'}`} />
                                {mapFollow ? 'Following Active Track' : 'Viewport Locked'}
                              </span>
                              <span>Zoom: {osmZoom} • Lat: {markerLat.toFixed(5)} • Lon: {markerLon.toFixed(5)}</span>
                            </div>
                          </>
                        );
                      })()}
                    </div>
                  )}

                  {/* Satellite Signal Skyplot Instrument (Bottom part of center area) */}
                  <div className="grid grid-cols-1 md:grid-cols-12 gap-5 mt-5 border-t border-slate-800/50 pt-5">
                    
                    {/* Radial Skyplot dome (5 cols) */}
                    <div className="md:col-span-5 flex flex-col items-center">
                      <span className="text-[11px] font-bold uppercase text-slate-400 tracking-wider mb-2 select-none">Skyplot Satellite Dome</span>
                      
                      <div className="w-[170px] h-[170px] bg-slate-950 rounded-full border border-slate-800/80 relative flex items-center justify-center p-2">
                        
                        {/* Ring concentric layers */}
                        <div className="absolute w-[140px] h-[140px] border border-slate-905 border-dashed rounded-full" />
                        <div className="absolute w-[100px] h-[100px] border border-slate-900 border-dashed rounded-full" />
                        <div className="absolute w-[60px] h-[60px] border border-slate-850 border-dashed rounded-full animate-pulse" />
                        
                        {/* Axes N-S / E-W */}
                        <div className="absolute w-full h-[1px] bg-slate-900/60" />
                        <div className="absolute h-full w-[1px] bg-slate-805/60" />
                        
                        <span className="absolute top-1 text-[9px] font-mono text-slate-500 font-bold">N</span>
                        <span className="absolute bottom-1 text-[9px] font-mono text-slate-500 font-bold">S</span>
                        <span className="absolute right-1 text-[9px] font-mono text-slate-500 font-bold">E</span>
                        <span className="absolute left-1 text-[9px] font-mono text-slate-500 font-bold">W</span>

                        {/* Plots actual active received satellites */}
                        {satellites.slice(0, 10).map((sat, idx) => {
                          // radial coordinate maths
                          // elevation 90 is center (r=0), elevation 0 is horizon (r=outer_radius=85px)
                          const r = 85 * ((90 - sat.elevation) / 90);
                          // convert azimuth degrees to radians clockwise from North (subtract 90 deg for math coord offset)
                          const rad = ((sat.azimuth - 90) * Math.PI) / 180;
                          const x = 85 + r * Math.cos(rad);
                          const y = 85 + r * Math.sin(rad);

                          const cColor = getConstellationColor(sat.constellation);

                          return (
                            <div 
                              key={idx}
                              title={`${sat.constellation} Sat PRN ${sat.prn} | Elev: ${sat.elevation}°, Azim: ${sat.azimuth}° | SNR: ${sat.snr}dB-Hz`}
                              className="absolute w-4 h-4 rounded-full border border-slate-950 flex items-center justify-center text-[7px] font-black cursor-help text-slate-100 transition-all duration-300 transform -translate-x-1/2 -translate-y-1/2"
                              style={{ 
                                left: `${x}px`, 
                                top: `${y}px`, 
                                backgroundColor: cColor,
                                boxShadow: `0 0 8px ${cColor}a0`
                              }}
                            >
                              {sat.prn}
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Carrier Signal strength indicator (7 cols) */}
                    <div className="md:col-span-7 space-y-3">
                      <span className="text-[11px] font-bold uppercase text-slate-400 tracking-wider select-none">Live Carrier Signal Power (C/N0 DB-Hz)</span>
                      
                      <div className="p-3 bg-slate-950 rounded-lg border border-slate-850 grid grid-cols-2 gap-3 min-h-[140px]">
                        {satellites.length > 0 ? (
                          satellites.slice(0, 6).map((sat, idx) => (
                            <div key={idx} className="space-y-1">
                              <div className="flex items-center justify-between text-[10px] font-mono">
                                <span className="font-bold text-white flex items-center gap-1">
                                  <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: getConstellationColor(sat.constellation) }} />
                                  {sat.constellation} PRN {sat.prn}
                                </span>
                                <span className="text-slate-400">{sat.snr} dB-Hz</span>
                              </div>
                              <div className="w-full bg-slate-900 h-1.5 rounded overflow-hidden">
                                <div 
                                  className="h-full rounded transition-all duration-500" 
                                  style={{ 
                                    width: `${(sat.snr / 50) * 100}%`,
                                    backgroundColor: sat.snr > 38 ? '#10b981' : (sat.snr > 28 ? '#eab308' : '#ef4444')
                                  }}
                                />
                              </div>
                            </div>
                          ))
                        ) : (
                          <div className="col-span-2 flex items-center justify-center text-slate-500 text-xs italic">
                            Waiting for GSV sentences in raw stream...
                          </div>
                        )}
                      </div>
                    </div>

                  </div>

                </div>

              </div>

            </div>

            {/* LOWER PORTION: NMEA STREAM LIVE SCROLLING TERMINAL */}
            <div className="bg-slate-900 rounded-xl border border-slate-850 p-5 mt-2">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500" />
                  </span>
                  <h3 className="text-sm font-bold text-white uppercase tracking-wider">Unicore UM960 Raw NMEA Stream Feed</h3>
                </div>
                <button
                  id="btn-clear-logs"
                  onClick={() => setConsoleLogs([])}
                  className="text-[10px] font-bold text-slate-400 hover:text-white uppercase tracking-wider bg-slate-950 px-2 py-1 rounded border border-slate-850"
                >
                  Clear Feed
                </button>
              </div>

              {/* Decoded Scroll Terminal */}
              <div ref={terminalContainerRef} className="bg-slate-950 p-4 rounded-lg border border-slate-850/80 h-32 overflow-y-auto font-mono text-xs text-emerald-400 space-y-1.5 select-all scrollbar-thin">
                {consoleLogs.map((log, i) => (
                  <div key={i} className="leading-5 tracking-wide hover:bg-slate-900/60 px-1 rounded transition-colors break-all">
                    {log}
                  </div>
                ))}
              </div>
            </div>

          </div>
        )}

        {/* PYTHON EXPORT MENU TAB */}
        {activeTab === 'script' && (
          <div className="bg-slate-900 rounded-xl border border-slate-850 p-6 md:p-8 max-w-5xl mx-auto shadow-2xl animate-fade-in">
            <div className="flex flex-col md:flex-row items-start md:items-center justify-between border-b border-slate-850 pb-6 gap-4">
              <div>
                <h2 className="text-xl font-extrabold tracking-tight text-white flex items-center gap-2.5">
                  <Cpu className="w-6 h-6 text-emerald-400" />
                  Unicore UM960 Python Tracker Script Exporter
                </h2>
                <p className="text-slate-400 text-sm mt-1">Configure serial connection attributes below and export a standalone executable python visualizer.</p>
              </div>
              
              <div className="flex flex-wrap items-center gap-3">
                <button 
                  id="btn-copy-python"
                  onClick={copyPythonScriptToClipboard}
                  className="px-4 py-2 bg-slate-950 hover:bg-slate-800 text-xs text-white rounded border border-slate-855 font-bold transition-all flex items-center gap-1.5"
                >
                  {isCopied ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
                  {isCopied ? 'Copied Code!' : 'Copy Script'}
                </button>

                <button 
                  id="btn-download-python"
                  onClick={downloadPythonScript}
                  className="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-xs text-slate-950 font-black rounded transition-all flex items-center gap-1.5 shadow-[0_4px_15px_rgba(16,185,129,0.3)]"
                >
                  <Download className="w-4 h-4" />
                  Download python script (.py)
                </button>
              </div>
            </div>

            {/* Embedded custom parameters */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 my-6 p-4 bg-slate-950 rounded-lg border border-slate-850">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-400 font-bold block mb-1">Target Python Port</label>
                <input 
                  type="text" 
                  value={pythonComPort} 
                  onChange={(e) => setPythonComPort(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded p-2 text-xs font-mono text-white focus:outline-none focus:border-emerald-500"
                  placeholder="/dev/ttyUSB0 or COM3"
                />
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {['/dev/ttyUSB0', '/dev/ttyACM0', 'COM3', '/dev/tty.usbserial'].map((p) => (
                    <button
                      key={p}
                      onClick={() => setPythonComPort(p)}
                      type="button"
                      className={`px-2 py-0.5 rounded text-[10px] font-mono transition-colors ${
                        pythonComPort === p 
                          ? 'bg-emerald-500 text-slate-950 font-semibold' 
                          : 'bg-slate-900 text-slate-400 hover:text-white hover:bg-slate-800 border border-slate-800'
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-400 font-bold block mb-1">Target Baudrate</label>
                <select 
                  value={pythonBaud} 
                  onChange={(e) => setPythonBaud(parseInt(e.target.value))}
                  className="w-full bg-slate-900 border border-slate-800 rounded p-2 text-xs font-mono text-white focus:outline-none"
                >
                  <option value="9600">9600 Baud</option>
                  <option value="115200">115200 Baud</option>
                  <option value="230400">230400 Baud</option>
                  <option value="460800">460800 Baud</option>
                </select>
              </div>

              <div>
                <label className="text-[10px] uppercase tracking-wider text-teal-400 font-bold block mb-1">GIS Coordinate Grids Embedded</label>
                <div className="text-xs text-white pt-2.5 font-bold font-mono">
                  ✓ BGS2005 (EPSG:7803/7801) & GGRS87 (EPSG:2100)
                </div>
              </div>
            </div>

            {/* Embedded Script Code Viewer */}
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
                <span>PREVIEW: STANDALONE PYTHON GIS SOURCE CODE</span>
                <span className="font-mono bg-slate-950 text-slate-350 px-2 rounded-full border border-slate-850">unicore_um960_tracker.py</span>
              </div>
              
              <pre className="p-4 bg-slate-950 rounded-lg border border-slate-850 text-xs font-mono text-slate-300 overflow-x-auto overflow-y-auto max-h-[400px] leading-relaxed select-all">
                {PYTHON_SCRIPT_TEXT}
              </pre>
            </div>
            
            <div className="mt-6 p-4 bg-emerald-500/5 rounded-lg border border-emerald-500/10 text-xs text-slate-400 leading-relaxed font-normal">
              <span className="font-bold text-white uppercase block mb-1 text-emerald-400">How to execute details:</span>
              1. Open a system terminal/cmd. Ensure you have python installed.
              <br />
              2. Run <code className="bg-slate-950 px-1 font-bold text-emerald-300 rounded border border-slate-850">pip install pyserial pyproj matplotlib</code> to install the requisite serial driver, proj engine, and plot libraries.
              <br />
              3. Connect your Unicore UM960 module and run: <code className="bg-slate-950 px-1 font-bold text-emerald-300 rounded border border-slate-850">python unicore_um960_tracker.py --port {pythonComPort} --baud {pythonBaud}</code>
              <br />
              4. You can also run it in standalone simulator demo mode by specifying: <code className="bg-slate-950 px-1 font-bold text-emerald-300 rounded border border-slate-850">python unicore_um960_tracker.py --simulate</code>
            </div>

          </div>
        )}

        {/* MANUAL NMEA PASTER TAB */}
        {activeTab === 'manual' && (
          <div className="bg-slate-900 rounded-xl border border-slate-850 p-6 md:p-8 max-w-4xl mx-auto shadow-xl">
            <h2 className="text-lg font-bold text-white mb-2 uppercase tracking-wider flex items-center gap-2">
              <FileText className="w-5 h-5 text-emerald-450" />
              NMEA Decoded Logger Console
            </h2>
            <p className="text-slate-400 text-xs mb-5 leading-normal">
              For manual testing, check coordinates converting, field parsing, or verification. Paste raw NMEA sentences containing <code className="text-teal-400 font-mono font-bold">$xxGGA</code>, <code className="text-teal-450 font-mono font-bold">$xxRMC</code>, or <code className="text-teal-400 font-mono font-bold">$KSXT</code> directly below.
            </p>

            <div className="space-y-4">
              <textarea
                value={pastedNmea}
                onChange={(e) => setPastedNmea(e.target.value)}
                placeholder={"$GNGGA,061230.10,4241.79110,N,2319.45131,E,4,24,0.9,550.21,M,35.2,M,,\n$GNHDT,123.45,T\n$KSXT,20260623061230.10,123.45,1.23,0.45,42.696492,23.324151,550.21,0.12,0.34,4,3,28"}
                className="w-full bg-slate-950 text-slate-100 font-mono text-xs p-4 rounded-lg border border-slate-850 focus:outline-none focus:border-emerald-500 min-h-[180px] leading-relaxed"
              />

              <div className="flex items-center justify-between">
                <button
                  id="btn-parse-pasted"
                  onClick={handleParsePastedNmea}
                  className="px-5 py-2.5 bg-emerald-500 hover:bg-emerald-600 text-xs text-slate-950 font-black rounded transition-all flex items-center gap-1.5 shadow-md"
                >
                  <Cpu className="w-4 h-4" />
                  Parse Pasted Sentences
                </button>

                <button
                  onClick={() => { setPastedNmea(""); setManualParseStatus(null); }}
                  className="text-xs text-slate-400 hover:text-white"
                >
                  Clear Area
                </button>
              </div>

              {manualParseStatus && (
                <div className="p-3 bg-emerald-500/5 border border-emerald-500/10 rounded-lg text-xs font-mono text-emerald-400 flex items-start gap-2 animate-fade-in">
                  <Check className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span>{manualParseStatus}</span>
                </div>
              )}
            </div>

            {/* Coordinate reference characteristics for engineering users */}
            <div className="mt-8 pt-6 border-t border-slate-850">
              <h3 className="text-xs font-bold text-white uppercase tracking-wider mb-3 select-none">Projected Grids Engineering Specifications</h3>
              
              <div className="overflow-x-auto text-[11px] font-mono whitespace-nowrap bg-slate-950 rounded-lg border border-slate-850 scrollbar-thin">
                <table className="w-full text-left border-collapse table-auto">
                  <thead>
                    <tr className="bg-slate-900 border-b border-slate-850 text-slate-350">
                      <th className="p-3">Reference grid / EPSG</th>
                      <th className="p-3">Ellipsoid</th>
                      <th className="p-3">投影 Projection type</th>
                      <th className="p-3">False Easting / Northing</th>
                      <th className="p-3">Coverage area</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b border-slate-850/60 hover:bg-slate-900/40 text-slate-300">
                      <td className="p-3 font-semibold text-white">БГС2005 / UTM zone 34N (EPSG:7803)</td>
                      <td className="p-3">GRS-80 (ETRS89 aligned)</td>
                      <td className="p-3">Transverse Mercator (UTM Zone 34)</td>
                      <td className="p-3">500,000m / 0m</td>
                      <td className="p-3">Western Bulgaria (West of 24°E)</td>
                    </tr>
                    <tr className="border-b border-slate-850/60 hover:bg-slate-900/40 text-slate-300">
                      <td className="p-3 font-semibold text-white">БГС2005 / CCS2005 Lambert (EPSG:7801)</td>
                      <td className="p-3">GRS-80 (Lambert Cone)</td>
                      <td className="p-3">Lambert Conformal Conic (2 Standard Parallels)</td>
                      <td className="p-3">500,000m / 4,725,824.3591m</td>
                      <td className="p-3">National coverage (Republic of Bulgaria)</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40 text-slate-305">
                      <td className="p-3 font-semibold text-white">HGRS87 / Greek Grid (EPSG:2100)</td>
                      <td className="p-3">GRS-80 (EGSA87 Datum)</td>
                      <td className="p-3">Transverse Mercator (Central Central Meridian 24°E)</td>
                      <td className="p-3">500,000m / 0m</td>
                      <td className="p-3">Greece National Cadastre (inc. Northern Greece)</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        )}
      </main>

      {/* FOOTER CLOGS */}
      <footer className="border-t border-slate-900/60 bg-slate-950 p-6 flex flex-col md:flex-row items-center justify-between text-xs text-slate-500 gap-3">
        <div className="flex items-center gap-1.5">
          <span>Standard: Decodes Unicore proprietary KSXT orientation & coordinate strings</span>
        </div>
        <div className="font-mono tracking-tight text-[10px] text-slate-500">
          U_UM960_GIS_TRANSFORMER_V1.0.0
        </div>
      </footer>

    </div>
  );
}
