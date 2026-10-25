# -*- coding: utf-8 -*-
"""
ATC DECK - asztali repulo radar + LHBP ATC radio widget (Windows)
==========================================================
Az ESP32 RadarApp asztali, "felturbozott" valtozata.

Nezetek:
  1. Zold radar      - forgo pasztazo radar, zoomolhato, valaszthato repuloter-kozeppont
  2. ATC radio       - LHBP Approach / Tower / Ground (miniaudio lejatszo)

Inditas:   pythonw atc_deck.py          (konzol nelkul)
Demo mod:  python  atc_deck.py --demo   (internet nelkul, kitalalt forgalommal)

Gyorsbillentyuk:
  P / F2   vezerlopult ki/be       1-2   nezetvaltas
  Space    szunet                  + / - zoom (radar)
  Esc      vezerlopult bezarasa    H     elrejtes a talcara
  C        kompakt nezet ki/be
"""

import os
import sys
import math
import time
import json
import random
import argparse
import threading
import urllib.request
import urllib.error
from array import array
from dataclasses import dataclass, field

from PySide6.QtCore import (Qt, QTimer, QUrl, QPointF, QRectF, QSettings, Signal,
                            QObject, QPropertyAnimation, QEasingCurve, QPoint, QSize, QEvent)
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QFont, QFontMetrics,
                           QConicalGradient, QRadialGradient, QLinearGradient,
                           QPainterPath, QPixmap, QIcon, QPolygonF, QAction, QRegion)
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QLabel, QPushButton,
                               QComboBox, QSlider, QCheckBox, QHBoxLayout, QVBoxLayout,
                               QButtonGroup, QSystemTrayIcon, QMenu, QStackedWidget,
                               QSizeGrip)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

try:
    import miniaudio
    HAS_MINIAUDIO = True
except Exception:  # pragma: no cover
    HAS_MINIAUDIO = False

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAS_QTAUDIO = True
except Exception:  # pragma: no cover
    HAS_QTAUDIO = False


# ============================================================
# SZINEK / BETUK
# ============================================================
C_BG       = QColor("#060a08")
C_SURFACE  = QColor("#0c1310")
C_SURFACE2 = QColor("#111b16")
C_BORDER   = QColor("#1d2b24")
C_GREEN    = QColor("#3dff8a")
C_GREEN_D  = QColor("#1c6b40")
C_TEXT     = QColor("#d4ecde")
C_MUTED    = QColor("#6f8a7b")
C_AMBER    = QColor("#ffb347")
C_RED      = QColor("#ff5a5a")
C_CYAN     = QColor("#5fd4ff")
C_MAGENTA  = QColor("#e58bff")
C_ORANGE   = QColor("#ff8c42")
C_BLUE     = QColor("#6f9bff")

FONT_UI   = "Segoe UI"
FONT_MONO = "Consolas"


def font(size, bold=False, mono=False):
    f = QFont(FONT_MONO if mono else FONT_UI)
    f.setPixelSize(int(size))
    f.setBold(bold)
    if mono:
        f.setStyleHint(QFont.Monospace)
    return f


def with_alpha(c, a):
    c2 = QColor(c)
    c2.setAlphaF(max(0.0, min(1.0, a)))
    return c2


def fmt_int(n):
    return f"{int(n):,}".replace(",", " ")


# ============================================================
# REPULOTEREK + ATC CSATORNAK
# ============================================================
# kifutok: (irany fok, oldaliranyu eltolas km, hosszanti eltolas km, hossz km) - csak megjeleniteshez, kozelitoleg
AIRPORTS = [
    {"icao": "LHBP", "code": "BUD", "name": "Budapest Liszt Ferenc", "lat": 47.4369, "lon": 19.2556,
     "rwy": [(133, -0.9, -1.4, 3.0), (133, 0.9, 0.9, 3.7)]},
    {"icao": "LHUD", "code": "LHUD", "name": "Szeged", "lat": 46.2468, "lon": 20.0911,
     "rwy": [(160, 0, 0, 1.2)]},
    {"icao": "LHDC", "code": "DEB", "name": "Debrecen", "lat": 47.4889, "lon": 21.6153,
     "rwy": [(50, 0, 0, 2.5)]},
    {"icao": "LHPR", "code": "QGY", "name": "Győr-Pér", "lat": 47.6244, "lon": 17.8136,
     "rwy": [(130, 0, 0, 2.0)]},
    {"icao": "LHSM", "code": "SOB", "name": "Hévíz–Balaton", "lat": 46.6864, "lon": 17.1589,
     "rwy": [(160, 0, 0, 2.5)]},
    {"icao": "LHPP", "code": "PEV", "name": "Pécs-Pogány", "lat": 45.9909, "lon": 18.2410,
     "rwy": [(160, 0, 0, 2.5)]},
    {"icao": "LOWW", "code": "VIE", "name": "Bécs", "lat": 48.1103, "lon": 16.5697,
     "rwy": [(115, 0, -1.5, 3.5), (165, 1.6, 1.0, 3.6)]},
    {"icao": "LZIB", "code": "BTS", "name": "Pozsony", "lat": 48.1702, "lon": 17.2127,
     "rwy": [(40, 0, 0, 2.9), (130, 1.0, 0, 3.2)]},
]

# ugyanazok a streamek, mint az ESP-n
ATC_CHANNELS = [
    {"name": "Approach", "short": "APP", "freq": "122.980", "url": "https://s1-bos.liveatc.net/lhbp_app_122980"},
    {"name": "Tower",    "short": "TWR", "freq": "118.715", "url": "https://s1-bos.liveatc.net/lhbp_twr_118715"},
    {"name": "Ground",   "short": "GND", "freq": "121.905", "url": "https://s1-bos.liveatc.net/lhbp_gnd"},
]

VIEW_NAMES = ["Radar", "Rádió"]


# ============================================================
# ESP LOGIKA ATIRVA (checkSpecialCallsign, checkAirline, getCompass)
# ============================================================
SPECIAL = {}
for cs in ("R906", "R907", "R908", "RENDOR01", "RENDOR1"):
    SPECIAL[cs] = 1
for cs in ("MEDIC01", "MEDIC02", "MEDIC1", "MENTO01", "HEMS01", "HEMS1"):
    SPECIAL[cs] = 2
for cs in ("SHADOW01", "SHADOW1", "SHADOW02", "HUNAF01", "HUNAF1"):
    SPECIAL[cs] = 3

MIL_STARTS = {"HUN": "HU-Military", "PUMA": "HU-Military", "AIRPOL": "HU-Rendőrség",
              "HAWK": "HU-Military", "FALCON": "HU-Military"}
MIL_PREFIX = {
    "HUA": "HU-Military", "HNA": "HU-Military", "BAF": "BE-Military", "GAF": "DE-Military",
    "IAM": "IT-Military", "AME": "ES-Military", "RFR": "UK-Military", "NAF": "NO-Military",
    "DAF": "DK-Military", "SVF": "SE-Military", "FAF": "FI-Military", "FAM": "FR-Military",
    "NED": "NL-Military", "PAF": "PL-Military", "CAF": "CA-Military", "RFF": "RU-Military",
    "CHF": "CN-Military", "UAF": "US-Military", "CNV": "US-Military", "RCH": "US-Military",
    "SAM": "US-Military", "MMF": "NATO - MIL", "IAF": "IL-Military", "TUR": "TR-Military",
    "GRC": "GR-Military", "CSH": "CZ-Military", "SVK": "SK-Military", "ROU": "RO-Military",
    "BUL": "BG-Military", "CFC": "CA-Military",
}
AIRLINES = {
    "WZZ": "Wizz Air", "WUK": "Wizz Air UK", "WMT": "Wizz Air Malta", "RYR": "Ryanair",
    "TVS": "Smartwings", "EZY": "easyJet", "EJU": "easyJet Europe", "BAW": "British Airways",
    "SHT": "BA Shuttle", "DLH": "Lufthansa", "GEC": "Lufthansa Cargo", "CLH": "Lufthansa CityLine",
    "AUA": "Austrian", "SWR": "Swiss", "AFR": "Air France", "FCM": "Air France Hop", "KLM": "KLM",
    "IBE": "Iberia", "VLG": "Vueling", "IBS": "Iberia Express", "AZA": "ITA Airways",
    "AZI": "Aeroitalia", "NOS": "Neos", "LOT": "LOT", "CSA": "Czech Airlines", "ROT": "TAROM",
    "BTI": "airBaltic", "BMS": "Belavia", "SAS": "SAS", "NAX": "Norwegian", "IBK": "Norwegian Intl",
    "FIN": "Finnair", "BEL": "Brussels Airlines", "TRA": "Transavia", "EWG": "Eurowings",
    "EWE": "Eurowings Europe", "CFG": "Condor", "TUI": "TUI Belgium", "TUC": "TUI fly",
    "TAR": "Tunisair", "FHY": "Freebird", "PGT": "Pegasus", "THY": "Turkish Airlines",
    "AHY": "Azerbaijan Airlines", "UAR": "Ukraine Airlines", "AFL": "Aeroflot", "SDM": "Rossiya",
    "UTA": "UTair", "UAE": "Emirates", "ETD": "Etihad", "QTR": "Qatar Airways", "GFA": "Gulf Air",
    "FDB": "flydubai", "ABY": "Air Arabia", "MEA": "Middle East Airlines", "IAW": "Iraqi Airways",
    "ELY": "El Al", "ISR": "Israir", "IRA": "Iran Air", "THA": "Thai", "BAV": "Bangkok Airways",
    "SIA": "Singapore Airlines", "CPA": "Cathay Pacific", "CCA": "Air China",
    "CSN": "China Southern", "CES": "China Eastern", "CAL": "China Airlines", "KAL": "Korean Air",
    "AAR": "Asiana", "MAS": "Malaysia Airlines", "GIA": "Garuda", "PAL": "Philippine Airlines",
    "PIA": "PIA", "AIC": "Air India", "IGO": "IndiGo", "AAL": "American Airlines",
    "ETH": "Ethiopian", "KQA": "Kenya Airways", "MSR": "EgyptAir", "RAM": "Royal Air Maroc",
    "SAA": "South African", "QFA": "Qantas", "CLX": "Cargolux", "TUA": "Turkmenistan",
    "TWI": "TailWind", "CAO": "Air China Cargo", "TAY": "ASL Belgium", "AZG": "Silk Way West",
    "SXS": "SunExpress", "GTI": "Atlas Air", "MGH": "MNG/Mavi Gök", "KZR": "Air Astana",
    "VKG": "Sunclass Airlines", "ADB": "Antonov An-124", "SMX": "Smartwings (SK)",
}

REG_COUNTRY = {
    "HA-": "Magyarország", "D-": "Németország", "OE-": "Ausztria", "OK-": "Csehország",
    "OM-": "Szlovákia", "SP-": "Lengyelország", "YR-": "Románia", "9H-": "Málta",
    "G-": "Egyesült Királyság", "EI-": "Írország", "F-": "Franciaország", "I-": "Olaszország",
    "EC-": "Spanyolország", "PH-": "Hollandia", "OO-": "Belgium", "HB-": "Svájc",
    "LX-": "Luxemburg", "SE-": "Svédország", "LN-": "Norvégia", "OY-": "Dánia",
    "OH-": "Finnország", "TC-": "Törökország", "SX-": "Görögország", "LZ-": "Bulgária",
    "YU-": "Szerbia", "9A-": "Horvátország", "S5-": "Szlovénia", "UR-": "Ukrajna",
    "ES-": "Észtország", "YL-": "Lettország", "LY-": "Litvánia", "4X-": "Izrael",
    "A6-": "Egyesült Arab Emírségek", "A7-": "Katar", "C-": "Kanada", "B-": "Kína",
    "CS-": "Portugália", "ER-": "Moldova", "EW-": "Fehéroroszország", "RA-": "Oroszország",
    "4K-": "Azerbajdzsán", "UP-": "Kazahsztán", "EP-": "Irán", "SU-": "Egyiptom",
    "CN-": "Marokkó", "ZS-": "Dél-Afrika", "VH-": "Ausztrália", "VT-": "India",
    "9V-": "Szingapúr", "HS-": "Thaiföld", "A9C-": "Bahrein", "T7-": "San Marino",
    "2-": "Guernsey", "M-": "Man-sziget", "VP-B": "Bermuda", "VQ-B": "Bermuda",
    "Z3-": "Észak-Macedónia", "E7-": "Bosznia-Hercegovina", "ZA-": "Albánia",
    "4O-": "Montenegró", "OD-": "Libanon", "JY-": "Jordánia", "HZ-": "Szaúd-Arábia",
    "9K-": "Kuvait", "A4O-": "Omán", "AP-": "Pakisztán", "JA": "Japán", "HL": "Dél-Korea",
    "N": "USA",
}


def check_special(callsign):
    cs = callsign.strip().upper().replace("-", "")
    return SPECIAL.get(cs, 0)


def check_airline(callsign):
    """(legitarsasag nev, katonai?) - az ESP checkAirline() megfeleloje"""
    cs = callsign.strip().upper()
    if len(cs) < 3:
        return "", False
    for start, name in MIL_STARTS.items():
        if cs.startswith(start):
            return name, True
    p = cs[:3]
    if p in MIL_PREFIX:
        return MIL_PREFIX[p], True
    return AIRLINES.get(p, ""), False


def compass_hu(h):
    names = ["É", "ÉK", "K", "DK", "D", "DNy", "Ny", "ÉNy"]
    return names[int(((h % 360) + 22.5) // 45) % 8]


def reg_country(reg):
    if not reg:
        return ""
    reg = reg.upper()
    best = ""
    for pre in REG_COUNTRY:
        if reg.startswith(pre) and len(pre) > len(best):
            best = pre
    return REG_COUNTRY.get(best, "")


def fmt_alt(alt_ft):
    if alt_ft is None:
        return "—"
    if alt_ft >= 10000:
        return f"FL{alt_ft // 100:03d}"
    return f"{fmt_int(alt_ft)} ft"


def nice_step(raw):
    mag = 10 ** math.floor(math.log10(max(raw, 1e-6)))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


# ============================================================
# ADATMODELL
# ============================================================
@dataclass
class Plane:
    hex: str
    callsign: str = ""
    reg: str = ""
    type: str = ""
    lat: float = 0.0
    lon: float = 0.0
    alt_ft: int = None
    on_ground: bool = False
    gs_kt: float = 0.0
    track: float = 0.0
    vrate: float = 0.0          # ft/min
    squawk: str = ""
    db_mil: bool = False
    t_fix: float = 0.0          # time.monotonic() a pozicio pillanataban
    last_seen: float = 0.0
    trail: list = field(default_factory=list)   # [(lat, lon), ...]

    @property
    def label(self):
        return self.callsign or self.reg or self.hex.upper()

    @property
    def speed_kmh(self):
        return int(self.gs_kt * 1.852)

    def pos_at(self, t):
        """holtszamitas: ahol a gep MOST lehet a sebesseg es irany alapjan"""
        dt = max(0.0, min(t - self.t_fix, 90.0))
        if self.on_ground or self.gs_kt <= 0:
            return self.lat, self.lon
        d_km = self.gs_kt * 1.852 * dt / 3600.0
        tr = math.radians(self.track)
        dlat = d_km * math.cos(tr) / 110.574
        dlon = d_km * math.sin(tr) / (111.320 * math.cos(math.radians(self.lat)))
        return self.lat + dlat, self.lon + dlon


def to_xy_km(lat, lon, lat0, lon0):
    x = (lon - lon0) * 111.320 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110.574
    return x, y


def classify(p, airport, area_km):
    """ESP logika: statusz szoveg + kategoria (special/mil/dep/arr/transit)"""
    spec = check_special(p.label)
    airline, mil = check_airline(p.label)
    mil = mil or p.db_mil
    x, y = to_xy_km(p.lat, p.lon, airport["lat"], airport["lon"])
    dist = math.hypot(x, y)
    alt = p.alt_ft or 0
    code = airport["code"]
    if spec == 1:
        return "Rendőrhelikopter", "special", "ORFK"
    if spec == 2:
        return "Mentőhelikopter", "special", "OMSZ"
    if spec == 3:
        return "Katonai helikopter", "special", airline
    if mil:
        return "Katonai bevetés", "mil", airline or "Katonai"
    if not p.on_ground and alt < 4000 and p.vrate > 100 and dist <= area_km:
        return f"Indul: {code} → ???", "dep", airline
    if not p.on_ground and alt < 15000 and p.vrate < -100 and dist <= area_km:
        return f"Érkezik: ??? → {code}", "arr", airline
    if p.on_ground:
        return "A földön", "ground", airline
    return "Átrepülő", "transit", airline


CAT_COLOR = {"special": C_RED, "mil": C_RED, "dep": C_CYAN, "arr": C_AMBER,
             "transit": C_GREEN, "ground": C_MUTED}


# ============================================================
# ADATFORRAS (adsb.lol, tartalek: airplanes.live)
# ============================================================
class DataStore(QObject):
    updated = Signal()
    status_changed = Signal()

    PROVIDERS = [
        ("adsb.lol", "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{r}"),
        ("airplanes.live", "https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{r}"),
    ]

    def __init__(self, demo=False, parent=None):
        super().__init__(parent)
        self.demo = demo
        self.nam = QNetworkAccessManager(self)
        self.planes = {}
        self.lat = AIRPORTS[0]["lat"]
        self.lon = AIRPORTS[0]["lon"]
        self.radius_nm = 40
        self.interval_s = 10
        self.last_fetch = time.monotonic()
        self.ok = None
        self.error = ""
        self.source = "demó" if demo else ""
        self.provider_idx = 0
        self.attempts = 0
        self.paused = False
        self._reply = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fetch)
        self.timer.start(self.interval_s * 1000)
        self._debounce = QTimer(self, singleShot=True, interval=700)
        self._debounce.timeout.connect(self.fetch)

    # --- beallitasok ---
    def set_center(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.planes.clear()
        self.updated.emit()
        self.fetch()

    def set_radius_km(self, km):
        nm = int(min(250, max(15, km / 1.852 * 1.15)))
        if abs(nm - self.radius_nm) >= 5:
            grew = nm > self.radius_nm
            self.radius_nm = nm
            if grew:
                self._debounce.start()

    def set_interval(self, s):
        self.interval_s = s
        self.timer.start(s * 1000)

    def seconds_left(self):
        return max(0, int(self.interval_s - (time.monotonic() - self.last_fetch)))

    # --- lekeres ---
    def fetch(self):
        if self.paused:
            return
        self.last_fetch = time.monotonic()
        self.timer.start(self.interval_s * 1000)
        if self.demo:
            self._demo_tick()
            return
        if self._reply is not None:
            return
        self.attempts = 0
        self._request()

    def _request(self):
        name, tpl = self.PROVIDERS[self.provider_idx]
        url = tpl.format(lat=self.lat, lon=self.lon, r=self.radius_nm)
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, "ATC-Deck/1.0")
        req.setTransferTimeout(12000)
        self._reply = self.nam.get(req)
        self._reply.finished.connect(lambda r=self._reply, n=name: self._on_reply(r, n))

    def _on_reply(self, reply, name):
        self._reply = None
        try:
            if reply.error() != QNetworkReply.NoError:
                raise RuntimeError(reply.errorString())
            data = json.loads(bytes(reply.readAll()).decode("utf-8", "replace"))
            ac = data.get("ac") or data.get("aircraft") or []
            self._merge(ac)
            self.ok, self.error, self.source = True, "", name
        except Exception as e:
            self.attempts += 1
            self.provider_idx = (self.provider_idx + 1) % len(self.PROVIDERS)
            if self.attempts < len(self.PROVIDERS):
                reply.deleteLater()
                self._request()          # azonnal probalja a tartalek forrast
                return
            self.ok, self.error = False, str(e)[:60]
        reply.deleteLater()
        self.status_changed.emit()
        self.updated.emit()

    def _merge(self, ac_list):
        now = time.monotonic()
        seen = set()
        for a in ac_list:
            if a.get("lat") is None or a.get("lon") is None:
                continue
            hx = a.get("hex", "").lower()
            if not hx:
                continue
            seen.add(hx)
            p = self.planes.get(hx) or Plane(hex=hx)
            alt = a.get("alt_baro")
            p.on_ground = (alt == "ground")
            if isinstance(alt, (int, float)):
                p.alt_ft = int(alt)
            elif isinstance(a.get("alt_geom"), (int, float)):
                p.alt_ft = int(a["alt_geom"])
            elif p.on_ground:
                p.alt_ft = 0
            new_lat, new_lon = float(a["lat"]), float(a["lon"])
            if p.t_fix and (abs(new_lat - p.lat) > 1e-4 or abs(new_lon - p.lon) > 1e-4):
                p.trail.append((p.lat, p.lon))
                del p.trail[:-24]
            p.lat, p.lon = new_lat, new_lon
            p.callsign = (a.get("flight") or p.callsign or "").strip()
            p.reg = a.get("r") or p.reg
            p.type = a.get("t") or p.type
            p.gs_kt = float(a.get("gs") or 0)
            p.track = float(a.get("track", a.get("true_heading", p.track)) or 0)
            vr = a.get("baro_rate", a.get("geom_rate"))
            p.vrate = float(vr) if isinstance(vr, (int, float)) else 0.0
            p.squawk = str(a.get("squawk") or "")
            p.db_mil = bool((a.get("dbFlags") or 0) & 1)
            p.t_fix = now - float(a.get("seen_pos") or 0)
            p.last_seen = now
            self.planes[hx] = p
        for hx in [h for h, p in self.planes.items() if h not in seen and now - p.last_seen > 60]:
            del self.planes[hx]

    # --- demo forgalom (internet nelkul) ---
    def _demo_tick(self):
        now = time.monotonic()
        if not self.planes:
            rnd = random.Random(7)
            calls = ["WZZ2254", "RYR82QK", "DLH1KC", "THY5UM", "AUA613", "LOT537", "QTR9VA",
                     "EZY45TW", "UAE1AT", "AFR1494", "KLM1975", "BAW868", "SWR1YW", "FIN1MG",
                     "WZZ7LN", "TVS3PY", "CSA74X", "PGT9FV", "MEDIC01", "HUAF123", "ROT231",
                     "LOT2TW", "WMT6512", "SXS2RW"]
            types = ["A21N", "B38M", "A20N", "A321", "E195", "B738", "A359", "A320", "B77W",
                     "A319", "B739", "BCS3", "E190", "A333"]
            regs = ["HA-LVK", "EI-IHE", "D-AINX", "TC-JTH", "OE-LWE", "SP-LWD", "A7-ALZ",
                    "G-UZHF", "A6-EGB", "F-HBNB", "PH-BXR", "G-EUUW", "HB-JBF", "OH-LVL"]
            for i, cs in enumerate(calls):
                ang = rnd.uniform(0, 360)
                d = rnd.uniform(5, 140)
                kind = "transit"
                if i in (0, 5, 16):
                    kind, d = "arr", rnd.uniform(12, 45)
                if i in (1, 9):
                    kind, d = "dep", rnd.uniform(3, 14)
                lat = self.lat + d * math.cos(math.radians(ang)) / 110.574
                lon = self.lon + d * math.sin(math.radians(ang)) / (111.32 * math.cos(math.radians(self.lat)))
                p = Plane(hex=f"4d{i:04x}", callsign=cs, reg=regs[i % len(regs)],
                          type=types[i % len(types)], lat=lat, lon=lon)
                if kind == "arr":
                    p.alt_ft, p.vrate, p.gs_kt = rnd.randint(3000, 11000), -rnd.randint(700, 1300), rnd.randint(170, 260)
                    p.track = (ang + 180) % 360
                elif kind == "dep":
                    p.alt_ft, p.vrate, p.gs_kt = rnd.randint(1200, 3800), rnd.randint(1500, 2600), rnd.randint(160, 230)
                    p.track = ang
                else:
                    p.alt_ft, p.vrate, p.gs_kt = rnd.choice([28000, 34000, 36000, 38000, 31000, 24000]), rnd.choice([0, 0, 0, 640, -512]), rnd.randint(380, 490)
                    p.track = rnd.uniform(0, 360)
                if cs == "MEDIC01":
                    p.alt_ft, p.gs_kt, p.vrate = 1500, 110, 0
                p.t_fix = now
                self.planes[p.hex] = p
        else:
            for p in self.planes.values():
                lat, lon = p.pos_at(now)
                p.trail.append((p.lat, p.lon))
                del p.trail[:-24]
                p.lat, p.lon = lat, lon
                if p.vrate:
                    p.alt_ft = max(0, int(p.alt_ft + p.vrate * (now - p.t_fix) / 60))
                p.t_fix = now
        self.ok, self.source = True, "demó"
        self.status_changed.emit()
        self.updated.emit()


# ============================================================
# ATC RADIO
#   Elsodleges: sajat letolto szal + miniaudio MP3 dekoder (bongeszo
#   User-Agenttel, mint egy rendes lejatszo). Tartalek: QtMultimedia.
# ============================================================
UA_VLC = "VLC/3.0.21 LibVLC/3.0.21"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def stream_candidates(url):
    """Csatlakozasi modok sorrendben. Ha egy nem megy (pl. a szerver bontja: WinError 10054),
    a kovetkezot probalja; a mukodot megjegyzi csatornankent."""
    mount = url.rstrip("/").rsplit("/", 1)[-1]
    host = url.split("//", 1)[-1].split("/", 1)[0]
    vlc = {"User-Agent": UA_VLC, "Accept": "*/*", "Icy-MetaData": "1"}
    web = {"User-Agent": UA_BROWSER, "Accept": "*/*", "Referer": "https://www.liveatc.net/",
           "Icy-MetaData": "1"}
    return [
        (url, vlc),                                                  # 1. ugyanaz, mint az ESP-n
        (f"http://{host}/{mount}", vlc),                             # 2. TLS nelkul
        (f"pls:https://www.liveatc.net/play/{mount}.pls", vlc),      # 3. aktualis szerver a .pls-bol
        (f"http://d.liveatc.net/{mount}", vlc),                      # 4. LiveATC elosztó
        (url, web),                                                  # 5. bongeszokent
    ]
AUDIO_RATE = 22050
PREBUFFER_BYTES = 6000


class _NetSource(miniaudio.StreamableSource if HAS_MINIAUDIO else object):
    """HTTP(S) stream letoltese hatterszalon, puffereles a dekoder szamara"""

    def __init__(self, url, headers):
        self.url = url
        self.headers = headers
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stopped = False
        self.eof = False
        self.error = ""
        self.total = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _resolve(self):
        """'pls:' elotag eseten a lejatszasi listabol olvassa ki a valodi stream cimet"""
        if not self.url.startswith("pls:"):
            return self.url
        req = urllib.request.Request(self.url[4:], headers=self.headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read(4096).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.lower().startswith("file") and "=" in line:
                return line.split("=", 1)[1].strip()
        raise RuntimeError("üres .pls lista")

    def _push(self, chunk):
        with self.lock:
            self.buf += chunk
            self.total += len(chunk)
            if len(self.buf) > 256 * 1024:          # ha a lejatszas lemaradt
                del self.buf[:len(self.buf) - 64 * 1024]

    def _read_exact(self, r, n):
        out = b""
        while len(out) < n and not self.stopped:
            part = r.read(n - len(out))
            if not part:
                break
            out += part
        return out

    def _run(self):
        try:
            real = self._resolve()
            req = urllib.request.Request(real, headers=self.headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                metaint = int(r.headers.get("icy-metaint") or 0)
                while not self.stopped:
                    chunk = self._read_exact(r, metaint) if metaint else r.read(4096)
                    if not chunk:
                        self.eof = True
                        break
                    self._push(chunk)
                    if metaint:                         # ICY metaadat kihagyasa
                        ln = self._read_exact(r, 1)
                        if ln:
                            self._read_exact(r, ln[0] * 16)
        except urllib.error.HTTPError as e:
            self.error = f"HTTP {e.code} {e.reason}"
        except Exception as e:
            self.error = str(e)[:80] or e.__class__.__name__

    def read(self, num_bytes):
        # amennyi van (legalabb 1 bajt) - a dekoder kezeli a rovid olvasast
        deadline = time.monotonic() + 8
        while True:
            with self.lock:
                if self.buf:
                    out = bytes(self.buf[:num_bytes])
                    del self.buf[:num_bytes]
                    return out
            if self.stopped or self.eof or self.error or time.monotonic() > deadline:
                return b""
            time.sleep(0.02)

    def close(self):
        self.stopped = True


class Radio(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        no_audio = bool(os.environ.get("ATCDECK_NO_AUDIO"))
        self.backend = "" if no_audio else ("miniaudio" if HAS_MINIAUDIO else ("qt" if HAS_QTAUDIO else ""))
        self.available = bool(self.backend)
        self.channel = 0
        self.volume = 60
        self.wanted = False
        self.live = False
        self.status = "Kikapcsolva" if self.available else "Nincs hangmodul – pip install miniaudio"
        self._gen = 0            # minden (ujra)csatlakozas uj azonosito -> a regi szal eldobja magat
        self._dev = None
        self._src = None
        self._fail = ""
        self._ended = False
        self._mode = {}          # csatorna -> utoljara mukodo csatlakozasi mod
        self._tries = 0
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self._connect)
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._check)
        if self.backend == "qt":
            self._init_qt()

    # --- kozos API ---
    def play(self, idx=None):
        if idx is not None:
            self.channel = idx % len(ATC_CHANNELS)
        self.wanted = True
        self._tries = 0
        self._connect()

    def stop(self):
        self.wanted = False
        self.live = False
        self._retry.stop()
        self._poll.stop()
        self._close_stream()
        self.status = "Kikapcsolva" if self.available else self.status
        self.changed.emit()

    def toggle(self):
        self.stop() if self.wanted else self.play()

    def set_volume(self, v):
        self.volume = max(0, min(100, int(v)))
        if self.backend == "qt":
            self.out.setVolume(self.volume / 100.0)
        self.changed.emit()

    def _connect(self):
        if not self.available or not self.wanted:
            self.changed.emit()
            return
        self.live = False
        cands = stream_candidates(ATC_CHANNELS[self.channel]["url"])
        mode = self._mode.get(self.channel, 0) % len(cands)
        self.status = f"Csatlakozás… ({mode + 1}/{len(cands)}. mód)"
        self.changed.emit()
        if self.backend == "qt":
            self.player.stop()
            self.player.setSource(QUrl(ATC_CHANNELS[self.channel]["url"]))
            self.player.play()
            return
        self._close_stream()
        self._gen += 1
        self._fail = ""
        self._ended = False
        url, headers = cands[mode]
        threading.Thread(target=self._worker, args=(self._gen, url, headers), daemon=True).start()
        self._poll.start(400)

    def _schedule_retry(self, why, ms=4000):
        self.live = False
        self.status = f"{why} – újra {max(1, ms // 1000)} mp múlva"
        self._close_stream()
        self._poll.stop()
        self._retry.start(ms)
        self.changed.emit()

    # --- miniaudio ag ---
    def _next_mode(self):
        """sikertelen csatlakozas utan a kovetkezo modra lep"""
        n = len(stream_candidates(ATC_CHANNELS[self.channel]["url"]))
        self._mode[self.channel] = (self._mode.get(self.channel, 0) + 1) % n
        self._tries += 1
        return self._tries < n                  # amig van ki nem probalt mod, gyorsan ujraprobal

    def _worker(self, gen, url, headers):
        src = _NetSource(url, headers)
        t0 = time.monotonic()
        while src.total < PREBUFFER_BYTES and not (src.error or src.eof):
            if gen != self._gen or time.monotonic() - t0 > 15:
                break
            time.sleep(0.05)
        if gen != self._gen:
            src.close()
            return
        if src.total < PREBUFFER_BYTES:
            src.close()
            self._fail = src.error or "Nem jön adat a streamből"
            return
        try:
            stream = miniaudio.stream_any(src, source_format=miniaudio.FileFormat.MP3,
                                          output_format=miniaudio.SampleFormat.SIGNED16,
                                          nchannels=1, sample_rate=AUDIO_RATE)
            dev = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16,
                                           nchannels=1, sample_rate=AUDIO_RATE,
                                           buffersize_msec=300, app_name="ATC Deck")
            g = self._volume_gen(stream, gen)
            next(g)
            dev.start(g)
        except Exception as e:
            src.close()
            self._fail = f"Lejátszó hiba: {str(e)[:60]}"
            return
        if gen != self._gen:
            dev.close()
            src.close()
            return
        self._dev, self._src = dev, src

    def _volume_gen(self, stream, gen):
        frames = yield b""
        while True:
            try:
                chunk = stream.send(frames)
            except StopIteration:
                if gen == self._gen:
                    self._ended = True
                chunk = array("h", bytes(2 * frames))           # csend, amig ujracsatlakozunk
            v = (self.volume / 100.0) ** 1.6
            if v < 0.999:
                chunk = array("h", [int(x * v) for x in chunk])
            frames = yield chunk

    def _check(self):
        """GUI szalon: a hatterszal allapotanak kovetese, hiba eseten ujracsatlakozas"""
        if not self.wanted:
            return
        if self._fail:
            quick = self._next_mode()
            self._schedule_retry(self._fail, 1000 if quick else 5000)
            if not quick:
                self._tries = 0
            return
        src = self._src
        if src is None:
            return
        drained = len(src.buf) == 0
        if self._ended or ((src.error or src.eof) and drained):
            self._schedule_retry(src.error or "Kapcsolat megszakadt", 3000)
            return
        if not self.live:
            self.live = True
            self._tries = 0
            self.status = "Élő adás"
            self.changed.emit()

    def _close_stream(self):
        self._gen += 1
        dev, src = self._dev, self._src
        self._dev = self._src = None
        if src:
            src.close()
        if dev:
            try:
                dev.close()
            except Exception:
                pass
        if self.backend == "qt":
            self.player.stop()

    # --- QtMultimedia tartalek ag ---
    def _init_qt(self):
        self.out = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.out)
        self.player.mediaStatusChanged.connect(self._qt_status)
        self.player.errorOccurred.connect(self._qt_error)
        self.out.setVolume(self.volume / 100.0)

    def _qt_status(self, st):
        if not self.wanted:
            return
        if st == QMediaPlayer.BufferedMedia:
            self.live = True
            self.status = "Élő adás"
            self.changed.emit()
        elif st in (QMediaPlayer.EndOfMedia, QMediaPlayer.InvalidMedia):
            self._schedule_retry("Kapcsolat megszakadt", 3000)

    def _qt_error(self, *_):
        if self.wanted:
            self._schedule_retry(f"Stream hiba: {self.player.errorString()[:50]}")


# ============================================================
# NEZETEK - kozos alap (ablakhuzas + kattintas)
# ============================================================
class ViewBase(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setMouseTracking(True)
        self._press = None
        self._dragging = False

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.position()
            self._dragging = False

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.LeftButton):
            if (e.position() - self._press).manhattanLength() > 5 and not self._dragging:
                self._dragging = True
                self.window().windowHandle().startSystemMove()
        else:
            self.hover(e.position())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None and not self._dragging:
            self.clicked(e.position())
        self._press = None
        self._dragging = False

    def hover(self, pos):
        pass

    def clicked(self, pos):
        pass

    def content_rect(self):
        """a vezerlopult altal NEM takart resz"""
        r = QRectF(self.rect())
        r.setBottom(r.bottom() - self.app.panel_cover())
        return r


# ============================================================
# 1. ZOLD RADAR
# ============================================================
class RadarView(ViewBase):
    SWEEP_PERIOD = 4.0

    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self.disp_range = app.cfg_range
        self.selected = None
        self.hovered = None
        self._screen_pts = {}

    def tick(self):
        target = self.app.cfg_range
        if abs(self.disp_range - target) > 0.01:
            self.disp_range += (target - self.disp_range) * 0.18
        self.update()

    def wheelEvent(self, e):
        f = 0.87 if e.angleDelta().y() > 0 else 1 / 0.87
        self.app.set_range(self.app.cfg_range * f)

    def hover(self, pos):
        h = self._pick(pos)
        if h != self.hovered:
            self.hovered = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)

    def clicked(self, pos):
        self.selected = self._pick(pos)

    def _pick(self, pos):
        best, bd = None, 16
        for hx, pt in self._screen_pts.items():
            d = math.hypot(pt.x() - pos.x(), pt.y() - pos.y())
            if d < bd:
                best, bd = hx, d
        return best

    # --- rajzolas ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        app = self.app
        now = app.now()
        ap = app.airport
        area = self.content_rect()
        p.fillRect(self.rect(), C_BG)

        cx, cy = area.center().x(), area.center().y()
        R = max(40.0, min(area.width(), area.height()) / 2 - (8 if app.compact else 18))
        rng = self.disp_range
        k = R / rng
        center = QPointF(cx, cy)

        # kepernyo hatter
        g = QRadialGradient(center, R)
        g.setColorAt(0, QColor("#0b2416"))
        g.setColorAt(0.75, QColor("#071710"))
        g.setColorAt(1, QColor("#040d08"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(center, R, R)

        clip = QPainterPath()
        clip.addEllipse(center, R, R)
        p.save()
        p.setClipPath(clip)

        self._draw_rings(p, center, R, rng)
        self._draw_runways(p, center, k, ap)
        sweep = (now % self.SWEEP_PERIOD) / self.SWEEP_PERIOD * 360.0
        self._draw_sweep(p, center, R, sweep)
        self._draw_planes(p, center, k, R, now, sweep, ap)
        p.restore()

        # perem
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(with_alpha(C_GREEN, 0.55), 1.6))
        p.drawEllipse(center, R, R)
        self._draw_ticks(p, center, R)
        self._draw_hud(p, area, rng)
        self._draw_selected_card(p, area, now, ap)
        p.end()

    def _draw_rings(self, p, c, R, rng):
        step = nice_step(rng / 4)
        p.setFont(font(10, mono=True))
        d = step
        while d < rng * 1.001:
            r = R * d / rng
            p.setPen(QPen(with_alpha(C_GREEN, 0.16), 1, Qt.DashLine if d < rng * 0.999 else Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            p.setPen(with_alpha(C_GREEN, 0.45))
            lbl = f"{d:g} km"
            p.drawText(QPointF(c.x() + r * 0.707 + 3, c.y() - r * 0.707 - 3), lbl)
            d += step
        p.setPen(QPen(with_alpha(C_GREEN, 0.10), 1))
        p.drawLine(QPointF(c.x() - R, c.y()), QPointF(c.x() + R, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - R), QPointF(c.x(), c.y() + R))

    def _draw_runways(self, p, c, k, ap):
        p.setPen(QPen(with_alpha(C_GREEN, 0.8), max(2.0, min(5.0, k * 0.06)), Qt.SolidLine, Qt.RoundCap))
        for hdg, perp, along, length in ap["rwy"]:
            h = math.radians(hdg)
            ux, uy = math.sin(h), math.cos(h)
            px, py = math.cos(h), -math.sin(h)
            mx = perp * px + along * ux
            my = perp * py + along * uy
            x1, y1 = mx - ux * length / 2, my - uy * length / 2
            x2, y2 = mx + ux * length / 2, my + uy * length / 2
            p.drawLine(QPointF(c.x() + x1 * k, c.y() - y1 * k), QPointF(c.x() + x2 * k, c.y() - y2 * k))
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(C_GREEN, 0.9))
        p.drawEllipse(c, 2.2, 2.2)

    def _draw_sweep(self, p, c, R, sweep):
        theta = 90.0 - sweep                     # Qt: ora jarasaval ellentetes, 0 = jobbra
        cg = QConicalGradient(c, theta)
        cg.setColorAt(0.0, with_alpha(C_GREEN, 0.30))
        cg.setColorAt(0.08, with_alpha(C_GREEN, 0.10))
        cg.setColorAt(0.22, with_alpha(C_GREEN, 0.0))
        cg.setColorAt(1.0, with_alpha(C_GREEN, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(cg)
        p.drawEllipse(c, R, R)
        a = math.radians(sweep)
        end = QPointF(c.x() + R * math.sin(a), c.y() - R * math.cos(a))
        lg = QLinearGradient(c, end)
        lg.setColorAt(0, with_alpha(C_GREEN, 0.2))
        lg.setColorAt(1, with_alpha(C_GREEN, 0.95))
        p.setPen(QPen(QBrush(lg), 2))
        p.drawLine(c, end)

    def _draw_ticks(self, p, c, R):
        for deg in range(0, 360, 5):
            a = math.radians(deg)
            l = 9 if deg % 30 == 0 else 4
            p.setPen(QPen(with_alpha(C_GREEN, 0.6 if deg % 30 == 0 else 0.3), 1))
            p.drawLine(QPointF(c.x() + R * math.sin(a), c.y() - R * math.cos(a)),
                       QPointF(c.x() + (R - l) * math.sin(a), c.y() - (R - l) * math.cos(a)))
        p.setFont(font(11, bold=True))
        p.setPen(with_alpha(C_GREEN, 0.85))
        for lbl, deg in (("É", 0), ("K", 90), ("D", 180), ("Ny", 270)):
            a = math.radians(deg)
            pt = QPointF(c.x() + (R - 20) * math.sin(a), c.y() - (R - 20) * math.cos(a))
            p.drawText(QRectF(pt.x() - 14, pt.y() - 9, 28, 18), Qt.AlignCenter, lbl)

    def _draw_planes(self, p, c, k, R, now, sweep, ap):
        app = self.app
        self._screen_pts = {}
        lab_font = font(10, mono=True)
        lab_font_b = font(10, bold=True, mono=True)
        blink = (now * 2) % 2 < 1
        for hx, pl in app.store.planes.items():
            if pl.on_ground and not app.cfg_ground:
                continue
            lat, lon = pl.pos_at(now)
            x, y = to_xy_km(lat, lon, ap["lat"], ap["lon"])
            if math.hypot(x, y) * k > R + 4:
                continue
            sp = QPointF(c.x() + x * k, c.y() - y * k)
            self._screen_pts[hx] = sp
            status, cat, _ = classify(pl, ap, app.cfg_area)
            col = QColor(CAT_COLOR[cat])
            emergency = pl.squawk in ("7700", "7600", "7500")
            if emergency:
                col = C_RED if blink else C_AMBER

            # foszfor-utofenyles: a pasztazo atfutasa utan felvillan
            brg = math.degrees(math.atan2(x, y)) % 360
            since = ((sweep - brg) % 360) / 360 * self.SWEEP_PERIOD
            glow = 0.45 + 0.55 * math.exp(-since / 1.2)
            sel = (hx == self.selected)
            if sel or hx == self.hovered:
                glow = 1.0

            # nyomvonal
            if app.cfg_trails and pl.trail:
                n = len(pl.trail)
                p.setPen(Qt.NoPen)
                for i, (tl, tn) in enumerate(pl.trail):
                    tx, ty = to_xy_km(tl, tn, ap["lat"], ap["lon"])
                    a = 0.08 + 0.35 * (i + 1) / n
                    p.setBrush(with_alpha(col, a * glow))
                    p.drawEllipse(QPointF(c.x() + tx * k, c.y() - ty * k), 1.4, 1.4)

            # 1 perces elorejelzo vonal
            if pl.gs_kt > 30 and not pl.on_ground:
                d_km = pl.gs_kt * 1.852 / 60
                tr = math.radians(pl.track)
                ep = QPointF(sp.x() + math.sin(tr) * d_km * k, sp.y() - math.cos(tr) * d_km * k)
                p.setPen(QPen(with_alpha(col, 0.55 * glow), 1))
                p.drawLine(sp, ep)

            # gep jel (nyil a haladasi irannyal)
            p.save()
            p.translate(sp)
            p.rotate(pl.track)
            if glow > 0.8:
                halo = QRadialGradient(QPointF(0, 0), 11)
                halo.setColorAt(0, with_alpha(col, 0.45 * (glow - 0.6)))
                halo.setColorAt(1, with_alpha(col, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(0, 0), 11, 11)
            poly = QPolygonF([QPointF(0, -6.5), QPointF(4.6, 5), QPointF(0, 2.6), QPointF(-4.6, 5)])
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(col, glow))
            p.drawPolygon(poly)
            p.restore()

            if sel:
                pulse = 11 + 3 * math.sin(now * 5)
                p.setPen(QPen(with_alpha(C_TEXT, 0.9), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(sp, pulse, pulse)

            # cimke
            if app.cfg_labels or sel or hx == self.hovered:
                la = max(0.55, glow)
                arrow = "↑" if pl.vrate > 300 else ("↓" if pl.vrate < -300 else " ")
                p.setFont(lab_font_b)
                p.setPen(with_alpha(col if cat != "transit" else C_TEXT, la))
                p.drawText(QPointF(sp.x() + 9, sp.y() - 4), pl.label)
                if app.compact and not sel:
                    continue
                p.setFont(lab_font)
                p.setPen(with_alpha(C_MUTED if not sel else C_TEXT, la))
                p.drawText(QPointF(sp.x() + 9, sp.y() + 8),
                           f"{fmt_alt(pl.alt_ft)}{arrow} {int(pl.gs_kt)}kt")

        if self.selected and self.selected not in app.store.planes:
            self.selected = None

    def _draw_hud(self, p, area, rng):
        app = self.app
        ap = app.airport
        if app.compact:
            self._draw_hud_compact(p, area, rng)
            return
        p.setFont(font(15, bold=True, mono=True))
        p.setPen(C_GREEN)
        p.drawText(QPointF(area.left() + 12, area.top() + 22), ap["icao"])
        p.setFont(font(11))
        p.setPen(C_MUTED)
        p.drawText(QPointF(area.left() + 12, area.top() + 38), ap["name"])
        n = sum(1 for pl in app.store.planes.values() if not pl.on_ground)
        p.setFont(font(11, mono=True))
        p.setPen(C_CYAN)
        p.drawText(QPointF(area.left() + 12, area.top() + 54), f"{n} gép a levegőben")

        txt = f"{rng:.0f} km" if rng >= 10 else f"{rng:.1f} km"
        p.setFont(font(12, bold=True, mono=True))
        fm = QFontMetrics(p.font())
        w = fm.horizontalAdvance(txt) + 18
        r = QRectF(area.right() - w - 10, area.bottom() - 30, w, 22)
        p.setPen(QPen(with_alpha(C_GREEN, 0.4), 1))
        p.setBrush(with_alpha(C_SURFACE, 0.85))
        p.drawRoundedRect(r, 11, 11)
        p.setPen(C_GREEN)
        p.drawText(r, Qt.AlignCenter, txt)

        # jelmagyarazat
        p.setFont(font(10))
        x = area.right() - 10
        y = area.top() + 18
        for name, col in (("átrepülő", C_GREEN), ("érkező", C_AMBER), ("induló", C_CYAN), ("katonai/spec.", C_RED)):
            w = QFontMetrics(p.font()).horizontalAdvance(name)
            p.setPen(C_MUTED)
            p.drawText(QPointF(x - w, y), name)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(x - w - 8, y - 4), 3, 3)
            y += 15

    def _draw_hud_compact(self, p, area, rng):
        """kompakt nezet: csak reptér kod + darabszam fent, hatotav lent"""
        ap = self.app.airport
        n = sum(1 for pl in self.app.store.planes.values() if not pl.on_ground)
        p.setFont(font(11, bold=True, mono=True))
        p.setPen(C_GREEN)
        p.drawText(QPointF(area.left() + 8, area.top() + 16), ap["icao"])
        p.setPen(C_CYAN)
        txt = f"{n} gép"
        p.drawText(QPointF(area.right() - 8 - QFontMetrics(p.font()).horizontalAdvance(txt), area.top() + 16), txt)
        p.setFont(font(10, bold=True, mono=True))
        txt = f"{rng:.0f} km" if rng >= 10 else f"{rng:.1f} km"
        p.setPen(with_alpha(C_GREEN, 0.8))
        p.drawText(QPointF(area.right() - 8 - QFontMetrics(p.font()).horizontalAdvance(txt), area.bottom() - 8), txt)

    def _draw_selected_card(self, p, area, now, ap):
        pl = self.app.store.planes.get(self.selected) if self.selected else None
        if not pl:
            return
        status, cat, airline = classify(pl, ap, self.app.cfg_area)
        col = CAT_COLOR[cat]
        lat, lon = pl.pos_at(now)
        x, y = to_xy_km(lat, lon, ap["lat"], ap["lon"])
        dist = math.hypot(x, y)
        if area.height() < 330 or self.app.compact:
            # kis ablak: csak egy kompakt sav, hogy ne takarja a radart
            if self.app.compact:
                txt = f"{pl.label} · {fmt_alt(pl.alt_ft)} · {int(pl.gs_kt)}kt"
                p.setFont(font(10, bold=True, mono=True))
            else:
                txt = f"{pl.label}  ·  {fmt_alt(pl.alt_ft)}  ·  {int(pl.gs_kt)} kt  ·  {int(pl.track)}°  ·  {dist:.0f} km"
                p.setFont(font(11, bold=True, mono=True))
            w = QFontMetrics(p.font()).horizontalAdvance(txt) + 22
            r = QRectF(area.left() + 6, area.bottom() - 28, w, 22)
            p.setPen(QPen(with_alpha(col, 0.6), 1))
            p.setBrush(with_alpha(C_SURFACE, 0.93))
            p.drawRoundedRect(r, 12, 12)
            p.setPen(col)
            p.drawText(r, Qt.AlignCenter, txt)
            return
        lines = [
            ("", f"{airline or '—'}  ·  {pl.type or '?'}  ·  {pl.reg or pl.hex.upper()}"),
            ("Magasság", f"{fmt_alt(pl.alt_ft)}   {fmt_int((pl.alt_ft or 0) / 3.28084)} m"),
            ("Sebesség", f"{int(pl.gs_kt)} kt   {pl.speed_kmh} km/h"),
            ("Irány", f"{int(pl.track)}° ({compass_hu(pl.track)})   {int(pl.vrate):+d} ft/min"),
            ("Távolság", f"{dist:.1f} km   {status}"),
        ]
        w, h = 270, 30 + 17 * len(lines)
        r = QRectF(area.left() + 10, area.bottom() - h - 10, w, h)
        p.setPen(QPen(with_alpha(col, 0.6), 1))
        p.setBrush(with_alpha(C_SURFACE, 0.93))
        p.drawRoundedRect(r, 10, 10)
        p.setFont(font(16, bold=True, mono=True))
        p.setPen(col)
        p.drawText(QPointF(r.left() + 12, r.top() + 24), pl.label)
        yy = r.top() + 42
        for key, val in lines:
            p.setFont(font(10))
            p.setPen(C_MUTED)
            if key:
                p.drawText(QPointF(r.left() + 12, yy), key)
            p.setFont(font(10, mono=True))
            p.setPen(C_TEXT)
            p.drawText(QPointF(r.left() + (78 if key else 12), yy), val)
            yy += 17


# ============================================================
# 2. ATC RADIO (ESP 2. oldal)
# ============================================================
class RadioView(ViewBase):
    def __init__(self, app, parent=None):
        super().__init__(app, parent)
        self._tiles = []
        self._play_rect = QRectF()
        self._vol_rect = QRectF()
        self.vol_overlay_until = 0.0

    def tick(self):
        self.update()

    def wheelEvent(self, e):
        rd = self.app.radio
        rd.set_volume(rd.volume + (5 if e.angleDelta().y() > 0 else -5))
        self.vol_overlay_until = time.monotonic() + 1.5
        self.app.panel.sync()

    def clicked(self, pos):
        rd = self.app.radio
        for i, r in enumerate(self._tiles):
            if r.contains(pos):
                rd.play(i)
                self.app.panel.sync()
                return
        if self._play_rect.contains(pos):
            rd.toggle()
            self.app.panel.sync()
        elif self._vol_rect.contains(pos) and self._vol_rect.width() > 0:
            rd.set_volume(round((pos.x() - self._vol_rect.left()) / self._vol_rect.width() * 100))
            self.app.panel.sync()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        rd = self.app.radio
        cpt = self.app.compact
        area = self.content_rect()
        p.fillRect(self.rect(), C_BG)
        ch = ATC_CHANNELS[rd.channel]
        t = time.monotonic()
        # meretek: normal / kompakt
        m = 8 if cpt else 16
        head_h = 30 if cpt else 60
        tiles_h = 50 if cpt else 74
        foot_h = 42 if cpt else 70

        st_col = C_GREEN if rd.live else (C_AMBER if rd.wanted else C_MUTED)
        if cpt:
            p.setFont(font(10))
            p.setPen(st_col)
            p.drawText(QRectF(area.left() + m, area.top() + 4, area.width() - 2 * m - 50, 20),
                       Qt.AlignLeft | Qt.AlignVCenter, rd.status)
        else:
            p.setFont(font(16, bold=True))
            p.setPen(C_TEXT)
            p.drawText(QPointF(area.left() + m, area.top() + 28), "LHBP ATC rádió")
            p.setFont(font(11))
            p.setPen(st_col)
            p.drawText(QPointF(area.left() + m, area.top() + 46), rd.status)

        # ON AIR jelzo
        if rd.live:
            a = 0.5 + 0.5 * math.sin(t * 4)
            r = QRectF(area.right() - m - (46 if cpt else 74), area.top() + (5 if cpt else 14),
                       46 if cpt else 74, 18 if cpt else 22)
            p.setPen(QPen(with_alpha(C_RED, 0.6 + 0.4 * a), 1.2))
            p.setBrush(with_alpha(C_RED, 0.12))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.setFont(font(9 if cpt else 10, bold=True))
            p.setPen(C_RED)
            p.drawText(r, Qt.AlignCenter, "● ÉLŐ")

        # nagy frekvencia
        mid_h = area.height() - head_h - tiles_h - foot_h
        mid = QRectF(area.left() + m, area.top() + head_h, area.width() - 2 * m, max(50, mid_h))
        p.setPen(QPen(C_BORDER, 1))
        p.setBrush(C_SURFACE)
        p.drawRoundedRect(mid, 10 if cpt else 14, 10 if cpt else 14)
        fs = max(20 if cpt else 26, min(64, mid.height() * 0.42, mid.width() / 6.5))
        p.setFont(font(fs, bold=True, mono=True))
        p.setPen(C_GREEN if rd.wanted else C_GREEN_D)
        p.drawText(QRectF(mid.left(), mid.top() + 4, mid.width(), mid.height() * 0.62), Qt.AlignCenter, ch["freq"])
        p.setFont(font(11 if cpt else 14, bold=True))
        p.setPen(C_TEXT)
        p.drawText(QRectF(mid.left(), mid.top() + mid.height() * 0.55, mid.width(), 20), Qt.AlignCenter,
                   f"{ch['name']}  ·  MHz")

        # aktivitas-savok (dekorativ, csak ha szol)
        bars = 20 if cpt else 32
        pad = 16 if cpt else 30
        bw = (mid.width() - 2 * pad) / bars
        base_y = mid.bottom() - (8 if cpt else 14)
        amp = 8 if cpt else 14
        for i in range(bars):
            h = 2 + amp * abs(math.sin(t * 3.1 + i * 0.7) * math.sin(t * 1.7 + i * 0.31)) if rd.live else 2
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(C_GREEN, 0.25 + (0.5 if rd.live else 0)))
            p.drawRoundedRect(QRectF(mid.left() + pad + i * bw, base_y - h, bw * 0.6, h), 1, 1)

        # csatorna csempek
        ty = mid.bottom() + (6 if cpt else 12)
        n = len(ATC_CHANNELS)
        gap = 6 if cpt else 10
        tw = (area.width() - 2 * m - gap * (n - 1)) / n
        self._tiles = []
        for i, c in enumerate(ATC_CHANNELS):
            r = QRectF(area.left() + m + i * (tw + gap), ty, tw, tiles_h - (6 if cpt else 10))
            self._tiles.append(r)
            act = (i == rd.channel)
            p.setPen(QPen(C_GREEN if act else C_BORDER, 1.5 if act else 1))
            p.setBrush(with_alpha(C_GREEN, 0.10) if act else C_SURFACE)
            p.drawRoundedRect(r, 8 if cpt else 10, 8 if cpt else 10)
            p.setFont(font(10 if cpt else 12, bold=True))
            p.setPen(C_TEXT if act else C_MUTED)
            p.drawText(QRectF(r.left(), r.top() + (4 if cpt else 8), r.width(), 18), Qt.AlignCenter,
                       c["short"] if cpt else c["name"])
            p.setFont(font(11 if cpt else 13, mono=True))
            p.setPen(C_GREEN if act else C_MUTED)
            p.drawText(QRectF(r.left(), r.top() + (21 if cpt else 30), r.width(), 20), Qt.AlignCenter, c["freq"])

        # play gomb + hangero
        by = ty + tiles_h + (0 if cpt else 4)
        pw, ph = (44, 30) if cpt else (120, 36)
        self._play_rect = QRectF(area.left() + m, by, pw, ph)
        pc = C_GREEN if not rd.wanted else C_RED
        p.setPen(QPen(pc, 1.4))
        p.setBrush(C_SURFACE2)
        p.drawRoundedRect(self._play_rect, ph / 2, ph / 2)
        p.setFont(font(12, bold=True))
        p.setPen(pc)
        if cpt:
            label = "▶" if not rd.wanted else "■"
        else:
            label = "▶  Lejátszás" if not rd.wanted else "■  Leállítás"
        p.drawText(self._play_rect, Qt.AlignCenter, label)
        vr = QRectF(self._play_rect.right() + 12, by + ph / 2 - 4,
                    area.right() - m - self._play_rect.right() - (52 if cpt else 60), 8)
        p.setPen(Qt.NoPen)
        p.setBrush(C_BORDER)
        p.drawRoundedRect(vr, 4, 4)
        p.setBrush(C_GREEN)
        p.drawRoundedRect(QRectF(vr.left(), vr.top(), vr.width() * rd.volume / 100, vr.height()), 4, 4)
        p.setFont(font(10 if cpt else 11, mono=True))
        p.setPen(C_TEXT)
        p.drawText(QPointF(vr.right() + 8, vr.bottom() + 1), f"{rd.volume}%")
        self._vol_rect = vr.adjusted(0, -8, 0, 8)

        # hangero overlay (mint az ESP-n)
        if time.monotonic() < self.vol_overlay_until:
            ow, oh = (130, 90) if cpt else (180, 120)
            ov = QRectF(area.center().x() - ow / 2, area.center().y() - oh / 2, ow, oh)
            p.setPen(QPen(C_GREEN, 1.2))
            p.setBrush(with_alpha(C_BG, 0.94))
            p.drawRoundedRect(ov, 14, 14)
            p.setFont(font(11 if cpt else 12, bold=True))
            p.setPen(C_CYAN)
            p.drawText(QRectF(ov.left(), ov.top() + 6, ov.width(), 20), Qt.AlignCenter, "Hangerő")
            p.setFont(font(30 if cpt else 44, bold=True, mono=True))
            p.setPen(C_TEXT)
            p.drawText(QRectF(ov.left(), ov.top() + 22, ov.width(), oh - 46), Qt.AlignCenter, str(rd.volume))
            bar = QRectF(ov.left() + 16, ov.bottom() - 18, ov.width() - 32, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(C_BORDER)
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(C_GREEN)
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * rd.volume / 100, 6), 3, 3)
        p.end()


# ============================================================
# FELSO SAV
# ============================================================
class TopBar(QWidget):
    H = 34

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setFixedHeight(self.H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(4)
        lay.addStretch(1)
        self.btn_compact = self._btn("⇲", "Kompakt nézet (C)", app.toggle_compact)
        self.btn_panel = self._btn("☰", "Vezérlőpult (P)", app.toggle_panel)
        self.btn_pause = self._btn("❚❚", "Szünet (Space)", app.toggle_pause)
        self.btn_hide = self._btn("–", "Elrejtés a tálcára (H)", app.hide_to_tray)
        self.btn_close = self._btn("✕", "Kilépés", app.quit_app, danger=True)
        for b in (self.btn_compact, self.btn_panel, self.btn_pause, self.btn_hide, self.btn_close):
            lay.addWidget(b)
        self._press = None

    def _btn(self, txt, tip, fn, danger=False):
        b = QPushButton(txt)
        b.setToolTip(tip)
        b.setFixedSize(28, 24)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        hov = "#3a1515" if danger else "#17261e"
        b.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: #9fb8aa; border: none; border-radius: 6px;
                          font: 13px "{FONT_UI}"; }}
            QPushButton:hover {{ background: {hov}; color: #ffffff; }}""")
        b.clicked.connect(fn)
        return b

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.window().windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        self.app.toggle_compact()

    def set_compact(self, on):
        """kompaktban alacsonyabb sav, kevesebb gomb"""
        self.setFixedHeight(26 if on else self.H)
        self.btn_panel.setVisible(not on)
        self.btn_hide.setVisible(not on)
        self.btn_compact.setText("⇱" if on else "⇲")
        self.btn_compact.setToolTip("Normál nézet (C)" if on else "Kompakt nézet (C)")
        for b in (self.btn_compact, self.btn_panel, self.btn_pause, self.btn_hide, self.btn_close):
            b.setFixedSize(24 if on else 28, 20 if on else 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), C_SURFACE)
        p.setPen(QPen(C_BORDER, 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        st = self.app.store
        cpt = self.app.compact
        p.setFont(font(11 if cpt else 12, bold=True))
        p.setPen(C_GREEN)
        base = 17 if cpt else 22
        x0 = 10 if cpt else 14
        p.drawText(QPointF(x0, base), "ATC")
        p.setPen(C_TEXT)
        p.drawText(QPointF(x0 + QFontMetrics(p.font()).horizontalAdvance("ATC "), base), "DECK")
        x = 90 if cpt else 104
        if self.app.paused:
            col, txt = C_AMBER, "SZÜNET"
        elif st.ok is None:
            col, txt = C_MUTED, "Betöltés…"
        elif st.ok:
            col, txt = C_GREEN, f"{st.source} · {st.seconds_left()}s"
        else:
            col, txt = C_RED, "API hiba – újra…"
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(x, base - 5), 3.5, 3.5)
        if cpt:
            return
        p.setFont(font(10, mono=True))
        p.setPen(C_MUTED)
        p.drawText(QPointF(x + 9, 21), f"{VIEW_NAMES[self.app.view_idx]}  ·  {txt}")
        p.end()


# ============================================================
# ALSO VEZERLOPULT
# ============================================================
PANEL_QSS = f"""
QFrame#panel {{ background: rgba(12,19,16,245); border-top: 1px solid #22372b; }}
QLabel {{ color: #6f8a7b; font: 600 11px "{FONT_UI}"; }}
QPushButton.seg {{ background: #0a100d; color: #9fb8aa; border: 1px solid #1d2b24; padding: 5px 10px;
                  font: 12px "{FONT_UI}"; border-radius: 7px; }}
QPushButton.seg:hover {{ border-color: #2f5a42; color: #ffffff; }}
QPushButton.seg:checked {{ background: #123322; color: #3dff8a; border-color: #3dff8a; }}
QComboBox {{ background: #0a100d; color: #d4ecde; border: 1px solid #1d2b24; border-radius: 7px;
            padding: 4px 8px; font: 12px "{FONT_UI}"; min-width: 120px; }}
QComboBox QAbstractItemView {{ background: #0c1310; color: #d4ecde; selection-background-color: #123322; }}
QSlider::groove:horizontal {{ height: 4px; background: #1d2b24; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #3dff8a; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #d4ecde; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QCheckBox {{ color: #9fb8aa; font: 12px "{FONT_UI}"; spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid #2f4a3b; background: #0a100d; }}
QCheckBox::indicator:checked {{ background: #3dff8a; border-color: #3dff8a; }}
"""


class ControlPanel(QFrame):
    H = 192

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.setObjectName("panel")
        self.setStyleSheet(PANEL_QSS)
        self._syncing = False
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)

        # 1. sor: nezet
        r1 = QHBoxLayout()
        r1.addWidget(self._lbl("NÉZET"))
        self.view_grp = QButtonGroup(self)
        for i, name in enumerate(VIEW_NAMES):
            b = self._seg(f"{i + 1}  {name}")
            self.view_grp.addButton(b, i)
            r1.addWidget(b)
        r1.addStretch(1)
        self.view_grp.idClicked.connect(app.set_view)
        v.addLayout(r1)

        # 2. sor: repuloter + frissites
        r2 = QHBoxLayout()
        r2.addWidget(self._lbl("REPÜLŐTÉR"))
        self.cb_airport = QComboBox()
        for a in AIRPORTS:
            self.cb_airport.addItem(f"{a['icao']}  {a['name']}")
        self.cb_airport.currentIndexChanged.connect(lambda i: (not self._syncing) and app.set_airport(i))
        r2.addWidget(self.cb_airport, 1)
        r2.addSpacing(8)
        r2.addWidget(self._lbl("FRISSÍTÉS"))
        self.cb_int = QComboBox()
        self.cb_int.setStyleSheet("min-width: 56px;")
        self.intervals = [5, 10, 15, 30, 60, 120]
        for sec in self.intervals:
            self.cb_int.addItem(f"{sec} mp")
        self.cb_int.currentIndexChanged.connect(lambda i: (not self._syncing) and app.set_interval(self.intervals[i]))
        r2.addWidget(self.cb_int)
        v.addLayout(r2)

        # 3. sor: zoom (radar)
        r3 = QHBoxLayout()
        r3.addWidget(self._lbl("ZOOM"))
        self.sl_range = QSlider(Qt.Horizontal)
        self.sl_range.setRange(0, 1000)
        self.sl_range.setToolTip("Radar hatótáv (görgővel is)")
        self.sl_range.valueChanged.connect(self._range_changed)
        r3.addWidget(self.sl_range, 1)
        self.lb_range = QLabel()
        self.lb_range.setFixedWidth(52)
        r3.addWidget(self.lb_range)
        v.addLayout(r3)

        # 4. sor: ATC csatornak + lejatszas + hangero
        r4 = QHBoxLayout()
        r4.addWidget(self._lbl("ATC"))
        self.ch_grp = QButtonGroup(self)
        for i, c in enumerate(ATC_CHANNELS):
            b = self._seg(f"{c['short']} {c['freq']}")
            b.setToolTip(f"LHBP {c['name']} {c['freq']} MHz")
            self.ch_grp.addButton(b, i)
            r4.addWidget(b)
        self.ch_grp.idClicked.connect(self._channel_clicked)
        self.btn_play = QPushButton()
        self.btn_play.setProperty("class", "seg")
        self.btn_play.setFixedWidth(36)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(self._play_clicked)
        r4.addWidget(self.btn_play)
        r4.addSpacing(4)
        self.sl_vol = QSlider(Qt.Horizontal)
        self.sl_vol.setRange(0, 100)
        self.sl_vol.setMinimumWidth(60)
        self.sl_vol.setToolTip("Hangerő")
        self.sl_vol.valueChanged.connect(lambda val: (not self._syncing) and app.radio.set_volume(val))
        r4.addWidget(self.sl_vol, 1)
        v.addLayout(r4)

        # 5. sor: kapcsolok
        r5 = QHBoxLayout()
        self.ck_top = QCheckBox("Mindig felül")
        self.ck_trails = QCheckBox("Nyomvonal")
        self.ck_labels = QCheckBox("Címkék")
        self.ck_ground = QCheckBox("Földi gépek")
        for ck in (self.ck_top, self.ck_trails, self.ck_labels, self.ck_ground):
            ck.setFocusPolicy(Qt.NoFocus)
            ck.toggled.connect(self._flags_changed)
            r5.addWidget(ck)
        r5.addStretch(1)
        v.addLayout(r5)
        v.addStretch(1)

        app.radio.changed.connect(self.sync)

    def _lbl(self, t):
        l = QLabel(t)
        l.setMinimumWidth(10)
        return l

    def _seg(self, t):
        b = QPushButton(t)
        b.setCheckable(True)
        b.setProperty("class", "seg")
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    # zoom csuszka logaritmikus: 3..400 km
    @staticmethod
    def _slider_to_km(v):
        return 3 * (400 / 3) ** (v / 1000)

    @staticmethod
    def _km_to_slider(km):
        return int(1000 * math.log(km / 3) / math.log(400 / 3))

    def _range_changed(self, v):
        km = self._slider_to_km(v)
        self.lb_range.setText(f"{km:.0f} km")
        if not self._syncing:
            self.app.set_range(km, from_panel=True)

    def _channel_clicked(self, i):
        self.app.radio.play(i)

    def _play_clicked(self):
        self.app.radio.toggle()

    def _flags_changed(self):
        if self._syncing:
            return
        self.app.set_flags(self.ck_top.isChecked(), self.ck_trails.isChecked(),
                           self.ck_labels.isChecked(), self.ck_ground.isChecked())

    def sync(self):
        a = self.app
        self._syncing = True
        self.view_grp.button(a.view_idx).setChecked(True)
        self.cb_airport.setCurrentIndex(a.airport_idx)
        self.sl_range.setValue(self._km_to_slider(a.cfg_range))
        self.lb_range.setText(f"{a.cfg_range:.0f} km")
        rd = a.radio
        self.ch_grp.button(rd.channel).setChecked(rd.wanted)
        for i in range(len(ATC_CHANNELS)):
            self.ch_grp.button(i).setChecked(rd.wanted and i == rd.channel)
        self.ch_grp.setExclusive(rd.wanted)
        self.btn_play.setText("■" if rd.wanted else "▶")
        self.btn_play.setToolTip("Leállítás" if rd.wanted else "Lejátszás")
        self.btn_play.setEnabled(rd.available)
        self.sl_vol.setValue(rd.volume)
        self.ck_top.setChecked(a.cfg_top)
        self.ck_trails.setChecked(a.cfg_trails)
        self.ck_labels.setChecked(a.cfg_labels)
        self.ck_ground.setChecked(a.cfg_ground)
        if a.store.interval_s in self.intervals:
            self.cb_int.setCurrentIndex(self.intervals.index(a.store.interval_s))
        self._syncing = False


# ============================================================
# FO ABLAK
# ============================================================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FILE = os.path.join(APP_DIR, "assets", "atc_deck.ico")
SETTINGS_FILE = os.path.join(APP_DIR, "beallitasok.ini")


def draw_icon_pixmap(size=64):
    """az app ikonja kodbol rajzolva (64-es racson, tetszoleges meretre skalazva)"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64.0, size / 64.0)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#061a0e"))
    p.drawEllipse(2, 2, 60, 60)
    p.setPen(QPen(C_GREEN, 3))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QPen(with_alpha(C_GREEN, 0.5), 2))
    p.drawEllipse(18, 18, 28, 28)
    p.setPen(QPen(C_GREEN, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(32, 32, 52, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(C_AMBER)
    p.drawEllipse(QPointF(22, 40), 4, 4)
    p.end()
    return pm


def make_icon():
    if os.path.exists(ICON_FILE):
        return QIcon(ICON_FILE)
    return QIcon(draw_icon_pixmap(64))


# ============================================================
# PARANCSIKON (Windows): asztal + Start menu
# ============================================================
SHORTCUT_PS = r"""
$ws = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($d in $dirs) {
  if (-not $d) { continue }
  $lnk = $ws.CreateShortcut((Join-Path $d 'ATC Deck.lnk'))
  $lnk.TargetPath = $env:ATCDECK_TARGET
  $lnk.Arguments = '"' + $env:ATCDECK_SCRIPT + '"'
  $lnk.WorkingDirectory = $env:ATCDECK_DIR
  if (Test-Path $env:ATCDECK_ICON) { $lnk.IconLocation = $env:ATCDECK_ICON + ',0' }
  $lnk.Description = 'ATC Deck - repulo radar es LHBP ATC radio'
  $lnk.Save()
  Write-Output ('Parancsikon: ' + (Join-Path $d 'ATC Deck.lnk'))
}
"""


def create_shortcuts():
    """Parancsikon az asztalra es a Start menube (pythonw-vel, konzolablak nelkul)."""
    import subprocess
    if sys.platform != "win32":
        print("Parancsikont csak Windowson lehet letrehozni.")
        return 1
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    env = dict(os.environ,
               ATCDECK_TARGET=pyw if os.path.exists(pyw) else exe,
               ATCDECK_SCRIPT=os.path.abspath(__file__),
               ATCDECK_DIR=APP_DIR,
               ATCDECK_ICON=ICON_FILE)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", SHORTCUT_PS],
                       env=env, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


def set_windows_app_id():
    """sajat tálcaikon-csoport (ne a Python ikonja latszodjon a talcan)"""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("zoltan234.ATCDeck")
        except Exception:
            pass


class RadarWindow(QWidget):
    RADIUS = 14

    def __init__(self, demo=False):
        super().__init__()
        # beallitasok az app mappajaban (nem a registryben) - a mappa torlesevel minden eltunik
        self.settings = QSettings(SETTINGS_FILE, QSettings.IniFormat)
        s = self.settings
        self.view_idx = int(s.value("view", 0)) % len(VIEW_NAMES)
        self.compact = False
        self._normal_size = None
        self.airport_idx = min(int(s.value("airport", 0)), len(AIRPORTS) - 1)
        self.cfg_range = float(s.value("range", 60))
        self.cfg_area = float(s.value("area", 35))
        self.cfg_top = s.value("top", "true") == "true"
        self.cfg_trails = s.value("trails", "true") == "true"
        self.cfg_labels = s.value("labels", "true") == "true"
        self.cfg_ground = s.value("ground", "false") == "true"
        self.paused = False
        self._frozen_t = None
        self.panel_open = False

        self.store = DataStore(demo=demo, parent=self)
        self.store.set_interval(int(s.value("interval", 10)))
        self.store.lat, self.store.lon = self.airport["lat"], self.airport["lon"]
        self.store.set_radius_km(max(self.cfg_range, self.cfg_area))
        self.radio = Radio(self)
        self.radio.channel = int(s.value("channel", 0)) % len(ATC_CHANNELS)
        self.radio.set_volume(int(s.value("volume", 60)))
        try:
            self.radio._mode = {int(k): int(v) for k, v in json.loads(s.value("stream_modes", "{}")).items()}
        except Exception:
            pass

        self.setWindowTitle("ATC Deck")
        self.setWindowIcon(make_icon())
        self._apply_flags()
        self.setMinimumSize(480, 460)
        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)
        self.topbar = TopBar(self)
        root.addWidget(self.topbar)
        self.stack = QStackedWidget()
        self.views = [RadarView(self), RadioView(self)]
        for v in self.views:
            self.stack.addWidget(v)
        root.addWidget(self.stack, 1)

        self.panel = ControlPanel(self, self.stack)
        self.panel.hide()
        self.panel_anim = QPropertyAnimation(self.panel, b"pos", self)
        self.panel_anim.setDuration(260)
        self.panel_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.panel_anim.finished.connect(self._panel_anim_done)

        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(14, 14)
        self.grip.setStyleSheet("background: transparent;")

        self.stack.setCurrentIndex(self.view_idx)
        self.panel.sync()

        self.anim = QTimer(self)
        self.anim.timeout.connect(self._frame)
        self.anim.start(16)
        self.bar_timer = QTimer(self)
        self.bar_timer.timeout.connect(self.topbar.update)
        self.bar_timer.start(500)
        self.store.status_changed.connect(self.topbar.update)

        self._setup_tray()
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(560, 600)
        if s.value("compact", "false") == "true":
            self.set_compact(True)
        QTimer.singleShot(200, self.store.fetch)

    # --- tulajdonsagok ---
    @property
    def airport(self):
        return AIRPORTS[self.airport_idx]

    def now(self):
        return self._frozen_t if self.paused else time.monotonic()

    def panel_cover(self):
        """annyi helyet foglal, amennyit a panel tenylegesen eltakar (csuszas kozben is)"""
        if not self.panel.isVisible():
            return 0
        return max(0, min(ControlPanel.H, self.stack.height() - self.panel.y()))

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(150, self._close_panel_if_inactive)

    def _close_panel_if_inactive(self):
        """masik ablakra valtaskor a vezerlopult bezarodik (legordulo menu nyitva hagyja)"""
        if self.isActiveWindow() or QApplication.activePopupWidget() is not None:
            return
        if self.panel_open:
            self.panel_open = False
            self.panel_anim.stop()
            self.panel.hide()
            self.views[self.view_idx].update()

    # --- beallitas-muveletek ---
    def set_view(self, i):
        self.view_idx = i % len(self.views)
        self.stack.setCurrentIndex(self.view_idx)   # ez az uj nezetet a panel fole hozza...
        if self.panel.isVisible():
            self.panel.raise_()                     # ...ezert a panelt vissza kell hozni elore
            self.show_panel(False)                  # es nezetvaltaskor bezarodik
        self.panel.sync()
        self.topbar.update()

    def set_airport(self, i):
        self.airport_idx = i
        self.views[0].selected = None
        self.store.set_center(self.airport["lat"], self.airport["lon"])
        self.panel.sync()

    def set_range(self, km, from_panel=False):
        self.cfg_range = max(3.0, min(400.0, km))
        self.store.set_radius_km(max(self.cfg_range, self.cfg_area))
        if not from_panel:
            self.panel.sync()

    def set_interval(self, s):
        self.store.set_interval(s)

    def set_flags(self, top, trails, labels, ground):
        self.cfg_trails, self.cfg_labels, self.cfg_ground = trails, labels, ground
        if top != self.cfg_top:
            self.cfg_top = top
            self._apply_flags()
            self._update_mask()
            self.show()
        self.tray_top.setChecked(top)

    def _apply_flags(self):
        flags = Qt.Window | Qt.FramelessWindowHint
        if self.cfg_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self._frozen_t = time.monotonic()
        self.store.paused = self.paused
        if not self.paused:
            self.store.fetch()
        self.topbar.btn_pause.setText("▶" if self.paused else "❚❚")
        self.tray_pause.setText("Folytatás" if self.paused else "Szünet")
        self.topbar.update()
        self.views[self.view_idx].update()

    # --- vezerlopult animacio ---
    def toggle_panel(self):
        if self.compact and not self.panel_open:
            self.set_compact(False)
        self.show_panel(not self.panel_open)

    # --- kompakt nezet ---
    COMPACT_MIN = QSize(220, 210)
    COMPACT_DEFAULT = QSize(260, 280)

    def toggle_compact(self):
        self.set_compact(not self.compact)

    def set_compact(self, on):
        if on == self.compact:
            return
        if on:
            self.show_panel(False)
            self._normal_size = self.size()
            self.compact = True
            self.topbar.set_compact(True)
            self.setMinimumSize(self.COMPACT_MIN)
            cs = self.settings.value("compact_size")
            self.resize(cs if isinstance(cs, QSize) else self.COMPACT_DEFAULT)
        else:
            self.settings.setValue("compact_size", self.size())
            self.compact = False
            self.topbar.set_compact(False)
            self.setMinimumSize(480, 460)
            self.resize(self._normal_size or QSize(560, 600))
        self.tray_compact.setChecked(on)
        self.topbar.update()


    def show_panel(self, show):
        if show == self.panel_open and self.panel.isVisible() == show:
            return
        self.panel_open = show
        w, h = self.stack.width(), self.stack.height()
        self.panel.setFixedWidth(w)
        self.panel.setFixedHeight(ControlPanel.H)
        up = QPoint(0, h - ControlPanel.H)
        down = QPoint(0, h)
        self.panel.show()
        self.panel.raise_()
        self.panel_anim.stop()
        self.panel_anim.setStartValue(self.panel.pos() if self.panel.isVisible() else down)
        self.panel_anim.setEndValue(up if show else down)
        self.panel_anim.start()

    def _panel_anim_done(self):
        if not self.panel_open:
            self.panel.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_mask()
        self.grip.move(self.width() - 16, self.height() - 16)
        self.grip.raise_()
        if self.panel_open:
            self.panel.setFixedWidth(self.stack.width())
            self.panel.move(0, self.stack.height() - ControlPanel.H)

    # --- rajz ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.RADIUS, self.RADIUS)
        p.fillPath(path, C_BG)
        p.setPen(QPen(C_BORDER, 1))
        p.drawPath(path)
        p.end()

    def _update_mask(self):
        """lekerekitett sarkok (atlatszosag nelkul)"""
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.RADIUS, self.RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _frame(self):
        if not self.isVisible() or self.paused:
            return
        v = self.views[self.view_idx]
        v.tick()

    # --- billentyuk ---
    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_P, Qt.Key_F2):
            self.toggle_panel()
        elif k == Qt.Key_Escape:
            self.show_panel(False)
        elif k == Qt.Key_Space:
            self.toggle_pause()
        elif k == Qt.Key_H:
            self.hide_to_tray()
        elif k == Qt.Key_C:
            self.toggle_compact()
        elif Qt.Key_1 <= k <= Qt.Key_2:
            self.set_view(k - Qt.Key_1)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.set_range(self.cfg_range * 0.8)
        elif k == Qt.Key_Minus:
            self.set_range(self.cfg_range / 0.8)
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        self.tray_menu.exec(e.globalPos())

    # --- talca ---
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        self.tray.setToolTip("ATC Deck")
        m = QMenu()
        m.setStyleSheet(f"""QMenu {{ background: #0c1310; color: #d4ecde; border: 1px solid #1d2b24;
                            font: 12px "{FONT_UI}"; padding: 4px; }}
                            QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
                            QMenu::item:selected {{ background: #123322; color: #3dff8a; }}""")
        a_show = QAction("Megjelenítés / elrejtés", self)
        a_show.triggered.connect(self.toggle_visible)
        a_panel = QAction("Vezérlőpult", self)
        a_panel.triggered.connect(lambda: (self.show_normal(), self.show_panel(True)))
        self.tray_pause = QAction("Szünet", self)
        self.tray_pause.triggered.connect(self.toggle_pause)
        self.tray_top = QAction("Mindig felül", self, checkable=True)
        self.tray_top.setChecked(self.cfg_top)
        self.tray_top.toggled.connect(lambda on: self.set_flags(on, self.cfg_trails, self.cfg_labels, self.cfg_ground) or self.panel.sync())
        view_menu = m.addMenu("Nézet")
        for i, n in enumerate(VIEW_NAMES):
            act = QAction(n, self)
            act.triggered.connect(lambda _=False, i=i: self.set_view(i))
            view_menu.addAction(act)
        self.tray_compact = QAction("Kompakt nézet", self, checkable=True)
        self.tray_compact.toggled.connect(self.set_compact)
        m.addAction(self.tray_compact)
        a_radio = QAction("ATC rádió be/ki", self)
        a_radio.triggered.connect(lambda: (self.radio.toggle(), self.panel.sync()))
        a_quit = QAction("Kilépés", self)
        a_quit.triggered.connect(self.quit_app)
        for a in (a_show, a_panel, self.tray_pause, self.tray_top, a_radio):
            m.addAction(a)
        m.addSeparator()
        m.addAction(a_quit)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and self.toggle_visible())
        self.tray.show()

    def toggle_visible(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_normal()

    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        self.hide()

    def quit_app(self):
        s = self.settings
        was_compact = self.compact
        if was_compact:
            s.setValue("compact_size", self.size())
            pos = self.pos()
            self.compact = False                  # normal meretet mentjuk geometriakent
            self.setMinimumSize(480, 460)
            self.resize(self._normal_size or QSize(560, 600))
            self.move(pos)
        s.setValue("compact", "true" if was_compact else "false")
        s.setValue("geometry", self.saveGeometry())
        s.setValue("view", self.view_idx)
        s.setValue("airport", self.airport_idx)
        s.setValue("range", self.cfg_range)
        s.setValue("area", self.cfg_area)
        s.setValue("top", "true" if self.cfg_top else "false")
        s.setValue("trails", "true" if self.cfg_trails else "false")
        s.setValue("labels", "true" if self.cfg_labels else "false")
        s.setValue("ground", "true" if self.cfg_ground else "false")
        s.setValue("interval", self.store.interval_s)
        s.setValue("channel", self.radio.channel)
        s.setValue("volume", self.radio.volume)
        s.setValue("stream_modes", json.dumps(self.radio._mode))
        s.sync()
        self.radio.stop()
        self.tray.hide()
        QApplication.quit()


def main():
    ap = argparse.ArgumentParser(description="ATC Deck – asztali repülő radar és ATC rádió")
    ap.add_argument("--demo", action="store_true", help="internet nélküli demó forgalom")
    ap.add_argument("--parancsikon", action="store_true", help="parancsikon az asztalra és a Start menübe")
    args, _ = ap.parse_known_args()
    if args.parancsikon:
        sys.exit(create_shortcuts())
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("ATC Deck")
    app.setQuitOnLastWindowClosed(False)
    w = RadarWindow(demo=args.demo)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
