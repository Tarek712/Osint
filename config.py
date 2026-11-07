import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
# TELEGRAM & SERVER CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PORT = int(os.getenv("PORT", 8000))

# Polling intervals (in seconds)
POLL_INTERVAL_AVIATION = int(os.getenv("POLL_INTERVAL_AVIATION", 180))  # 3 minutes
POLL_INTERVAL_SEISMIC = int(os.getenv("POLL_INTERVAL_SEISMIC", 180))    # 3 minutes
POLL_INTERVAL_NGA = int(os.getenv("POLL_INTERVAL_NGA", 600))            # 10 minutes

# Strict Timestamp Window (minutes) to prevent duplicate alerts on Render restarts
STARTUP_LOOKBACK_MINUTES = int(os.getenv("STARTUP_LOOKBACK_MINUTES", 20))

# ==========================================
# GEOSPATIAL BOUNDING BOXES (MIDDLE EAST & IRAN)
# ==========================================
SECTORS = {
    "IRAN_AND_GULF": {
        "name_ar": "العمق الإيراني والخليج العربي",
        "min_lat": 24.0, "max_lat": 39.8,
        "min_lon": 44.0, "max_lon": 63.3,
        "center_lat": 32.0, "center_lon": 53.0,
        "radius_nm": 250
    },
    "LEVANT_AND_EAST_MED": {
        "name_ar": "بلاد الشام وشرق المتوسط",
        "min_lat": 31.0, "max_lat": 37.5,
        "min_lon": 32.0, "max_lon": 42.5,
        "center_lat": 34.2, "center_lon": 36.5,
        "radius_nm": 250
    },
    "IRAQ_CORRIDOR": {
        "name_ar": "الممر الجوي العراقي والجزيرة",
        "min_lat": 29.0, "max_lat": 37.5,
        "min_lon": 38.5, "max_lon": 48.5,
        "center_lat": 33.3, "center_lon": 43.8,
        "radius_nm": 250
    },
    "RED_SEA_BAB_EL_MANDEB": {
        "name_ar": "البحر الأحمر وباب المندب",
        "min_lat": 11.5, "max_lat": 22.0,
        "min_lon": 37.0, "max_lon": 46.0,
        "center_lat": 15.5, "center_lon": 42.0,
        "radius_nm": 250
    }
}

# ==========================================
# STRATEGIC SENSITIVE SITES (FOR PROXIMITY ALERTS)
# ==========================================
STRATEGIC_SITES = [
    {"name": "منشأة نطنز النووية (Natanz)", "lat": 33.7244, "lon": 51.7256, "radius_km": 35},
    {"name": "منشأة فوردو النووية (Fordow)", "lat": 34.8845, "lon": 50.9958, "radius_km": 35},
    {"name": "مجمع بارشين العسكري (Parchin)", "lat": 35.5200, "lon": 51.7700, "radius_km": 30},
    {"name": "قاعدة أصفهان الجوية والمركز النووي", "lat": 32.7508, "lon": 51.8613, "radius_km": 40},
    {"name": "محطة بوشهر النووية (Bushehr)", "lat": 28.8294, "lon": 50.8856, "radius_km": 40},
    {"name": "قاعدة بندر عباس (مضيق هرمز)", "lat": 27.2181, "lon": 56.3778, "radius_km": 45},
    {"name": "قاعدة أكروتيري الجوية (قبرص)", "lat": 34.5904, "lon": 32.9879, "radius_km": 35},
    {"name": "قاعدة العديد الجوية (قطر)", "lat": 25.1173, "lon": 51.3150, "radius_km": 35},
    {"name": "قاعدة الظفرة الجوية (الإمارات)", "lat": 24.2483, "lon": 54.5477, "radius_km": 35},
    {"name": "قاعدة عين الأسد (العراق)", "lat": 33.7856, "lon": 42.4411, "radius_km": 30},
    {"name": "قاعدة حميميم الجوية (سوريا)", "lat": 35.4008, "lon": 35.9486, "radius_km": 30}
]

# ==========================================
# HIGH-VALUE MILITARY AIRCRAFT FILTERS
# ==========================================
HIGH_VALUE_AIRCRAFT_TYPES = {
    # ISR & Command (استطلاع وحرب إلكترونية وقيادة)
    "R135": "RC-135 Rivet Joint (استطلاع إلكتروني استراتيجي)",
    "E3CF": "E-3 Sentry AWACS (إنذار مبكر وقيادة جوية)",
    "E3TF": "E-3 Sentry AWACS (إنذار مبكر وقيادة جوية)",
    "E2": "E-2 Hawkeye (إنذار مبكر بحري/جوي)",
    "GLF5": "Gulfstream G550 Nachshon/Eitam (استطلاع وإنذار مبكر)",
    "GLEX": "Bombardier Global Express E-11A BACN (اتصالات قتالية)",
    "P8": "P-8A Poseidon (استطلاع بحري ومضاد للغواصات)",
    "EP3": "EP-3E Aries II (استخبارات إشارات SIGINT)",
    "RQ4": "RQ-4 Global Hawk / Triton (مسيرة استطلاع استراتيجي)",
    "B350": "Beechcraft RC-12 / King Air ISR (استطلاع تكتيكي)",
    # Aerial Refueling (التزود بالوقود جواً - مؤشر عمليات قتالية)
    "K35R": "KC-135R Stratotanker (تزود بالوقود جواً)",
    "KC46": "KC-46A Pegasus (تزود بالوقود جواً)",
    "A332": "Airbus A330 MRTT (تزود بالوقود جواً)",
    "KC30": "KC-30A MRTT (تزود بالوقود جواً)",
    # Strategic Bombers & Heavy Military Transport (قاذفات وشحن عسكري ثقيل)
    "B52": "B-52H Stratofortress (قاذفة استراتيجية)",
    "B1": "B-1B Lancer (قاذفة استراتيجية)",
    "IL76": "Ilyushin Il-76 (شحن عسكري استراتيجي)",
    "A124": "Antonov An-124 Ruslan (شحن عسكري ثقيل)",
    "C17": "Boeing C-17A Globemaster III (نقل عسكري استراتيجي)",
    "C5M": "Lockheed C-5M Super Galaxy (نقل عسكري استراتيجي)",
    "B742": "Boeing 747-200F (شحن عسكري / إيراني خاص)"
}

MILITARY_CALLSIGN_PREFIXES = (
    "FORTE", "HOMER", "JAKE", "OLIVE", "NCHO", "LAGR", "GOLD", "PYTHON",
    "ETHYL", "NATO", "RRR", "RCH", "ASCOT", "IAM", "FAF", "GAF",
    "IRGC", "IRI", "POUYA", "FARS", "QESHM", "SYR", "RF", "RFF"
)

# ==========================================
# NGA NAVIGATIONAL WARNING KEYWORDS
# ==========================================
NGA_CRITICAL_KEYWORDS = [
    "MISSILE", "ROCKET", "HAZARDOUS OPERATIONS", "FIRING EXERCISE",
    "GUNNERY", "LIVE FIRE", "SPACE DEBRIS", "AIR DEFENSE",
    "MILITARY OPERATIONS", "PROHIBITED AREA", "DANGER AREA"
]
