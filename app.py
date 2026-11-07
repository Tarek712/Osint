import asyncio
import hashlib
import logging
import math
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import config

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("OSINT-Core")


# ==========================================
# GEOSPATIAL & UTILITY FUNCTIONS
# ==========================================
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance in kilometers between two points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def match_sector(lat: float, lon: float) -> Optional[str]:
    """Check if coordinates fall inside any monitored Middle East / Iran sector."""
    for key, sec in config.SECTORS.items():
        if sec["min_lat"] <= lat <= sec["max_lat"] and sec["min_lon"] <= lon <= sec["max_lon"]:
            return sec["name_ar"]
    return None


def check_strategic_proximity(lat: float, lon: float) -> Optional[Tuple[str, float]]:
    """Return the nearest strategic military/nuclear site if within alert radius."""
    for site in config.STRATEGIC_SITES:
        dist = haversine_km(lat, lon, site["lat"], site["lon"])
        if dist <= site["radius_km"]:
            return site["name"], round(dist, 1)
    return None


# ==========================================
# STATE & DEDUPLICATION ENGINE (IN-MEMORY TTL)
# ==========================================
class StateEngine:
    def __init__(self):
        self.seen_events: Dict[str, float] = {}
        self.aircraft_tracks: Dict[str, List[Tuple[float, float, float, float]]] = {}
        self.stats = {
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "alerts_sent": 0,
            "aviation_scans": 0,
            "seismic_scans": 0,
            "nga_scans": 0,
            "last_alert_time": "لا يوجد حتى الآن"
        }

    def is_duplicate(self, event_key: str, ttl_seconds: int = 3600) -> bool:
        now = time.time()
        # Clean expired keys periodically
        expired = [k for k, exp in self.seen_events.items() if exp < now]
        for k in expired:
            del self.seen_events[k]

        if event_key in self.seen_events:
            return True
        self.seen_events[event_key] = now + ttl_seconds
        return False

    def record_aircraft_heading(self, hex_code: str, lat: float, lon: float, track: float) -> bool:
        """
        Detects if a tanker or ISR aircraft is flying in an orbit / racetrack pattern
        by analyzing cumulative heading changes within a confined radius.
        """
        now = time.time()
        history = self.aircraft_tracks.get(hex_code, [])
        # Keep last 25 minutes of positions
        history = [pt for pt in history if now - pt[0] <= 1500]
        history.append((now, lat, lon, track))
        self.aircraft_tracks[hex_code] = history

        if len(history) < 4:
            return False

        total_turn = 0.0
        for i in range(1, len(history)):
            diff = abs(history[i][3] - history[i - 1][3])
            if diff > 180:
                diff = 360 - diff
            total_turn += diff

        # Check displacement between first and last point
        displacement = haversine_km(history[0][1], history[0][2], history[-1][1], history[-1][2])
        return total_turn >= 160.0 and displacement < 90.0


state = StateEngine()


# ==========================================
# TELEGRAM PRESENTATION & DISPATCHER
# ==========================================
async def send_telegram_alert(
    client: httpx.AsyncClient,
    severity: str,
    category: str,
    title: str,
    sector_name: str,
    lat: Optional[float],
    lon: Optional[float],
    telemetry_lines: List[str],
    extra_links: Optional[List[Tuple[str, str]]] = None
):
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning(f"Telegram credentials not set. Alert skipped: {title}")
        return

    badge = "🔴 CRITICAL" if severity == "CRITICAL" else "🟠 HIGH PRIORITY"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg_parts = [
        f"<b>{badge} | {category}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<b>الحدث:</b> {title}",
        f"<b>القطاع:</b> {sector_name}",
    ]

    if lat is not None and lon is not None:
        msg_parts.append(f"<b>الإحداثيات:</b> <code>{lat:.4f}, {lon:.4f}</code>")
        prox = check_strategic_proximity(lat, lon)
        if prox:
            site_name, dist_km = prox
            msg_parts.append(f"⚠️ <b>قرب موقع حساس:</b> {site_name} ({dist_km} كم)")

    msg_parts.append("")
    msg_parts.append("<b>البيانات الخام (Raw Telemetry):</b>")
    for line in telemetry_lines:
        msg_parts.append(f"• {line}")

    msg_parts.append("━━━━━━━━━━━━━━━━━━━━")
    msg_parts.append(f"⏱ <code>{now_str}</code>")

    text = "\n".join(msg_parts)

    # Build Tactical Inline Buttons
    inline_keyboard = []
    if lat is not None and lon is not None:
        gmaps_url = f"https://www.google.com/maps?q={lat},{lon}&t=k"
        copernicus_url = (
            f"https://browser.dataspace.copernicus.eu/?zoom=13&lat={lat}&lng={lon}"
        )
        inline_keyboard.append([
            {"text": "🛰 Google Satellite", "url": gmaps_url},
            {"text": "📡 Sentinel Radar/Optical", "url": copernicus_url}
        ])

    if extra_links:
        row = [{"text": label, "url": url} for label, url in extra_links]
        inline_keyboard.append(row)

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = await client.post(url, json=payload, timeout=15.0)
        if resp.status_code == 200:
            state.stats["alerts_sent"] += 1
            state.stats["last_alert_time"] = now_str
            logger.info(f"Alert dispatched: {title}")
        else:
            logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Failed to send Telegram alert: {exc}")


# ==========================================
# COLLECTOR 1: MILITARY AVIATION & ISR (ADS-B RAW)
# ==========================================
async def poll_military_aviation(client: httpx.AsyncClient):
    """
    Polls unfiltered ADS-B military feeds (airplanes.live / adsb.lol fallback)
    and filters strictly for high-value ISR, Tankers, Strategic Transports, or Emergency Squawks.
    """
    state.stats["aviation_scans"] += 1
    endpoints = [
        "https://api.airplanes.live/v2/mil",
        "https://api.adsb.lol/v2/mil"
    ]

    aircraft_list = []
    for ep in endpoints:
        try:
            resp = await client.get(ep, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                aircraft_list = data.get("ac", [])
                break
        except Exception as exc:
            logger.warning(f"Aviation endpoint {ep} failed: {exc}")

    if not aircraft_list:
        return

    for ac in aircraft_list:
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            continue

        sector_name = match_sector(lat, lon)
        if not sector_name:
            continue

        hex_code = str(ac.get("hex", "")).upper()
        callsign = str(ac.get("flight", "")).strip().upper() or "UNKNOWN"
        ac_type = str(ac.get("t", "")).strip().upper()
        alt_baro = ac.get("alt_baro", "N/A")
        gs = ac.get("gs", "N/A")
        track = float(ac.get("track", 0.0) or 0.0)
        squawk = str(ac.get("squawk", ""))

        # Check if High-Value Type or Strategic Callsign
        type_desc = config.HIGH_VALUE_AIRCRAFT_TYPES.get(ac_type)
        is_strategic_callsign = callsign.startswith(config.MILITARY_CALLSIGN_PREFIXES)
        is_emergency = squawk in ("7700", "7600", "7500")

        # Record heading to detect Orbit/Racetrack patterns (Tankers & ISR)
        is_orbiting = state.record_aircraft_heading(hex_code, lat, lon, track)

        # Ignore routine low-priority military trainers/helicopters unless orbiting or emergency
        if not (type_desc or is_strategic_callsign or is_emergency or is_orbiting):
            continue

        severity = "CRITICAL" if (is_orbiting or is_emergency or ac_type in ("R135", "E3CF", "E3TF", "B52", "B1", "RQ4")) else "HIGH"

        # Deduplication key: alert once per 2 hours per aircraft unless it starts orbiting
        dedup_suffix = "ORBIT" if is_orbiting else "DETECT"
        dedup_key = f"AIR_{hex_code}_{dedup_suffix}"
        if state.is_duplicate(dedup_key, ttl_seconds=7200):
            continue

        pattern_note = "🔄 مدار دائري نشط (Orbit/Racetrack - مؤشر عمليات قتالية أو تزود بالوقود)" if is_orbiting else "عبور / دورية استطلاع نشطة"
        title = type_desc if type_desc else f"طائرة عسكرية خاصة ({ac_type or 'غير مصنف'})"
        if is_orbiting:
            title = f"نشاط عملياتي (مدار مستمر): {title}"

        telemetry = [
            f"النداء اللاسلكي (Callsign): <code>{callsign}</code>",
            f"كود الإيكاو (ICAO Hex): <code>{hex_code}</code>",
            f"الطراز الخام (Type): <code>{ac_type or 'N/A'}</code>",
            f"الارتفاع (Altitude): <code>{alt_baro} ft</code> | السرعة: <code>{gs} kt</code>",
            f"الاتجاه (Heading): <code>{track}°</code> | Squawk: <code>{squawk or 'N/A'}</code>",
            f"نمط الطيران: {pattern_note}"
        ]

        adsb_url = f"https://globe.adsbexchange.com/?icao={hex_code.lower()}"
        airframes_url = f"https://app.airframes.io/aircraft/{hex_code.lower()}"

        await send_telegram_alert(
            client=client,
            severity=severity,
            category="MILITARY AVIATION / SIGINT",
            title=title,
            sector_name=sector_name,
            lat=lat,
            lon=lon,
            telemetry_lines=telemetry,
            extra_links=[("✈️ ADS-B Exchange Live", adsb_url), ("📡 ACARS / Airframes", airframes_url)]
        )


# ==========================================
# COLLECTOR 2: SEISMIC & SURFACE EXPLOSIONS (USGS / EMSC)
# ==========================================
async def poll_seismic_anomalies(client: httpx.AsyncClient):
    """
    Polls global seismic feeds and filters strictly for shallow events (0-5km depth)
    indicative of bunker-buster strikes, ammunition depot explosions, or underground tests,
    or major events near sensitive Iranian/Middle Eastern facilities.
    """
    state.stats["seismic_scans"] += 1
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson"

    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code != 200:
            return
        data = resp.json()
    except Exception as exc:
        logger.warning(f"Seismic feed error: {exc}")
        return

    now_ms = time.time() * 1000.0
    max_age_ms = config.STARTUP_LOOKBACK_MINUTES * 60 * 1000.0

    for feat in data.get("features", []):
        feat_id = feat.get("id")
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])

        if len(coords) < 3:
            continue

        lon, lat, depth_km = float(coords[0]), float(coords[1]), float(coords[2])
        mag = float(props.get("mag") or 0.0)
        event_time_ms = float(props.get("time") or 0.0)
        event_type = str(props.get("type", "earthquake")).lower()
        place = str(props.get("place", "Unknown Location"))

        # Strict time filter for Render spin-ups
        if (now_ms - event_time_ms) > max_age_ms:
            continue

        sector_name = match_sector(lat, lon)
        if not sector_name:
            continue

        prox = check_strategic_proximity(lat, lon)
        is_shallow_explosion_signature = depth_km <= 5.0
        is_explicit_explosion = "explosion" in event_type or "blast" in event_type

        # Ignore routine natural deep earthquakes unless near a strategic nuclear/military site or >= 4.8 mag
        if not (is_shallow_explosion_signature or is_explicit_explosion or prox is not None or mag >= 4.8):
            continue

        dedup_key = f"SEISMIC_{feat_id}"
        if state.is_duplicate(dedup_key, ttl_seconds=86400):
            continue

        if is_explicit_explosion or (depth_km <= 2.5 and prox is not None):
            severity = "CRITICAL"
            title = f"بصمة انفجار سطحي / ارتجاج عالي الخطورة ({mag}M)"
        elif is_shallow_explosion_signature:
            severity = "CRITICAL"
            title = f"حدث زلزالي ضحل جداً (احتمال انفجار ضخم أو ضربة عميقة - {mag}M)"
        else:
            severity = "HIGH"
            title = f"نشاط زلزالي في قطاع استراتيجي ({mag}M)"

        event_dt = datetime.fromtimestamp(event_time_ms / 1000.0, tz=timezone.utc).strftime("%H:%M:%S UTC")

        telemetry = [
            f"القوة المسجلة (Magnitude): <code>{mag} M</code>",
            f"العمق الجيولوجي (Depth): <code>{depth_km:.1f} km</code> {'⚠️ (سطحي/ضحل جداً)' if depth_km <= 5.0 else ''}",
            f"التصنيف الخام للمرصد: <code>{event_type.upper()}</code>",
            f"النطاق المرجعي: <code>{place}</code>",
            f"وقت التسجيل الزلزالي: <code>{event_dt}</code>"
        ]

        usgs_url = props.get("url", "https://earthquake.usgs.gov")
        await send_telegram_alert(
            client=client,
            severity=severity,
            category="SEISMIC / BLAST SIGNATURE",
            title=title,
            sector_name=sector_name,
            lat=lat,
            lon=lon,
            telemetry_lines=telemetry,
            extra_links=[("📊 التقرير الموجي الخام (USGS)", usgs_url)]
        )


# ==========================================
# COLLECTOR 3: NGA NAVIGATIONAL & MISSILE WARNINGS
# ==========================================
def parse_nga_coordinates(text: str) -> Optional[Tuple[float, float]]:
    """Extracts the first Deg-Min(-Sec) coordinate pair from raw NGA warning text."""
    pattern = r"(\d{2})-(\d{2})(?:\.(\d+))?([NS])\s+(\d{3}|\d{2})-(\d{2})(?:\.(\d+))?([EW])"
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        lat_deg = float(match.group(1))
        lat_min = float(match.group(2))
        lat_dec = float(f"0.{match.group(3)}") if match.group(3) else 0.0
        lat = lat_deg + (lat_min + lat_dec) / 60.0
        if match.group(4) == "S":
            lat = -lat

        lon_deg = float(match.group(5))
        lon_min = float(match.group(6))
        lon_dec = float(f"0.{match.group(7)}") if match.group(7) else 0.0
        lon = lon_deg + (lon_min + lon_dec) / 60.0
        if match.group(8) == "W":
            lon = -lon

        return round(lat, 4), round(lon, 4)
    except Exception:
        return None


async def poll_nga_warnings(client: httpx.AsyncClient):
    """
    Polls the US National Geospatial-Intelligence Agency (NGA) Maritime Safety Broadcasts
    for active missile launches, hazardous military operations, and airspace/sea closures
    in the Middle East (HYDROPAC / HYDROLANT / NAVAREA IX & III).
    """
    state.stats["nga_scans"] += 1
    url = "https://msi.nga.mil/api/publications/broadcast-warn?output=json&status=A"

    try:
        resp = await client.get(url, timeout=20.0)
        if resp.status_code != 200:
            return
        data = resp.json()
    except Exception as exc:
        logger.warning(f"NGA MSI feed error: {exc}")
        return

    warnings = data.get("broadcast-warn", [])
    # Process only the latest 60 active warnings to avoid startup flood
    for warn in warnings[:60]:
        msg_year = warn.get("msgYear")
        msg_num = warn.get("msgNumber")
        nav_area = str(warn.get("navArea", ""))
        subregion = str(warn.get("subregion", ""))
        text = str(warn.get("text", "")).upper()

        # Check if text contains critical military/missile keywords
        matched_kw = [kw for kw in config.NGA_CRITICAL_KEYWORDS if kw in text]
        if not matched_kw:
            continue

        # Check region relevance (Middle East is primarily subregion 62, 63, 26 or mentions Persian Gulf/Red Sea/Med)
        me_regions = ("PERSIAN GULF", "RED SEA", "ARABIAN SEA", "GULF OF OMAN", "GULF OF ADEN", "EASTERN MEDITERRANEAN", "STRAIT OF HORMUZ", "BAB EL MANDEB", "ISRAEL", "LEBANON", "SYRIA", "IRAN", "YEMEN", "CYPRUS")
        in_me_text = any(r in text for r in me_regions) or subregion in ("62", "63", "26")

        coords = parse_nga_coordinates(text)
        sector_name = None
        lat, lon = None, None

        if coords:
            lat, lon = coords
            sector_name = match_sector(lat, lon)

        if not (sector_name or in_me_text):
            continue

        if not sector_name:
            sector_name = "المجال البحري والجوي للشرق الأوسط (NAVAREA)"

        dedup_key = f"NGA_{nav_area}_{msg_year}_{msg_num}"
        if state.is_duplicate(dedup_key, ttl_seconds=86400 * 7):
            continue

        # Avoid flooding old warnings on initial startup by hashing into seen_events during first scan
        if state.stats["nga_scans"] == 1:
            continue

        severity = "CRITICAL" if ("MISSILE" in matched_kw or "ROCKET" in matched_kw) else "HIGH"
        title = f"إشعار عمليات عسكرية / إطلاق صواريخ (NGA {nav_area} {msg_num}/{msg_year})"

        clean_excerpt = " ".join(text.split())[:320] + "..."
        telemetry = [
            f"رقم التحذير العسكري: <code>{nav_area} {msg_num}/{msg_year}</code>",
            f"التصنيف المرصود: <code>{', '.join(matched_kw)}</code>",
            f"النص الخام للإشعار: <code>{clean_excerpt}</code>"
        ]

        await send_telegram_alert(
            client=client,
            severity=severity,
            category="NOTAM / NGA NAVIGATIONAL WARNING",
            title=title,
            sector_name=sector_name,
            lat=lat,
            lon=lon,
            telemetry_lines=telemetry,
            extra_links=[("⚓ المصدر الخام (NGA MSI)", "https://msi.nga.mil/NavWarnings")]
        )


# ==========================================
# BACKGROUND ORCHESTRATOR LOOP
# ==========================================
async def osint_scheduler_loop():
    logger.info("Starting Autonomous OSINT Ingestion Engine (Phase 1)...")
    headers = {
        "User-Agent": "OSINT-Telemetry-Monitor/1.0 (Strategic Research; +https://github.com)"
    }
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        last_aviation = 0.0
        last_seismic = 0.0
        last_nga = 0.0

        while True:
            now = time.time()
            try:
                if now - last_aviation >= config.POLL_INTERVAL_AVIATION:
                    await poll_military_aviation(client)
                    last_aviation = time.time()

                if now - last_seismic >= config.POLL_INTERVAL_SEISMIC:
                    await poll_seismic_anomalies(client)
                    last_seismic = time.time()

                if now - last_nga >= config.POLL_INTERVAL_NGA:
                    await poll_nga_warnings(client)
                    last_nga = time.time()

            except Exception as exc:
                logger.error(f"Scheduler loop error: {exc}")

            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(osint_scheduler_loop())
    yield
    task.cancel()


# ==========================================
# FASTAPI MICRO-SERVER & MINIMALIST UI (FOR RENDER)
# ==========================================
app = FastAPI(title="OSINT Raw Telemetry Engine", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {
        "status": "operational",
        "phase": "Phase 1 - Aviation, Seismic & NGA Warnings",
        "stats": state.stats,
        "cached_events": len(state.seen_events)
    }


@app.get("/", response_class=HTMLResponse)
async def status_dashboard():
    html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OSINT Telemetry Core</title>
    <style>
        :root {{
            --bg: #0a0a0c;
            --card: #121216;
            --border: #232329;
            --text: #f5f5f7;
            --muted: #86868b;
            --accent: #30d158;
            --danger: #ff453a;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 24px;
        }}
        .container {{
            width: 100%;
            max-width: 720px;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 32px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
        }}
        .title-group h1 {{
            font-size: 20px;
            font-weight: 600;
            letter-spacing: -0.3px;
        }}
        .title-group p {{
            color: var(--muted);
            font-size: 13px;
            margin-top: 4px;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(48, 209, 88, 0.12);
            color: var(--accent);
            padding: 6px 14px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
        }}
        .dot {{
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            box-shadow: 0 0 8px var(--accent);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .metric {{
            background: rgba(255,255,255,0.02);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 16px;
        }}
        .metric span {{
            display: block;
            color: var(--muted);
            font-size: 12px;
            margin-bottom: 8px;
        }}
        .metric strong {{
            font-size: 22px;
            font-weight: 600;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        }}
        .footer {{
            font-size: 12px;
            color: var(--muted);
            display: flex;
            justify-content: space-between;
            border-top: 1px solid var(--border);
            padding-top: 16px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title-group">
                <h1>محرك الرصد العسكري الخام (OSINT Core)</h1>
                <p>الشرق الأوسط وإيران — Phase 1 Telemetry Engine</p>
            </div>
            <div class="badge">
                <span class="dot"></span>
                ONLINE 24/7
            </div>
        </div>
        <div class="grid">
            <div class="metric">
                <span>التنبيهات الحرجة المرسلة</span>
                <strong>{state.stats["alerts_sent"]}</strong>
            </div>
            <div class="metric">
                <span>مسح الرادار الجوي (ADS-B)</span>
                <strong>{state.stats["aviation_scans"]}</strong>
            </div>
            <div class="metric">
                <span>فحص البصمات الزلزالية</span>
                <strong>{state.stats["seismic_scans"]}</strong>
            </div>
            <div class="metric">
                <span>فحص تحذيرات الصواريخ (NGA)</span>
                <strong>{state.stats["nga_scans"]}</strong>
            </div>
        </div>
        <div class="footer">
            <span>بدء التشغيل: {state.stats["started_at"]}</span>
            <span>الأحداث المحفوظة بالذاكرة: {len(state.seen_events)}</span>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=config.PORT, log_level="info")
