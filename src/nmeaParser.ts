/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import proj4 from 'proj4';
import { GNSSData, SatelliteInfo } from './types';

// Define Coordinate Systems
const EPSG_BGS2005_UTM34 = "+proj=utm +zone=34 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs"; // EPSG:7803
const EPSG_BGS2005_LAMBERT = "+proj=lcc +lat_1=42 +lat_2=43.3333333333333 +lat_0=42.6678756833333 +lon_0=25.5 +x_0=500000 +y_0=4725824.3591 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs"; // EPSG:7801 (BGS2005 / CCS2005)
const EPSG_GREEK_GRID = "+proj=tmerc +lat_0=0 +lon_0=24 +k=0.9996 +x_0=500000 +y_0=0 +ellps=GRS80 +towgs84=-199.87,74.79,246.62,0,0,0,0 +units=m +no_defs"; // EPSG:2100

export function convertWGS84ToGrids(lat: number, lon: number) {
  if (isNaN(lat) || isNaN(lon) || lat === 0 || lon === 0) {
    return {
      bgsEastUTM34: NaN,
      bgsNorthUTM34: NaN,
      bgsEastLambert: NaN,
      bgsNorthLambert: NaN,
      greekEasting: NaN,
      greekNorthing: NaN
    };
  }

  try {
    const [bgsEU34, bgsNU34] = proj4(proj4.WGS84, EPSG_BGS2005_UTM34, [lon, lat]);
    const [bgsELam, bgsNLam] = proj4(proj4.WGS84, EPSG_BGS2005_LAMBERT, [lon, lat]);
    const [greekE, greekN] = proj4(proj4.WGS84, EPSG_GREEK_GRID, [lon, lat]);

    return {
      bgsEastUTM34: bgsEU34,
      bgsNorthUTM34: bgsNU34,
      bgsEastLambert: bgsELam,
      bgsNorthLambert: bgsNLam,
      greekEasting: greekE,
      greekNorthing: greekN
    };
  } catch (error) {
    console.error("Coordinate projection failed:", error);
    return {
      bgsEastUTM34: NaN,
      bgsNorthUTM34: NaN,
      bgsEastLambert: NaN,
      bgsNorthLambert: NaN,
      greekEasting: NaN,
      greekNorthing: NaN
    };
  }
}

export class NMEAParser {
  private currentData: GNSSData;
  private satellites: Map<string, SatelliteInfo> = new Map();
  private gsvAccumulator: Map<string, Array<{ prn: number; elevation: number; azimuth: number; snr: number }>> = new Map();

  constructor() {
    this.currentData = this.getInitialState();
  }

  public getInitialState(): GNSSData {
    return {
      latitude: 0,
      longitude: 0,
      altitude: 0,
      speedKmh: 0,
      courseDeg: 0,
      headingDeg: NaN,
      pitchDeg: 0,
      rollDeg: 0,
      fixQuality: 0,
      numSatellites: 0,
      hdop: 1.0,
      vdop: 1.0,
      pdop: 1.0,
      timestamp: "",
      date: "",
      bgsEastUTM34: NaN,
      bgsNorthUTM34: NaN,
      bgsEastLambert: NaN,
      bgsNorthLambert: NaN,
      greekEasting: NaN,
      greekNorthing: NaN,
      gpsCount: 0,
      glonassCount: 0,
      galileoCount: 0,
      beidouCount: 0,
      qzssCount: 0,
      lastSentenceType: ""
    };
  }

  public getSatellitesList(): SatelliteInfo[] {
    return Array.from(this.satellites.values());
  }

  private parseDDMMToDecimal(nmeaStr: string, direction: string): number {
    if (!nmeaStr || !direction) return 0;
    try {
      const dotIndex = nmeaStr.indexOf('.');
      if (dotIndex === -1) return 0;

      const degSplit = dotIndex - 2;
      const degStr = nmeaStr.substring(0, degSplit);
      const minStr = nmeaStr.substring(degSplit);

      const degrees = parseFloat(degStr);
      const minutes = parseFloat(minStr);

      let decimal = degrees + (minutes / 60.0);
      if (direction === 'S' || direction === 'W') {
        decimal = -decimal;
      }
      return decimal;
    } catch (e) {
      return 0;
    }
  }

  private determineConstellation(prefix: string, prn: number): 'GPS' | 'GLONASS' | 'Galileo' | 'BeiDou' | 'QZSS' | 'Other' {
    const p = prefix.toUpperCase();
    if (p === 'GP' || p === 'GPS') return 'GPS';
    if (p === 'GL' || p === 'GB' || p === 'GN' && (prn >= 65 && prn <= 96)) return 'GLONASS'; // typical NMEA ranges
    if (p === 'GA' || p === 'GAL') return 'Galileo';
    if (p === 'BD' || p === 'GB' || p === 'GN' && (prn >= 140 && prn <= 180 || prn < 60)) {
      if (prn >= 193 && prn <= 200) return 'QZSS';
      return 'BeiDou';
    }
    if (p === 'QZ' || p === 'GQ' || (prn >= 193 && prn <= 200)) return 'QZSS';
    
    // Fallback based on typical PRN numbers:
    if (prn >= 1 && prn <= 32) return 'GPS';
    if (prn >= 65 && prn <= 96) return 'GLONASS';
    if (prn >= 101 && prn <= 136) return 'Galileo';
    if (prn >= 140 && prn <= 192) return 'BeiDou';
    if (prn >= 193 && prn <= 200) return 'QZSS';
    return 'Other';
  }

  public parseSentence(sentence: string): { data: GNSSData; updated: boolean } {
    const trimmed = sentence.trim();
    if (!trimmed.startsWith('$')) {
      return { data: this.currentData, updated: false };
    }

    let payload = trimmed;
    if (trimmed.includes('*')) {
      payload = trimmed.split('*')[0];
    }

    const parts = payload.split(',');
    const cmd = parts[0].toUpperCase();
    let updated = false;

    this.currentData.lastSentenceType = cmd;

    // Detect general NMEA Talker IDs:
    // GP = GPS, GL = GLONASS, GA = Galileo, BD/GB = BeiDou, GN = Mixed GNSS (most common for UM960)
    const talkerId = cmd.substring(1, 3);
    const sentenceId = cmd.substring(3);

    // 1. GGA: Fix position details
    if (sentenceId === 'GGA' || cmd === '$GNGGA' || cmd === '$GPGGA') {
      const utcTime = parts[1];
      const rawLat = parts[2];
      const latDir = parts[3];
      const rawLon = parts[4];
      const lonDir = parts[5];
      const quality = parseInt(parts[6] || '0', 10);
      const numSats = parseInt(parts[7] || '0', 10);
      const hdop = parseFloat(parts[8] || '1.0');
      const alt = parseFloat(parts[9] || '0');

      if (rawLat && rawLon) {
        this.currentData.timestamp = this.formatNmeaTime(utcTime);
        this.currentData.latitude = this.parseDDMMToDecimal(rawLat, latDir);
        this.currentData.longitude = this.parseDDMMToDecimal(rawLon, lonDir);
        this.currentData.altitude = alt;
        this.currentData.fixQuality = quality;
        this.currentData.numSatellites = numSats;
        this.currentData.hdop = hdop;

        // Perform transformations
        const grids = convertWGS84ToGrids(this.currentData.latitude, this.currentData.longitude);
        this.currentData.bgsEastUTM34 = grids.bgsEastUTM34;
        this.currentData.bgsNorthUTM34 = grids.bgsNorthUTM34;
        this.currentData.bgsEastLambert = grids.bgsEastLambert;
        this.currentData.bgsNorthLambert = grids.bgsNorthLambert;
        this.currentData.greekEasting = grids.greekEasting;
        this.currentData.greekNorthing = grids.greekNorthing;
        
        updated = true;
      }
    }

    // 2. RMC: Precision positioning backup + Speed over ground
    else if (sentenceId === 'RMC' || cmd === '$GNRMC' || cmd === '$GPRMC') {
      const utcTime = parts[1];
      const status = parts[2];
      const rawLat = parts[3];
      const latDir = parts[4];
      const rawLon = parts[5];
      const lonDir = parts[6];
      const speedKnots = parseFloat(parts[7] || '0');
      const course = parseFloat(parts[8] || '0');
      const dateStr = parts[9];

      if (status === 'A' && rawLat && rawLon) {
        this.currentData.timestamp = this.formatNmeaTime(utcTime);
        this.currentData.latitude = this.parseDDMMToDecimal(rawLat, latDir);
        this.currentData.longitude = this.parseDDMMToDecimal(rawLon, lonDir);
        this.currentData.speedKmh = speedKnots * 1.852;
        this.currentData.courseDeg = course;
        this.currentData.date = this.formatNmeaDate(dateStr);

        // Perform transformations
        const grids = convertWGS84ToGrids(this.currentData.latitude, this.currentData.longitude);
        this.currentData.bgsEastUTM34 = grids.bgsEastUTM34;
        this.currentData.bgsNorthUTM34 = grids.bgsNorthUTM34;
        this.currentData.bgsEastLambert = grids.bgsEastLambert;
        this.currentData.bgsNorthLambert = grids.bgsNorthLambert;
        this.currentData.greekEasting = grids.greekEasting;
        this.currentData.greekNorthing = grids.greekNorthing;

        updated = true;
      }
    }

    // 3. HDT: Dual antenna Standard Compass Heading (True Course)
    else if (sentenceId === 'HDT' || cmd === '$GNHDT' || cmd === '$GPHDT') {
      const heading = parseFloat(parts[1] || 'NaN');
      if (!isNaN(heading)) {
        this.currentData.headingDeg = heading;
        updated = true;
      }
    }

    // 4. VTG: Course over Ground and Ground Speed (backup)
    else if (sentenceId === 'VTG' || cmd === '$GNVTG' || cmd === '$GPVTG') {
      const cogt = parseFloat(parts[1] || 'NaN');
      const speedKmh = parseFloat(parts[7] || 'NaN');

      if (!isNaN(cogt)) this.currentData.courseDeg = cogt;
      if (!isNaN(speedKmh)) {
        this.currentData.speedKmh = speedKmh;
        updated = true;
      }
    }

    // 5. GSA: Active dilution of precision and satellite listings
    else if (sentenceId === 'GSA' || cmd === '$GNGSA' || cmd === '$GPGSA') {
      const pdop = parseFloat(parts[15] || '1.0');
      const hdop = parseFloat(parts[16] || '1.0');
      const vdop = parseFloat(parts[17] || '1.0');

      this.currentData.pdop = pdop;
      this.currentData.hdop = hdop;
      this.currentData.vdop = vdop;

      // Unicore outputs active satellites here. Mark PRNs matching as 'active' inside our map.
      // Index 3 to 14 lists active satellite PRN slots
      for (let i = 3; i <= 14; i++) {
        const prnString = parts[i];
        if (prnString) {
          const prn = parseInt(prnString, 10);
          if (!isNaN(prn) && prn > 0) {
            const satKey = `${talkerId}_${prn}`;
            const existing = this.satellites.get(satKey);
            if (existing) {
              existing.active = true;
              this.satellites.set(satKey, existing);
            } else {
              this.satellites.set(satKey, {
                prn,
                elevation: 45, // default guess if not received in GSV yet
                azimuth: (prn * 17) % 360,
                snr: 35,
                constellation: this.determineConstellation(talkerId, prn),
                active: true
              });
            }
          }
        }
      }
      updated = true;
    }

    // 6. GSV: Satellites in View
    // Standard format: $GPGSV,total_sentences,sentence_num,total_sats_in_view,prn,elevation,azimuth,snr,...
    else if (sentenceId === 'GSV' || cmd.endsWith('GSV')) {
      const totalSentences = parseInt(parts[1] || '1', 10);
      const sentenceNum = parseInt(parts[2] || '1', 10);
      const totalSats = parseInt(parts[3] || '0', 10);

      const talker = talkerId;
      if (sentenceNum === 1) {
        this.gsvAccumulator.set(talker, []);
      }

      const acc = this.gsvAccumulator.get(talker) || [];
      
      // Parse blocks of 4 elements (PRN, Elev, Azim, SNR) starting at index 4
      for (let i = 4; i < parts.length - 3; i += 4) {
        const prnVal = parseInt(parts[i], 10);
        const elevVal = parseFloat(parts[i + 1] || '0');
        const azimVal = parseFloat(parts[i + 2] || '0');
        const snrVal = parseFloat(parts[i + 3] || '0');

        if (!isNaN(prnVal) && prnVal > 0) {
          acc.push({
            prn: prnVal,
            elevation: elevVal,
            azimuth: azimVal,
            snr: snrVal || 0
          });
        }
      }

      this.gsvAccumulator.set(talker, acc);

      if (sentenceNum === totalSentences) {
        // Complete sweep for this constellation - flush to satellites list
        const processedSats = this.gsvAccumulator.get(talker) || [];
        processedSats.forEach(sat => {
          const satKey = `${talker}_${sat.prn}`;
          const constellation = this.determineConstellation(talker, sat.prn);
          this.satellites.set(satKey, {
            prn: sat.prn,
            elevation: sat.elevation,
            azimuth: sat.azimuth,
            snr: sat.snr,
            constellation,
            active: true
          });
        });

        // Recalculate separate constellation metrics
        this.recalculateConstellationCounts();
        updated = true;
      }
    }

    // 7. KSXT: Unicore Proprietary Positioning Sentence
    else if (cmd === '$KSXT') {
      try {
        const utcTime = parts[1];
        const heading = parseFloat(parts[2] || 'NaN');
        const pitch = parseFloat(parts[3] || '0');
        const roll = parseFloat(parts[4] || '0');
        const lat = parseFloat(parts[5] || 'NaN');
        const lon = parseFloat(parts[6] || 'NaN');
        const alt = parseFloat(parts[7] || '0');
        const posStatus = parseInt(parts[12] || '0', 10);
        const satCount = parseInt(parts[13] || '0', 10);

        if (!isNaN(lat) && !isNaN(lon)) {
          this.currentData.timestamp = this.formatNmeaTime(utcTime);
          this.currentData.latitude = lat;
          this.currentData.longitude = lon;
          this.currentData.altitude = alt;
          this.currentData.headingDeg = isNaN(heading) ? this.currentData.headingDeg : heading;
          this.currentData.pitchDeg = pitch;
          this.currentData.rollDeg = roll;
          this.currentData.numSatellites = satCount;

          // Convert Unicore status code to GGA Fix status
          // Unicore 4/62 = Fixed RTK. 5/61 = Float RTK. 1 = Standard SPS single.
          if (posStatus === 4 || posStatus === 62) {
            this.currentData.fixQuality = 4; // RTK Fixed
          } else if (posStatus === 5 || posStatus === 61) {
            this.currentData.fixQuality = 5; // RTK Float
          } else if (posStatus === 2) {
            this.currentData.fixQuality = 2; // DGPS
          } else {
            this.currentData.fixQuality = 1; // Single
          }

          // Perform transformations
          const grids = convertWGS84ToGrids(this.currentData.latitude, this.currentData.longitude);
          this.currentData.bgsEastUTM34 = grids.bgsEastUTM34;
          this.currentData.bgsNorthUTM34 = grids.bgsNorthUTM34;
          this.currentData.bgsEastLambert = grids.bgsEastLambert;
          this.currentData.bgsNorthLambert = grids.bgsNorthLambert;
          this.currentData.greekEasting = grids.greekEasting;
          this.currentData.greekNorthing = grids.greekNorthing;

          updated = true;
        }
      } catch (err) {
        console.error("Failed to parse proprietary Unicore KSXT line:", err);
      }
    }

    return { data: this.currentData, updated };
  }

  private recalculateConstellationCounts() {
    let gps = 0, glonass = 0, galileo = 0, beidou = 0, qzss = 0;
    this.satellites.forEach(sat => {
      if (sat.active) {
        if (sat.constellation === 'GPS') gps++;
        else if (sat.constellation === 'GLONASS') glonass++;
        else if (sat.constellation === 'Galileo') galileo++;
        else if (sat.constellation === 'BeiDou') beidou++;
        else if (sat.constellation === 'QZSS') qzss++;
      }
    });

    this.currentData.gpsCount = gps;
    this.currentData.glonassCount = glonass;
    this.currentData.galileoCount = galileo;
    this.currentData.beidouCount = beidou;
    this.currentData.qzssCount = qzss;
  }

  private formatNmeaTime(timeStr: string): string {
    if (!timeStr) return "";
    try {
      const hh = timeStr.substring(0, 2);
      const mm = timeStr.substring(2, 4);
      const ss = timeStr.substring(4, 6);
      return `${hh}:${mm}:${ss} UTC`;
    } catch (e) {
      return timeStr;
    }
  }

  private formatNmeaDate(dateStr: string): string {
    if (!dateStr) return "";
    try {
      const dd = dateStr.substring(0, 2);
      const mm = dateStr.substring(2, 4);
      const yy = dateStr.substring(4, 6);
      return `20${yy}-${mm}-${dd}`;
    } catch (e) {
      return dateStr;
    }
  }
}
