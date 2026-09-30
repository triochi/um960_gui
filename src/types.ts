/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

export interface SatelliteInfo {
  prn: number;
  elevation: number;   // 0 - 90 degrees
  azimuth: number;     // 0 - 360 degrees
  snr: number;         // 0 - 99 dB-Hz (C/N0)
  constellation: 'GPS' | 'GLONASS' | 'Galileo' | 'BeiDou' | 'QZSS' | 'Other';
  active: boolean;
}

export interface GNSSData {
  latitude: number;
  longitude: number;
  altitude: number;
  speedKmh: number;
  courseDeg: number;
  headingDeg: number; // dual antenna standard True Heading, common on UM960
  pitchDeg: number;   // pitch from proprietary $KSXT sentence
  rollDeg: number;    // roll from proprietary $KSXT sentence
  fixQuality: number; // 0=No Fix, 1=Single, 2=DGPS, 4=RTK Fixed, 5=RTK Float
  numSatellites: number;
  hdop: number;
  vdop: number;
  pdop: number;
  timestamp: string;
  date: string;
  
  // Grids
  bgsEastUTM34: number; // EPSG:7803 (BGS2005 / UTM zone 34N for Western Bulgaria)
  bgsNorthUTM34: number;
  bgsEastLambert: number; // EPSG:7801 (BGS2005 / CCS2005 Lambert National Bulgaria)
  bgsNorthLambert: number;
  greekEasting: number;   // EPSG:2100 (GGRS87 / Greek Grid for Greece Cadastre)
  greekNorthing: number;
  
  // Constellation counts
  gpsCount: number;
  glonassCount: number;
  galileoCount: number;
  beidouCount: number;
  qzssCount: number;

  lastSentenceType: string;
}

export interface TrackPoint {
  latitude: number;
  longitude: number;
  altitude: number;
  bgsEastUTM34: number;
  bgsNorthUTM34: number;
  bgsEastLambert: number;
  bgsNorthLambert: number;
  greekEasting: number;
  greekNorthing: number;
  fixQuality: number;
  timestamp: string;
  heading: number;
}

export interface SerialConfig {
  baudRate: number;
  dataBits: number;
  stopBits: number;
  parity: 'none' | 'even' | 'odd';
  flowControl: 'none' | 'hardware';
  portPath?: string;
}
