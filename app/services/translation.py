"""
E-Huzanya Gateway — Translation Engine
========================================

The intellectual core of E-Huzanya. Three-stage pipeline:

    1. ingest_to_canonical()  — Source payload → CIR
    2. apply_firewall()       — Strip restricted compartments
    3. canonical_to_destination() — CIR → Destination payload

These are pure functions with no side effects (no DB writes, no network calls).
The API layer calls them and handles persistence/routing separately.

BIDIRECTIONAL CONSTANCY INVARIANT:
    For any real-world event, regardless of which agency reports it,
    the CIR produced by ingest_to_canonical() must be structurally identical.
    Only source_agency and source_incident_ref differ.
"""

from datetime import datetime, timezone
from typing import Optional
import hashlib

from app.core.mappings import (
    translate_incident_type,
    translate_severity,
    translate_scene_status,
    translate_to_destination,
    get_resource_display,
    FIREWALL_RULES,
)


# ============================================================================
# ID GENERATION
# ============================================================================

def generate_ehz_id(timestamp: datetime, source_agency: str) -> str:
    """Generate a unique E-Huzanya incident ID.
    
    Format: EHZ-YYYYMMDD-HHMMSS-XXXX
    where XXXX is a 4-char hash derived from timestamp + agency for uniqueness.
    """
    ts_str = timestamp.strftime("%Y%m%d-%H%M%S")
    hash_input = f"{ts_str}-{source_agency}-{datetime.now(timezone.utc).timestamp()}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:4].upper()
    return f"EHZ-{ts_str}-{short_hash}"


def generate_request_id(timestamp: datetime, sequence: int = 1) -> str:
    """Generate a unique cross-agency request ID."""
    ts_str = timestamp.strftime("%Y%m%d-%H%M%S")
    return f"REQ-{ts_str}-{sequence:03d}"


# ============================================================================
# STAGE 1: INGEST TO CANONICAL
# ============================================================================

def ingest_rnp_to_canonical(payload: dict) -> dict:
    """Translate an RNP native payload into the Canonical Intermediate Representation.
    
    Args:
        payload: Raw RNP JSON payload (validated by RNPSourcePayload schema)
        
    Returns:
        A dict matching the CanonicalIncident schema
    """
    now = datetime.now(timezone.utc)
    call_ts = payload.get("call_timestamp", now.isoformat())
    if isinstance(call_ts, str):
        call_ts = datetime.fromisoformat(call_ts)

    ehz_id = generate_ehz_id(call_ts, "RNP")
    details = payload.get("incident_details", {})
    location = payload.get("location", {})
    coords = location.get("coordinates", {})

    # Build canonical type and severity
    canonical_type = translate_incident_type("RNP", payload.get("incident_type", ""))
    canonical_severity = translate_severity("RNP", payload.get("severity", ""))

    # Determine scene status
    scene_status = "SCENE_NOT_SECURE"
    if details.get("weapons_involved"):
        scene_status = "SCENE_NOT_SECURE"
    elif details.get("chemical_hazard") and details["chemical_hazard"].get("identified"):
        scene_status = "HAZARD_ACTIVE"
    elif details.get("hazards_reported"):
        scene_status = "SCENE_NOT_SECURE"

    # Build situation object
    situation = {
        "hazards": details.get("hazards_reported", []),
        "weapons_present": details.get("weapons_involved", False),
        "weapon_category": (details.get("weapon_types", [None])[0] 
                           if details.get("weapon_types") else None),
        "fire_active": details.get("fire_active", False),
        "scene_status": scene_status,
        "vehicle_count": details.get("vehicle_count"),
        "vehicle_types": details.get("vehicle_types"),
    }

    # Add fuel spill if present
    if details.get("fuel_type"):
        situation["fuel_spill"] = {
            "active": True,
            "fuel_type": details["fuel_type"],
            "flow_direction": details.get("fuel_flow_direction"),
            "ignition_risk": "HIGH" if details.get("fuel_type") == "DIESEL" else "MODERATE",
        }

    # Add chemical hazard if present
    chem = details.get("chemical_hazard")
    if chem and chem.get("identified"):
        situation["chemical_hazard"] = {
            "agents_identified": chem.get("agents", []),
            "toxic_gas": chem.get("toxic_gas_detected"),
            "exposure_route": "INHALATION" if chem.get("toxic_gas_detected") else None,
            "visible_indicators": chem.get("visible_indicators"),
        }

    # Add multi-zone data if present
    zones_data = payload.get("incident_zones")
    if zones_data:
        situation["multi_zone"] = True
        situation["zones"] = [
            {
                "zone_id": z["zone_id"],
                "description": z["description"],
                "center": z["center"],
                "status": _map_threat_to_scene_status(z.get("threat_level", "")),
                "casualties_present": z.get("casualties_present", 0),
                "threat_level": z.get("threat_level", "NONE"),
            }
            for z in zones_data
        ]

    # Build agencies involved
    rnp_response = payload.get("rnp_response", {})
    rnp_units = rnp_response.get("units_dispatched", [])
    agencies_involved = [
        {
            "agency": "RNP",
            "status": "DISPATCHED",
            "resources": [
                {
                    "unit_ref": u.get("unit_id", ""),
                    "type": u.get("unit_type", ""),
                    "eta_minutes": u.get("eta_minutes"),
                    "status": u.get("status", "DISPATCHED"),
                }
                for u in rnp_units
            ],
        }
    ]

    # Add fire brigade if mentioned
    if rnp_response.get("fire_brigade_notified"):
        agencies_involved.append({
            "agency": "FIRE",
            "status": "DISPATCHED",
            "resources": [
                {
                    "unit_ref": "FIRE-KGL-UNIT-02",
                    "type": "FIRE_ENGINE_HAZMAT" if chem else "FIRE_ENGINE",
                    "eta_minutes": rnp_response.get("fire_brigade_eta"),
                    "status": "DISPATCHED",
                }
            ],
        })

    # Build cross-agency request
    req = payload.get("cross_agency_request", {})
    cross_agency_requests = []
    if req:
        cross_agency_requests.append({
            "request_id": generate_request_id(call_ts),
            "target_agency": req.get("agency_needed", ""),
            "request_type": translate_incident_type("RNP", req.get("request_type", ""))
                           if req.get("request_type", "").startswith("REQUEST_")
                           else req.get("request_type", ""),
            "urgency": req.get("urgency", "IMMEDIATE"),
            "requested_units": req.get("requested_units"),
            "status": "PENDING",
            "reason": req.get("reason", ""),
        })
        # Use the mapping for request types
        request_type_raw = req.get("request_type", "")
        from app.core.mappings import RNP_TO_CANONICAL_REQUEST
        canonical_request = RNP_TO_CANONICAL_REQUEST.get(request_type_raw, request_type_raw)
        cross_agency_requests[0]["request_type"] = canonical_request

    # Build the CIR
    canonical = {
        "ehz_incident_id": ehz_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "version": 1,
        "source_agency": "RNP",
        "source_incident_ref": payload.get("rnp_incident_id", ""),
        "linked_refs": [],

        "canonical_type": canonical_type,
        "canonical_severity": canonical_severity,
        "mci_declared": details.get("estimated_casualties", 0) >= 3,
        "estimated_casualties": details.get("estimated_casualties", 0),

        "location": {
            "latitude": coords.get("latitude", 0),
            "longitude": coords.get("longitude", 0),
            "address_text": location.get("description", ""),
            "road_name": location.get("road_name"),
            "venue_type": location.get("venue_type"),
            "admin_hierarchy": {
                "sector": location.get("sector", ""),
                "district": location.get("district", ""),
                "province": location.get("province", "Kigali"),
            },
            "confidence": location.get("location_confidence", "UNKNOWN"),
            "civilian_density_estimate": location.get("estimated_civilian_density"),
        },

        "situation": situation,
        "access_restriction": None,
        "hazard_zone": None,

        "agencies_involved": agencies_involved,
        "cross_agency_requests": cross_agency_requests,

        "clearance_handshake": {
            "status": "NOT_INITIATED",
            "declared_by": None,
            "declared_at": None,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "scope": None,
        },

        "delayed_casualty_risk": None,

        "restricted_compartments": {
            "rnp_restricted": payload.get("rnp_restricted"),
            "samu_restricted": None,
            "minema_restricted": None,
        },

        "timeline": [
            {
                "timestamp": now.isoformat(),
                "event": "INCIDENT_CREATED",
                "actor": "RNP",
                "detail": f"{canonical_type} reported via 112",
            }
        ],
    }

    # Build access restriction for armed threat scenarios
    if details.get("weapons_involved") and details.get("suspects"):
        canonical["access_restriction"] = _build_access_restriction_from_rnp(
            payload, coords
        )

    # Build hazard zone for chemical/HAZMAT scenarios
    if chem and chem.get("identified"):
        canonical["hazard_zone"] = _build_hazard_zone_from_rnp(payload, coords, chem)
        canonical["delayed_casualty_risk"] = _build_delayed_risk(chem)

    return canonical


def ingest_samu_to_canonical(payload: dict) -> dict:
    """Translate a SAMU/e-Banguka native payload into the CIR.
    
    Args:
        payload: Raw SAMU JSON payload (validated by SAMUSourcePayload schema)
        
    Returns:
        A dict matching the CanonicalIncident schema
    """
    now = datetime.now(timezone.utc)
    call_ts = payload.get("call_received", now.isoformat())
    if isinstance(call_ts, str):
        call_ts = datetime.fromisoformat(call_ts)

    ehz_id = generate_ehz_id(call_ts, "SAMU")
    location = payload.get("scene_location", {})
    clinical = payload.get("clinical_summary", {})

    # Build canonical type and severity
    canonical_type = translate_incident_type("SAMU", payload.get("medical_category", ""))
    canonical_severity = translate_severity("SAMU", payload.get("triage_priority", ""))

    # Check if violence-related (affects canonical type)
    special = clinical.get("special_considerations", "")
    if "gunfire" in special.lower() or "weapon" in special.lower():
        # Upgrade to violence-related canonical type if applicable
        if canonical_type in ("MASS_VIOLENCE_INCIDENT", "VIOLENT_INCIDENT"):
            pass  # Already correct
        elif canonical_type == "CROWD_CRUSH":
            canonical_type = "MASS_VIOLENCE_INCIDENT"

    # Determine scene status
    scene_status = "SCENE_NOT_SECURE"
    if "gunfire" in special.lower() or "weapon" in special.lower():
        scene_status = "SCENE_NOT_SECURE"
    
    samu_response = payload.get("samu_response", {})
    ambulances = samu_response.get("ambulances_dispatched", [])
    
    # Check for holding status
    any_holding = any(
        a.get("status") == "EN_ROUTE_HOLDING" or a.get("hold_reason")
        for a in ambulances
    )

    # Build situation
    situation = {
        "hazards": [],
        "weapons_present": "gunfire" in special.lower() or "weapon" in special.lower(),
        "weapon_category": None,
        "fire_active": False,
        "scene_status": scene_status,
    }

    # Infer hazards from clinical data
    if "gunfire" in special.lower():
        situation["hazards"].append("active_gunfire")
    if clinical.get("mechanism_of_injury") == "CRUSH_STAMPEDE":
        situation["hazards"].append("stampede_in_progress")
        situation["hazards"].append("crowd_panic")

    # Build agencies involved
    agencies_involved = [
        {
            "agency": "SAMU",
            "status": "DISPATCHED",
            "resources": [
                {
                    "unit_ref": a.get("unit_id", ""),
                    "type": "AMBULANCE_ALS" if a.get("crew_type") == "NURSE_ANESTHETIST" else "AMBULANCE_BLS",
                    "eta_minutes": a.get("eta_minutes"),
                    "status": a.get("status", "EN_ROUTE"),
                }
                for a in ambulances
            ],
        }
    ]

    # Build cross-agency request
    req = payload.get("cross_agency_request", {})
    cross_agency_requests = []
    if req:
        from app.core.mappings import SAMU_TO_CANONICAL_REQUEST
        request_type_raw = req.get("request_type", "")
        canonical_request = SAMU_TO_CANONICAL_REQUEST.get(request_type_raw, request_type_raw)
        cross_agency_requests.append({
            "request_id": generate_request_id(call_ts),
            "target_agency": req.get("agency_needed", ""),
            "request_type": canonical_request,
            "urgency": req.get("urgency", "IMMEDIATE"),
            "status": "PENDING",
            "reason": req.get("reason", ""),
        })

    canonical = {
        "ehz_incident_id": ehz_id,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "version": 1,
        "source_agency": "SAMU",
        "source_incident_ref": payload.get("ebanguka_case_id", ""),
        "linked_refs": [],

        "canonical_type": canonical_type,
        "canonical_severity": canonical_severity,
        "mci_declared": payload.get("patient_count", 0) >= 3,
        "estimated_casualties": payload.get("patient_count", 0),

        "location": {
            "latitude": location.get("lat", 0),
            "longitude": location.get("lng", 0),
            "address_text": location.get("description", ""),
            "road_name": None,
            "venue_type": None,
            "admin_hierarchy": {
                "sector": location.get("sector", ""),
                "district": location.get("district", ""),
                "province": "Kigali",
            },
            "confidence": "CALLER_DESCRIBED",
        },

        "situation": situation,
        "access_restriction": None,
        "hazard_zone": None,

        "agencies_involved": agencies_involved,
        "cross_agency_requests": cross_agency_requests,

        "clearance_handshake": {
            "status": "NOT_INITIATED",
            "declared_by": None,
            "declared_at": None,
            "acknowledged_by": None,
            "acknowledged_at": None,
            "scope": None,
        },

        "delayed_casualty_risk": None,

        "restricted_compartments": {
            "rnp_restricted": None,
            "samu_restricted": payload.get("samu_restricted"),
            "minema_restricted": None,
        },

        "timeline": [
            {
                "timestamp": now.isoformat(),
                "event": "INCIDENT_CREATED",
                "actor": "SAMU",
                "detail": f"{canonical_type} reported via 912",
            }
        ],
    }

    return canonical


def ingest_to_canonical(source_agency: str, payload: dict) -> dict:
    """Route to the correct ingestion function based on source agency.
    
    This is the main entry point for Stage 1 of the pipeline.
    """
    if source_agency == "RNP":
        return ingest_rnp_to_canonical(payload)
    elif source_agency == "SAMU":
        return ingest_samu_to_canonical(payload)
    else:
        raise ValueError(f"Unknown source agency: {source_agency}")


# ============================================================================
# STAGE 2: CLINICAL FIREWALL
# ============================================================================

def apply_firewall(canonical: dict, destination_agency: str) -> dict:
    """Apply the Clinical Firewall to a canonical record.
    
    Strips restricted compartments that the destination agency
    must not see. Returns a NEW dict (does not mutate the original).
    
    Args:
        canonical: The full CIR dict
        destination_agency: "RNP" or "SAMU"
        
    Returns:
        A copy of the CIR with restricted compartments stripped.
    """
    import copy
    filtered = copy.deepcopy(canonical)

    rules = FIREWALL_RULES.get(destination_agency)
    if not rules:
        raise ValueError(f"No firewall rules for agency: {destination_agency}")

    # Strip compartments that this agency must not see
    compartments = filtered.get("restricted_compartments", {})
    for compartment_key in rules["strip_compartments"]:
        compartments[compartment_key] = None

    filtered["restricted_compartments"] = compartments

    return filtered


# ============================================================================
# STAGE 3: CANONICAL TO DESTINATION
# ============================================================================

def canonical_to_samu(canonical: dict) -> dict:
    """Translate a firewalled CIR into a SAMU/e-Banguka destination payload.
    
    The canonical record MUST have been through apply_firewall("SAMU") first.
    """
    loc = canonical.get("location", {})
    sit = canonical.get("situation", {})
    agencies = canonical.get("agencies_involved", [])
    requests = canonical.get("cross_agency_requests", [])

    # Find RNP resources for coordination block
    rnp_info = next((a for a in agencies if a["agency"] == "RNP"), None)
    rnp_units = rnp_info["resources"] if rnp_info else []
    rnp_count = len(rnp_units)
    rnp_first_eta = min((u.get("eta_minutes", 99) for u in rnp_units), default=None)
    
    # Find tactical unit ETA if present
    tactical_eta = None
    for u in rnp_units:
        if u.get("type") == "TACTICAL_UNIT":
            tactical_eta = u.get("eta_minutes")

    # Find fire brigade info
    fire_info = next((a for a in agencies if a["agency"] == "FIRE"), None)
    fire_dispatched = fire_info is not None
    fire_eta = None
    if fire_info and fire_info.get("resources"):
        fire_eta = fire_info["resources"][0].get("eta_minutes")

    # Build scene safety
    scene_safety = {
        "scene_status": translate_to_destination(
            "SAMU", sit.get("scene_status", "SCENE_NOT_SECURE"), "scene_status"
        ),
        "hazards": sit.get("hazards", []),
        "weapons_present": sit.get("weapons_present", False),
        "weapon_category": sit.get("weapon_category"),
        "fire_active": sit.get("fire_active", False),
        "medical_access_authorized": False,
        "requires_hazmat_ppe": False,
    }

    # Add fuel spill if present
    if sit.get("fuel_spill"):
        scene_safety["fuel_spill"] = sit["fuel_spill"]

    # Add chemical hazard if present
    if sit.get("chemical_hazard"):
        scene_safety["chemical_hazard"] = {
            "toxic_gas": sit["chemical_hazard"].get("toxic_gas"),
            "exposure_route": sit["chemical_hazard"].get("exposure_route"),
            "visible_indicators": sit["chemical_hazard"].get("visible_indicators"),
        }
        scene_safety["requires_hazmat_ppe"] = True

    # Check access restriction and hazard zone for medical access status
    access = canonical.get("access_restriction")
    hazard = canonical.get("hazard_zone")

    if access:
        scene_safety["medical_access_authorized"] = access.get("medical_access_authorized", False)
    if hazard:
        scene_safety["medical_access_authorized"] = hazard.get("medical_access_authorized", False)
        scene_safety["requires_hazmat_ppe"] = hazard.get("requires_hazmat_ppe", True)

    # Police on scene check
    rnp_on_scene = any(
        u.get("status") == "ON_SCENE" for u in rnp_units
    )
    scene_safety["police_on_scene"] = rnp_on_scene
    scene_safety["police_eta_minutes"] = rnp_first_eta if not rnp_on_scene else None

    # Build request
    req = requests[0] if requests else {}
    request_type = translate_to_destination(
        "SAMU", req.get("request_type", "MEDICAL_DISPATCH"), "request"
    )

    dest_request = {
        "type": request_type,
        "urgency": req.get("urgency", "IMMEDIATE"),
        "requested_units": req.get("requested_units"),
        "reason": req.get("reason", ""),
    }

    # Add PPE requirements for HAZMAT
    if hazard and hazard.get("requires_hazmat_ppe"):
        dest_request["ppe_requirements"] = ["SCBA_RESPIRATOR", "CHEMICAL_SUIT_LEVEL_B"]

    # Determine clearance type
    clearance_status = canonical.get("clearance_handshake", {}).get("status", "NOT_INITIATED")
    clearance_type = None
    if sit.get("multi_zone"):
        clearance_type = "ZONE_BY_ZONE"

    # Build destination payload
    dest = {
        "external_ref": canonical["ehz_incident_id"],
        "source_system": "E-HUZANYA_GATEWAY",
        "originating_agency": canonical["source_agency"],
        "originating_ref": canonical["source_incident_ref"],

        "incident_category": translate_to_destination(
            "SAMU", canonical["canonical_type"], "incident"
        ),
        "triage_priority": translate_to_destination(
            "SAMU", canonical["canonical_severity"], "severity"
        ),
        "mci_declared": canonical.get("mci_declared", False),
        "patient_estimate": canonical.get("estimated_casualties", 0),

        "scene_location": {
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "description": loc.get("address_text", ""),
            "road_name": loc.get("road_name"),
            "venue_type": loc.get("venue_type"),
            "sector": loc.get("admin_hierarchy", {}).get("sector", ""),
            "district": loc.get("admin_hierarchy", {}).get("district", ""),
        },

        "scene_safety": scene_safety,

        "coordination": {
            "rnp_units_dispatched": rnp_count,
            "rnp_first_arrival_eta": rnp_first_eta,
            "tactical_unit_eta": tactical_eta,
            "fire_brigade_dispatched": fire_dispatched,
            "fire_brigade_eta": fire_eta,
            "clearance_status": clearance_status,
            "clearance_type": clearance_type,
        },

        "request": dest_request,

        "callback_url": f"https://api.ehuzanya.rw/v1/incidents/{canonical['ehz_incident_id']}/updates",
    }

    # Add access restriction if present
    if access:
        dest_access = {
            "restriction_type": access["restriction_type"],
            "restricted_area": access["restricted_area"],
        }
        if access.get("safe_staging"):
            dest_access["safe_staging"] = {
                "lat": access["safe_staging"].get("latitude"),
                "lng": access["safe_staging"].get("longitude"),
                "description": access["safe_staging"].get("description", ""),
                "approach_route": access["safe_staging"].get("approach_route"),
            }
        if access.get("zone_specific_access"):
            dest_access["zone_access"] = [
                {
                    "zone_id": z["zone_id"],
                    "description": _get_zone_description(canonical, z["zone_id"]),
                    "access": z["access"],
                    "casualties_present": _get_zone_casualties(canonical, z["zone_id"]),
                }
                for z in access["zone_specific_access"]
            ]
        dest["access_restriction"] = dest_access

    # Add hazard zone if present
    if hazard:
        plume = hazard.get("estimated_plume", {})
        safe = hazard.get("safe_approach", {})
        staging = safe.get("staging_point", {})
        met = hazard.get("meteorological", {})

        dest["hazard_zone"] = {
            "type": hazard["type"],
            "source_point": {
                "lat": hazard["source_point"]["latitude"],
                "lng": hazard["source_point"]["longitude"],
            },
            "hazard_agent": hazard["hazard_agent"],
            "wind": {
                "direction_degrees": met.get("wind_direction_degrees"),
                "speed_kmh": met.get("wind_speed_kmh"),
                "drift_direction": plume.get("drift_direction"),
            },
            "danger_radius_meters": plume.get("danger_radius_meters"),
            "evacuation_radius_meters": plume.get("evacuation_radius_meters"),
            "plume_corridor": [
                {"lat": p.get("latitude"), "lng": p.get("longitude")}
                for p in plume.get("plume_corridor", [])
            ],
            "safe_staging": {
                "lat": staging.get("latitude"),
                "lng": staging.get("longitude"),
                "description": staging.get("description", ""),
                "approach_route": safe.get("approach_route"),
            },
            "last_updated": hazard.get("last_updated"),
        }

    # Add delayed casualty warning if present
    if canonical.get("delayed_casualty_risk"):
        dest["delayed_casualty_warning"] = canonical["delayed_casualty_risk"]

    return dest


def canonical_to_rnp(canonical: dict) -> dict:
    """Translate a firewalled CIR into an RNP destination payload.
    
    The canonical record MUST have been through apply_firewall("RNP") first.
    """
    loc = canonical.get("location", {})
    sit = canonical.get("situation", {})
    agencies = canonical.get("agencies_involved", [])
    requests = canonical.get("cross_agency_requests", [])

    # Find SAMU resources for coordination block
    samu_info = next((a for a in agencies if a["agency"] == "SAMU"), None)
    samu_units = samu_info["resources"] if samu_info else []
    samu_count = len(samu_units)
    samu_first_eta = min((u.get("eta_minutes", 99) for u in samu_units), default=None)

    # Determine SAMU status
    samu_status = None
    if samu_units:
        any_holding = any(u.get("status", "").startswith("EN_ROUTE_HOLDING") or 
                         u.get("status") == "HOLDING" for u in samu_units)
        if any_holding:
            samu_status = "EN_ROUTE_HOLDING_FOR_CLEARANCE"
        else:
            samu_status = samu_units[0].get("status", "EN_ROUTE")

    # Build request
    req = requests[0] if requests else {}
    request_type = translate_to_destination(
        "RNP", req.get("request_type", "POLICE_DISPATCH"), "request"
    )

    # Build scene assessment
    scene_assessment = {
        "weapons_reported": sit.get("weapons_present"),
        "reports_of_gunfire": "active_gunfire" in sit.get("hazards", []),
        "scene_secure": sit.get("scene_status") == "SCENE_SECURE",
        "civilian_density": loc.get("civilian_density_estimate"),
    }

    # Determine action needed
    action_needed = "SECURE_SCENE_FOR_MEDICAL_ACCESS"
    if req.get("request_type") == "POLICE_DISPATCH_URGENT":
        action_needed = "NEUTRALIZE_THREAT_AND_SECURE_SCENE"
    elif sit.get("scene_status") == "HAZARD_ACTIVE":
        action_needed = "ESTABLISH_PERIMETER_AND_EVACUATE"

    dest = {
        "external_ref": canonical["ehz_incident_id"],
        "source_system": "E-HUZANYA_GATEWAY",
        "originating_agency": canonical["source_agency"],
        "originating_ref": canonical["source_incident_ref"],

        "incident_type": translate_to_destination(
            "RNP", canonical["canonical_type"], "incident"
        ),
        "severity": translate_to_destination(
            "RNP", canonical["canonical_severity"], "severity"
        ),
        "estimated_casualties": canonical.get("estimated_casualties", 0),

        "location": {
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "description": loc.get("address_text", ""),
            "sector": loc.get("admin_hierarchy", {}).get("sector", ""),
            "district": loc.get("admin_hierarchy", {}).get("district", ""),
        },

        "scene_assessment": scene_assessment,

        "coordination": {
            "samu_ambulances_dispatched": samu_count,
            "samu_first_arrival_eta": samu_first_eta,
            "samu_status": samu_status,
            "action_needed": action_needed,
        },

        "request": {
            "type": request_type,
            "urgency": req.get("urgency", "IMMEDIATE"),
            "reason": req.get("reason", ""),
        },

        "callback_url": f"https://api.ehuzanya.rw/v1/incidents/{canonical['ehz_incident_id']}/updates",
    }

    return dest


def canonical_to_destination(canonical: dict, destination_agency: str) -> dict:
    """Route to the correct destination builder.
    
    This is the main entry point for Stage 3 of the pipeline.
    The canonical record MUST have been through apply_firewall() first.
    """
    if destination_agency == "SAMU":
        return canonical_to_samu(canonical)
    elif destination_agency == "RNP":
        return canonical_to_rnp(canonical)
    else:
        raise ValueError(f"Unknown destination agency: {destination_agency}")


# ============================================================================
# FULL PIPELINE
# ============================================================================

def run_translation_pipeline(source_agency: str, payload: dict) -> dict:
    """Execute the complete E-Huzanya translation pipeline.
    
    Source payload → Canonical → Firewall → Destination payload
    
    Args:
        source_agency: "RNP" or "SAMU"
        payload: The raw source payload dict
        
    Returns:
        A dict containing:
            - canonical: The full CIR (for database storage)
            - destination_agency: Who receives the translated payload
            - destination_payload: The translated, firewalled payload
    """
    # Determine destination
    dest_agency = "SAMU" if source_agency == "RNP" else "RNP"

    # Stage 1: Ingest to canonical
    canonical = ingest_to_canonical(source_agency, payload)

    # Stage 2: Apply firewall
    firewalled = apply_firewall(canonical, dest_agency)

    # Stage 3: Translate to destination
    destination_payload = canonical_to_destination(firewalled, dest_agency)

    return {
        "canonical": canonical,
        "destination_agency": dest_agency,
        "destination_payload": destination_payload,
    }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _map_threat_to_scene_status(threat_level: str) -> str:
    """Map an RNP zone threat level to a canonical scene status."""
    mapping = {
        "ACTIVE_THREAT": "SCENE_NOT_SECURE",
        "ACTIVE_ARMED_THREAT": "SCENE_NOT_SECURE",
        "NO_ARMED_THREAT": "SCENE_CONTAINED",
        "NONE": "SCENE_CONTAINED",
        "CLEARED": "SCENE_SECURE",
    }
    return mapping.get(threat_level, "SCENE_NOT_SECURE")


def _build_access_restriction_from_rnp(payload: dict, coords: dict) -> dict:
    """Build a CIR access_restriction object from RNP armed threat data."""
    zones = payload.get("incident_zones", [])
    zone_access = None

    if zones:
        zone_access = [
            {
                "zone_id": z["zone_id"],
                "access": "DENIED" if z.get("threat_level") in ("ACTIVE_THREAT", "ACTIVE_ARMED_THREAT") else "PENDING_CLEARANCE",
                "reason": f"{'Active armed suspects present' if z.get('threat_level') in ('ACTIVE_THREAT', 'ACTIVE_ARMED_THREAT') else 'Awaiting perimeter confirmation'}",
            }
            for z in zones
        ]

    return {
        "restriction_type": "HOT_ZONE",
        "restricted_area": {
            "center": {
                "latitude": coords.get("latitude", 0),
                "longitude": coords.get("longitude", 0),
            },
            "radius_meters": 150,
        },
        "safe_staging": None,  # Set by RNP tactical on scene
        "restriction_reason": "ACTIVE_ARMED_THREAT",
        "medical_access_authorized": False,
        "zone_specific_access": zone_access,
    }


def _build_hazard_zone_from_rnp(payload: dict, coords: dict, chem: dict) -> dict:
    """Build a CIR hazard_zone object from RNP chemical hazard data."""
    now = datetime.now(timezone.utc)

    # Default plume corridor (straight line from source, direction NW)
    src_lat = coords.get("latitude", 0)
    src_lng = coords.get("longitude", 0)
    plume_corridor = [
        {"latitude": src_lat, "longitude": src_lng},
        {"latitude": src_lat + 0.002, "longitude": src_lng - 0.002},
        {"latitude": src_lat + 0.004, "longitude": src_lng - 0.004},
        {"latitude": src_lat + 0.006, "longitude": src_lng - 0.006},
        {"latitude": src_lat + 0.008, "longitude": src_lng - 0.008},
        {"latitude": src_lat + 0.010, "longitude": src_lng - 0.010},
        {"latitude": src_lat + 0.012, "longitude": src_lng - 0.012},
    ]

    return {
        "type": "CHEMICAL_PLUME",
        "source_point": {
            "latitude": src_lat,
            "longitude": src_lng,
        },
        "hazard_agent": chem.get("toxic_gas_detected", "UNKNOWN"),
        "meteorological": {
            "wind_direction_degrees": 315,  # Default NW, updated by field reports
            "wind_speed_kmh": 12,
            "temperature_celsius": 24,
            "atmospheric_stability": "NEUTRAL",
        },
        "estimated_plume": {
            "drift_direction": "NORTHWEST",
            "danger_radius_meters": 800,
            "evacuation_radius_meters": 2000,
            "plume_corridor": plume_corridor,
        },
        "safe_approach": {
            "direction": "SOUTHEAST",
            "staging_point": {
                "latitude": src_lat - 0.003,
                "longitude": src_lng + 0.003,
                "description": "Upwind position, southeast of source",
            },
            "approach_route": "Approach from southeast. Do NOT approach from northwest.",
        },
        "medical_access_authorized": False,
        "requires_hazmat_ppe": True,
        "last_updated": now.isoformat(),
        "update_frequency_seconds": 300,
    }


def _build_delayed_risk(chem: dict) -> Optional[dict]:
    """Build delayed casualty risk from chemical hazard data."""
    toxic_gas = chem.get("toxic_gas_detected")
    if toxic_gas == "NITROGEN_DIOXIDE":
        return {
            "expected": True,
            "mechanism": "NITROGEN_DIOXIDE_INHALATION",
            "onset_window_hours": "4-24",
            "potential_affected_population": 15000,
            "clinical_presentation": "DELAYED_PULMONARY_EDEMA",
        }
    return None


def _get_zone_description(canonical: dict, zone_id: str) -> str:
    """Look up a zone's description from the canonical situation."""
    zones = canonical.get("situation", {}).get("zones", [])
    for z in zones:
        if z.get("zone_id") == zone_id:
            return z.get("description", "")
    return ""


def _get_zone_casualties(canonical: dict, zone_id: str) -> int:
    """Look up a zone's casualty count from the canonical situation."""
    zones = canonical.get("situation", {}).get("zones", [])
    for z in zones:
        if z.get("zone_id") == zone_id:
            return z.get("casualties_present", 0)
    return 0
