"""
E-Huzanya Gateway — Core Configuration
Database, auth, and environment settings.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATABASE_URL = f"sqlite+aiosqlite:///{BASE_DIR / 'ehuzanya.db'}"

# ---------------------------------------------------------------------------
# API Authentication (MVP: static API keys per agency)
# Production would use OAuth 2.0 Client Credentials flow.
# ---------------------------------------------------------------------------
API_KEYS = {
    "rnp_dispatch_key_2026": {
        "agency": "RNP",
        "scopes": ["incidents:write", "incidents:read", "resources:publish", "resources:stream"],
    },
    "samu_dispatch_key_2026": {
        "agency": "SAMU",
        "scopes": ["incidents:write", "incidents:read", "resources:publish", "resources:stream"],
    },
    "ehz_admin_key_2026": {
        "agency": "EHZ_ADMIN",
        "scopes": ["admin:mappings", "incidents:read", "incidents:write", "resources:stream"],
    },
    # Simulator key — used by the frontend demo to act as either agency
    "simulator_key_2026": {
        "agency": "SIMULATOR",
        "scopes": ["incidents:write", "incidents:read", "resources:publish", "resources:stream"],
    },
}

# ---------------------------------------------------------------------------
# Webhook destinations (MVP: placeholder URLs)
# In production these would be real agency endpoints.
# ---------------------------------------------------------------------------
WEBHOOK_ENDPOINTS = {
    "SAMU": "http://localhost:8000/api/v1/simulator/samu-inbox",
    "RNP": "http://localhost:8000/api/v1/simulator/rnp-inbox",
}

# ---------------------------------------------------------------------------
# Geofencing
# ---------------------------------------------------------------------------
GEOFENCE_APPROACH_THRESHOLD_METERS = 200  # Warn when resource is this close to boundary

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
WS_HEARTBEAT_INTERVAL_SECONDS = 30
