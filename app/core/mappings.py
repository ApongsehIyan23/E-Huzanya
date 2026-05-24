"""
E-Huzanya Gateway — Taxonomy Mapping Dictionaries
===================================================

BIDIRECTIONAL CONSTANCY RULE:
    RNP_code  →  Canonical_code  ←  SAMU_code
    
    The canonical code is the SINGLE SOURCE OF TRUTH.
    Both agency-native codes map TO it.
    Both destination payloads are derived FROM it.
    
    If RNP reports an RTA and SAMU reports the same RTA,
    the CIR incident record is identical — only source metadata differs.
    
These dictionaries are intentionally hardcoded for the MVP.
Production would store them in a database table for runtime updates.
"""

# ============================================================================
# INCIDENT TYPE MAPPINGS
# ============================================================================

# RNP native code → EHZ canonical code
RNP_TO_CANONICAL_INCIDENT = {
    "RTA":                "ROAD_TRAFFIC_ACCIDENT",
    "RTA_HAZMAT":         "RTA_WITH_HAZMAT",
    "ARMED_ROBBERY":      "ACTIVE_ARMED_THREAT",
    "ASSAULT":            "VIOLENT_INCIDENT",
    "ASSAULT_MASS":       "MASS_VIOLENCE_INCIDENT",
    "FIRE_STRUCTURAL":    "STRUCTURAL_FIRE",
    "FIRE_INDUSTRIAL":    "INDUSTRIAL_FIRE",
    "HAZMAT_CHEMICAL":    "HAZMAT_RELEASE",
    "HAZMAT_FUEL":        "HAZMAT_FUEL_SPILL",
    "STAMPEDE":           "CROWD_CRUSH",
    "EXPLOSION":          "EXPLOSIVE_EVENT",
    "DROWNING":           "WATER_RESCUE",
    "DOMESTIC_VIOLENCE":  "GBV_INCIDENT",
    "PUBLIC_DISTURBANCE": "PUBLIC_ORDER",
    "LANDSLIDE":          "NATURAL_DISASTER",
}

# SAMU native code → EHZ canonical code
SAMU_TO_CANONICAL_INCIDENT = {
    "TRAUMA_RTA":             "ROAD_TRAFFIC_ACCIDENT",
    "TRAUMA_RTA_CHEMICAL":    "RTA_WITH_HAZMAT",
    "TRAUMA_BALLISTIC":       "ACTIVE_ARMED_THREAT",
    "TRAUMA_ASSAULT":         "VIOLENT_INCIDENT",
    "TRAUMA_MASS_CASUALTY":   "MASS_VIOLENCE_INCIDENT",
    "BURN_MASS_CASUALTY":     "STRUCTURAL_FIRE",
    "BURN_CHEMICAL":          "INDUSTRIAL_FIRE",
    "EXPOSURE_CHEMICAL":      "HAZMAT_RELEASE",
    "EXPOSURE_INHALATION":    "HAZMAT_FUEL_SPILL",
    "TRAUMA_CRUSH":           "CROWD_CRUSH",
    "TRAUMA_BLAST":           "EXPLOSIVE_EVENT",
    "TRAUMA_DROWNING":        "WATER_RESCUE",
    "MEDICAL_CARDIAC":        "MEDICAL_EMERGENCY",
    "MEDICAL_OBSTETRIC":      "MEDICAL_EMERGENCY",
}

# EHZ canonical code → RNP native code (for destination payloads)
CANONICAL_TO_RNP_INCIDENT = {v: k for k, v in RNP_TO_CANONICAL_INCIDENT.items()}
# Handle canonical codes that have no RNP equivalent
CANONICAL_TO_RNP_INCIDENT["MEDICAL_EMERGENCY"] = "MEDICAL_ASSIST"

# EHZ canonical code → SAMU native code (for destination payloads)
CANONICAL_TO_SAMU_INCIDENT = {v: k for k, v in SAMU_TO_CANONICAL_INCIDENT.items()}
# Handle canonical codes that have no SAMU equivalent
CANONICAL_TO_SAMU_INCIDENT["PUBLIC_ORDER"] = "NO_MEDICAL_RELEVANCE"
CANONICAL_TO_SAMU_INCIDENT["GBV_INCIDENT"] = "TRAUMA_ASSAULT"
CANONICAL_TO_SAMU_INCIDENT["NATURAL_DISASTER"] = "TRAUMA_MASS_CASUALTY"


# ============================================================================
# SEVERITY MAPPINGS
# ============================================================================

RNP_TO_CANONICAL_SEVERITY = {
    "CRITICAL":   "P1",
    "URGENT":     "P2",
    "NON_URGENT": "P3",
}

SAMU_TO_CANONICAL_SEVERITY = {
    "SEVERE":   "P1",
    "MODERATE": "P2",
    "MINOR":    "P3",
}

CANONICAL_TO_RNP_SEVERITY = {v: k for k, v in RNP_TO_CANONICAL_SEVERITY.items()}
CANONICAL_TO_SAMU_SEVERITY = {v: k for k, v in SAMU_TO_CANONICAL_SEVERITY.items()}


# ============================================================================
# SCENE STATUS MAPPINGS
# ============================================================================

RNP_TO_CANONICAL_SCENE_STATUS = {
    "ACTIVE_THREAT":    "SCENE_NOT_SECURE",
    "PERIMETER_SET":    "SCENE_CONTAINED",
    "AREA_CLEARED":     "SCENE_SECURE",
    "HAZMAT_ACTIVE":    "HAZARD_ACTIVE",
    "HAZMAT_CONTAINED": "HAZARD_CONTAINED",
}

SAMU_TO_CANONICAL_SCENE_STATUS = {
    "ACCESS_DENIED":        "SCENE_NOT_SECURE",
    "ACCESS_STAGING_ONLY":  "SCENE_CONTAINED",
    "ACCESS_AUTHORIZED":    "SCENE_SECURE",
    "ACCESS_DENIED_HAZMAT": "HAZARD_ACTIVE",
    "ACCESS_WITH_PPE":      "HAZARD_CONTAINED",
}

CANONICAL_TO_RNP_SCENE_STATUS = {v: k for k, v in RNP_TO_CANONICAL_SCENE_STATUS.items()}
CANONICAL_TO_SAMU_SCENE_STATUS = {v: k for k, v in SAMU_TO_CANONICAL_SCENE_STATUS.items()}


# ============================================================================
# CROSS-AGENCY REQUEST TYPE MAPPINGS
# ============================================================================

RNP_TO_CANONICAL_REQUEST = {
    "REQUEST_AMBULANCE":       "MEDICAL_DISPATCH",
    "REQUEST_HAZMAT_MEDICAL":  "MEDICAL_DISPATCH_HAZMAT",
    "REQUEST_FIRE":            "FIRE_DISPATCH",
}

SAMU_TO_CANONICAL_REQUEST = {
    "REQUEST_SCENE_SECURITY":       "POLICE_DISPATCH",
    "REQUEST_ACTIVE_THREAT_CLEAR":  "POLICE_DISPATCH_URGENT",
    "REQUEST_FIRE":                 "FIRE_DISPATCH",
}

CANONICAL_TO_RNP_REQUEST = {
    "POLICE_DISPATCH":        "DISPATCH_PATROL",
    "POLICE_DISPATCH_URGENT": "DISPATCH_TACTICAL",
    "FIRE_DISPATCH":          "DISPATCH_FIRE",
    "MEDICAL_DISPATCH":       "REQUEST_AMBULANCE",
    "MEDICAL_DISPATCH_HAZMAT":"REQUEST_HAZMAT_MEDICAL",
}

CANONICAL_TO_SAMU_REQUEST = {
    "MEDICAL_DISPATCH":       "AMBULANCE_DISPATCH",
    "MEDICAL_DISPATCH_HAZMAT":"AMBULANCE_DISPATCH_HAZMAT",
    "FIRE_DISPATCH":          "REQUEST_FIRE",
    "POLICE_DISPATCH":        "REQUEST_SCENE_SECURITY",
    "POLICE_DISPATCH_URGENT": "REQUEST_ACTIVE_THREAT_CLEAR",
}


# ============================================================================
# RESOURCE TYPE MAPPINGS (for WebSocket stream icon rendering)
# ============================================================================

RESOURCE_DISPLAY = {
    # RNP resource types
    "TRAFFIC_PATROL":   {"icon": "police_car",   "color": "#1D3557"},
    "GENERAL_PATROL":   {"icon": "police_car",   "color": "#1D3557"},
    "TACTICAL_UNIT":    {"icon": "shield",       "color": "#E63946"},
    # SAMU resource types
    "AMBULANCE":        {"icon": "ambulance",    "color": "#E63946"},
    "AMBULANCE_ALS":    {"icon": "ambulance",    "color": "#E63946"},
    "AMBULANCE_BLS":    {"icon": "ambulance",    "color": "#F4A261"},
    # Fire resource types
    "FIRE_ENGINE":      {"icon": "fire_truck",   "color": "#E76F51"},
    "FIRE_ENGINE_HAZMAT":{"icon": "fire_truck",  "color": "#9B2226"},
}


# ============================================================================
# CLINICAL FIREWALL RULES
# ============================================================================
# These define which restricted_compartments fields are STRIPPED
# when translating to each destination agency.

FIREWALL_RULES = {
    "SAMU": {
        # When destination is SAMU, strip all RNP restricted data
        "strip_compartments": ["rnp_restricted", "minema_restricted"],
        # But KEEP these fields even though they originate from RNP
        # (because they are scene safety fields that protect paramedic lives)
        "always_share": [
            "weapons_present",
            "weapon_category",
            "hazards",
            "access_restriction",
            "hazard_zone",
            "scene_status",
            "clearance_handshake",
            "delayed_casualty_risk",
        ],
    },
    "RNP": {
        # When destination is RNP, strip all SAMU restricted data
        "strip_compartments": ["samu_restricted", "minema_restricted"],
        "always_share": [
            "weapons_present",
            "weapon_category",
            "hazards",
            "access_restriction",
            "hazard_zone",
            "scene_status",
            "clearance_handshake",
            "delayed_casualty_risk",
        ],
    },
}


# ============================================================================
# LOOKUP HELPERS
# ============================================================================

def translate_incident_type(source_agency: str, native_code: str) -> str:
    """Translate an agency-native incident type to canonical code."""
    if source_agency == "RNP":
        return RNP_TO_CANONICAL_INCIDENT.get(native_code, "UNKNOWN_INCIDENT")
    elif source_agency == "SAMU":
        return SAMU_TO_CANONICAL_INCIDENT.get(native_code, "UNKNOWN_INCIDENT")
    return "UNKNOWN_INCIDENT"


def translate_severity(source_agency: str, native_code: str) -> str:
    """Translate an agency-native severity to canonical code."""
    if source_agency == "RNP":
        return RNP_TO_CANONICAL_SEVERITY.get(native_code, "P2")
    elif source_agency == "SAMU":
        return SAMU_TO_CANONICAL_SEVERITY.get(native_code, "P2")
    return "P2"


def translate_scene_status(source_agency: str, native_code: str) -> str:
    """Translate an agency-native scene status to canonical code."""
    if source_agency == "RNP":
        return RNP_TO_CANONICAL_SCENE_STATUS.get(native_code, "SCENE_NOT_SECURE")
    elif source_agency == "SAMU":
        return SAMU_TO_CANONICAL_SCENE_STATUS.get(native_code, "SCENE_NOT_SECURE")
    return "SCENE_NOT_SECURE"


def translate_to_destination(dest_agency: str, canonical_code: str, field_type: str) -> str:
    """Translate a canonical code to a destination agency's native code.
    
    Args:
        dest_agency: "RNP" or "SAMU"
        canonical_code: The EHZ canonical code
        field_type: "incident", "severity", "scene_status", or "request"
    """
    lookup = {
        ("RNP", "incident"):      CANONICAL_TO_RNP_INCIDENT,
        ("RNP", "severity"):      CANONICAL_TO_RNP_SEVERITY,
        ("RNP", "scene_status"):  CANONICAL_TO_RNP_SCENE_STATUS,
        ("RNP", "request"):       CANONICAL_TO_RNP_REQUEST,
        ("SAMU", "incident"):     CANONICAL_TO_SAMU_INCIDENT,
        ("SAMU", "severity"):     CANONICAL_TO_SAMU_SEVERITY,
        ("SAMU", "scene_status"): CANONICAL_TO_SAMU_SCENE_STATUS,
        ("SAMU", "request"):      CANONICAL_TO_SAMU_REQUEST,
    }
    mapping = lookup.get((dest_agency, field_type), {})
    return mapping.get(canonical_code, f"UNMAPPED_{canonical_code}")


def get_resource_display(resource_type: str) -> dict:
    """Get icon and color for a resource type for map rendering."""
    return RESOURCE_DISPLAY.get(resource_type, {"icon": "default", "color": "#6C757D"})
