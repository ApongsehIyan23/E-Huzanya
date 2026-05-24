"""
E-Huzanya Gateway — API Routes
================================

REST endpoints for the E-Huzanya Gateway:

    POST /api/v1/incidents/ingest         — Receive webhook from agency
    GET  /api/v1/incidents                — List all incidents
    GET  /api/v1/incidents/{id}           — Get single incident (CIR)
    GET  /api/v1/incidents/{id}/destination/{agency} — Get translated payload
    POST /api/v1/incidents/{id}/clearance/declare    — Scene clearance declaration
    POST /api/v1/incidents/{id}/clearance/acknowledge — Scene clearance acknowledgment
    POST /api/v1/incidents/{id}/updates   — Callback endpoint for agency updates
    
    # Simulator endpoints (catch translated payloads during demo)
    POST /api/v1/simulator/samu-inbox     — Catches payloads destined for SAMU
    POST /api/v1/simulator/rnp-inbox      — Catches payloads destined for RNP
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import verify_api_key, require_scope
from app.db.session import get_db
from app.db.models import IncidentRecord, TimelineEvent, ActiveZone
from app.services.translation import (
    run_translation_pipeline,
    apply_firewall,
    canonical_to_destination,
    ingest_to_canonical,
)

router = APIRouter(prefix="/api/v1")


# ============================================================================
# In-memory stores for the MVP demo
# ============================================================================

# Stores the last N translated payloads for each agency (simulator inbox)
simulator_inbox = {
    "SAMU": [],
    "RNP": [],
}

# Stores active incidents by EHZ ID for quick lookup
# (supplements the database for real-time operations)
active_incidents_cache = {}


# ============================================================================
# CORE ENDPOINT: Incident Ingestion
# ============================================================================

@router.post("/incidents/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_incident(
    payload: dict,
    x_ehz_source_agency: str = Header(..., alias="X-EHZ-Source-Agency"),
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Receive a webhook from an agency system and run the full translation pipeline.
    
    This is the primary endpoint of E-Huzanya. When an agency logs an incident
    in their own system, they POST to this endpoint. E-Huzanya:
    
    1. Normalizes the payload to the Canonical Intermediate Representation
    2. Stores the CIR in the database
    3. Applies the Clinical Firewall
    4. Translates to the destination agency's format
    5. Returns both the CIR and the translated payload
    
    In production, step 5 would push via webhook to the destination agency.
    In the MVP, we store it in the simulator inbox for the frontend to poll.
    """
    # Determine source agency from header (SIMULATOR can specify any)
    source_agency = x_ehz_source_agency
    if source_agency not in ("RNP", "SAMU"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid source agency: {source_agency}. Must be RNP or SAMU.",
        )

    # Run the full translation pipeline
    try:
        result = run_translation_pipeline(source_agency, payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Translation pipeline error: {str(e)}",
        )

    canonical = result["canonical"]
    dest_agency = result["destination_agency"]
    dest_payload = result["destination_payload"]

    # Store in database
    incident = IncidentRecord(
        ehz_incident_id=canonical["ehz_incident_id"],
        source_agency=canonical["source_agency"],
        source_incident_ref=canonical["source_incident_ref"],
        linked_refs=canonical.get("linked_refs", []),
        canonical_type=canonical["canonical_type"],
        canonical_severity=canonical["canonical_severity"],
        mci_declared=canonical.get("mci_declared", False),
        estimated_casualties=canonical.get("estimated_casualties", 0),
        latitude=canonical["location"]["latitude"],
        longitude=canonical["location"]["longitude"],
        address_text=canonical["location"].get("address_text"),
        road_name=canonical["location"].get("road_name"),
        venue_type=canonical["location"].get("venue_type"),
        sector=canonical["location"]["admin_hierarchy"]["sector"],
        district=canonical["location"]["admin_hierarchy"]["district"],
        province=canonical["location"]["admin_hierarchy"].get("province", "Kigali"),
        location_confidence=canonical["location"].get("confidence"),
        situation=canonical["situation"],
        access_restriction=canonical.get("access_restriction"),
        hazard_zone=canonical.get("hazard_zone"),
        delayed_casualty_risk=canonical.get("delayed_casualty_risk"),
        agencies_involved=canonical.get("agencies_involved", []),
        cross_agency_requests=canonical.get("cross_agency_requests", []),
        clearance_handshake=canonical.get("clearance_handshake", {}),
        restricted_compartments=canonical.get("restricted_compartments", {}),
        status="ACTIVE",
    )
    db.add(incident)

    # Add timeline event
    timeline = TimelineEvent(
        incident_id=0,  # Will be set after flush
        timestamp=datetime.now(timezone.utc),
        event="INCIDENT_CREATED",
        actor=source_agency,
        detail=f"{canonical['canonical_type']} reported via {'112' if source_agency == 'RNP' else '912'}",
    )

    await db.flush()  # Get the auto-generated incident.id
    timeline.incident_id = incident.id
    db.add(timeline)

    # Store active zone for geofencing if applicable
    if canonical.get("access_restriction"):
        ar = canonical["access_restriction"]
        zone = ActiveZone(
            incident_id=incident.id,
            ehz_incident_id=canonical["ehz_incident_id"],
            zone_type="HOT_ZONE",
            center_lat=ar["restricted_area"]["center"]["latitude"],
            center_lng=ar["restricted_area"]["center"]["longitude"],
            radius_meters=ar["restricted_area"].get("radius_meters", 150),
            staging_lat=ar["safe_staging"]["latitude"] if ar.get("safe_staging") else None,
            staging_lng=ar["safe_staging"]["longitude"] if ar.get("safe_staging") else None,
            staging_description=ar["safe_staging"]["description"] if ar.get("safe_staging") else None,
            is_active=True,
            medical_access_authorized=ar.get("medical_access_authorized", False),
        )
        db.add(zone)

    if canonical.get("hazard_zone"):
        hz = canonical["hazard_zone"]
        plume = hz.get("estimated_plume", {})
        safe = hz.get("safe_approach", {})
        staging = safe.get("staging_point", {})
        zone = ActiveZone(
            incident_id=incident.id,
            ehz_incident_id=canonical["ehz_incident_id"],
            zone_type="CHEMICAL_PLUME",
            center_lat=hz["source_point"]["latitude"],
            center_lng=hz["source_point"]["longitude"],
            plume_corridor=plume.get("plume_corridor"),
            danger_radius_meters=plume.get("danger_radius_meters"),
            evacuation_radius_meters=plume.get("evacuation_radius_meters"),
            staging_lat=staging.get("latitude"),
            staging_lng=staging.get("longitude"),
            staging_description=staging.get("description"),
            is_active=True,
            medical_access_authorized=hz.get("medical_access_authorized", False),
            requires_hazmat_ppe=hz.get("requires_hazmat_ppe", True),
        )
        db.add(zone)

    await db.commit()

    # Cache for real-time operations
    active_incidents_cache[canonical["ehz_incident_id"]] = canonical

    # Store in simulator inbox (MVP: simulates webhook push to destination)
    simulator_inbox[dest_agency].append({
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": dest_payload,
    })
    # Keep only last 50 messages per inbox
    simulator_inbox[dest_agency] = simulator_inbox[dest_agency][-50:]

    return {
        "status": "CREATED",
        "ehz_incident_id": canonical["ehz_incident_id"],
        "canonical_type": canonical["canonical_type"],
        "canonical_severity": canonical["canonical_severity"],
        "mci_declared": canonical["mci_declared"],
        "destination_agency": dest_agency,
        "destination_payload": dest_payload,
        "pipeline_summary": {
            "source": source_agency,
            "source_ref": canonical["source_incident_ref"],
            "canonical_type": canonical["canonical_type"],
            "destination": dest_agency,
            "destination_type": dest_payload.get("incident_category") or dest_payload.get("incident_type"),
            "firewall_applied": True,
            "fields_stripped": _get_stripped_fields_summary(source_agency),
        },
    }


# ============================================================================
# INCIDENT RETRIEVAL
# ============================================================================

@router.get("/incidents")
async def list_incidents(
    status_filter: Optional[str] = "ACTIVE",
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all incidents, optionally filtered by status."""
    query = select(IncidentRecord)
    if status_filter:
        query = query.where(IncidentRecord.status == status_filter)
    query = query.order_by(IncidentRecord.created_at.desc())

    result = await db.execute(query)
    incidents = result.scalars().all()

    return {
        "count": len(incidents),
        "incidents": [
            {
                "ehz_incident_id": inc.ehz_incident_id,
                "canonical_type": inc.canonical_type,
                "canonical_severity": inc.canonical_severity,
                "mci_declared": inc.mci_declared,
                "estimated_casualties": inc.estimated_casualties,
                "source_agency": inc.source_agency,
                "latitude": inc.latitude,
                "longitude": inc.longitude,
                "address_text": inc.address_text,
                "sector": inc.sector,
                "district": inc.district,
                "status": inc.status,
                "created_at": inc.created_at.isoformat() if inc.created_at else None,
                "has_access_restriction": inc.access_restriction is not None,
                "has_hazard_zone": inc.hazard_zone is not None,
            }
            for inc in incidents
        ],
    }


@router.get("/incidents/{ehz_incident_id}")
async def get_incident(
    ehz_incident_id: str,
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get a single incident's full canonical representation.
    
    Note: Returns the FULL CIR including restricted compartments.
    In production, this would be filtered based on the requesting agency's scopes.
    """
    query = select(IncidentRecord).where(
        IncidentRecord.ehz_incident_id == ehz_incident_id
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {ehz_incident_id} not found",
        )

    # Get timeline
    timeline_query = select(TimelineEvent).where(
        TimelineEvent.incident_id == incident.id
    ).order_by(TimelineEvent.timestamp)
    timeline_result = await db.execute(timeline_query)
    timeline_events = timeline_result.scalars().all()

    return {
        "ehz_incident_id": incident.ehz_incident_id,
        "source_agency": incident.source_agency,
        "source_incident_ref": incident.source_incident_ref,
        "canonical_type": incident.canonical_type,
        "canonical_severity": incident.canonical_severity,
        "mci_declared": incident.mci_declared,
        "estimated_casualties": incident.estimated_casualties,
        "location": {
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "address_text": incident.address_text,
            "road_name": incident.road_name,
            "venue_type": incident.venue_type,
            "sector": incident.sector,
            "district": incident.district,
            "province": incident.province,
        },
        "situation": incident.situation,
        "access_restriction": incident.access_restriction,
        "hazard_zone": incident.hazard_zone,
        "delayed_casualty_risk": incident.delayed_casualty_risk,
        "agencies_involved": incident.agencies_involved,
        "cross_agency_requests": incident.cross_agency_requests,
        "clearance_handshake": incident.clearance_handshake,
        "status": incident.status,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        "timeline": [
            {
                "timestamp": te.timestamp.isoformat() if te.timestamp else None,
                "event": te.event,
                "actor": te.actor,
                "detail": te.detail,
            }
            for te in timeline_events
        ],
    }


@router.get("/incidents/{ehz_incident_id}/translated/{dest_agency}")
async def get_translated_payload(
    ehz_incident_id: str,
    dest_agency: str,
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get the translated payload for a specific destination agency.
    
    Applies the Clinical Firewall and returns the destination-formatted payload.
    Useful for the frontend to display what each agency sees.
    """
    if dest_agency not in ("RNP", "SAMU"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="dest_agency must be RNP or SAMU",
        )

    # Get from cache first, fall back to database
    canonical = active_incidents_cache.get(ehz_incident_id)

    if not canonical:
        query = select(IncidentRecord).where(
            IncidentRecord.ehz_incident_id == ehz_incident_id
        )
        result = await db.execute(query)
        incident = result.scalar_one_or_none()
        if not incident:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident {ehz_incident_id} not found",
            )
        # Reconstruct canonical from DB record
        canonical = _db_record_to_canonical(incident)

    # Apply firewall and translate
    firewalled = apply_firewall(canonical, dest_agency)
    translated = canonical_to_destination(firewalled, dest_agency)

    return {
        "ehz_incident_id": ehz_incident_id,
        "destination_agency": dest_agency,
        "firewall_applied": True,
        "translated_payload": translated,
    }


# ============================================================================
# CLEARANCE HANDSHAKE
# ============================================================================

@router.post("/incidents/{ehz_incident_id}/clearance/declare")
async def declare_clearance(
    ehz_incident_id: str,
    body: dict,
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Stage 1 of the bilateral clearance handshake.
    
    RNP declares a scene (or zone) is cleared for medical access.
    Status changes to AWAITING_ACKNOWLEDGMENT. SAMU must acknowledge
    before ambulances are authorized to advance.
    """
    query = select(IncidentRecord).where(
        IncidentRecord.ehz_incident_id == ehz_incident_id
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    now = datetime.now(timezone.utc)
    declaring_agency = body.get("declaring_agency", "RNP")
    scope = body.get("scope", {})

    # Update clearance handshake
    incident.clearance_handshake = {
        "status": "AWAITING_ACKNOWLEDGMENT",
        "declared_by": declaring_agency,
        "declared_at": now.isoformat(),
        "acknowledged_by": None,
        "acknowledged_at": None,
        "scope": scope,
    }

    # If zone-specific clearance, update access restriction
    if incident.access_restriction and scope.get("zone_id"):
        ar = incident.access_restriction
        if ar.get("zone_specific_access"):
            for zone in ar["zone_specific_access"]:
                if zone["zone_id"] == scope["zone_id"]:
                    zone["access"] = "CLEARED_AWAITING_ACK"
            incident.access_restriction = ar

    # Timeline event
    timeline = TimelineEvent(
        incident_id=incident.id,
        timestamp=now,
        event="CLEARANCE_DECLARED",
        actor=declaring_agency,
        detail=f"Scene clearance declared for {scope.get('zone_id', 'full scene')}",
    )
    db.add(timeline)

    incident.updated_at = now
    await db.commit()

    # Update cache
    if ehz_incident_id in active_incidents_cache:
        active_incidents_cache[ehz_incident_id]["clearance_handshake"] = incident.clearance_handshake

    return {
        "status": "AWAITING_ACKNOWLEDGMENT",
        "ehz_incident_id": ehz_incident_id,
        "declared_by": declaring_agency,
        "declared_at": now.isoformat(),
        "scope": scope,
        "message": "Clearance declared. Awaiting acknowledgment from receiving agency.",
    }


@router.post("/incidents/{ehz_incident_id}/clearance/acknowledge")
async def acknowledge_clearance(
    ehz_incident_id: str,
    body: dict,
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Stage 2 of the bilateral clearance handshake.
    
    SAMU acknowledges the clearance declaration. Only now are ambulances
    authorized to advance to the cleared zone. Both agencies share
    accountability via the audit trail.
    """
    query = select(IncidentRecord).where(
        IncidentRecord.ehz_incident_id == ehz_incident_id
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Verify clearance has been declared
    handshake = incident.clearance_handshake or {}
    if handshake.get("status") != "AWAITING_ACKNOWLEDGMENT":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot acknowledge: clearance status is '{handshake.get('status', 'NOT_INITIATED')}', expected 'AWAITING_ACKNOWLEDGMENT'",
        )

    now = datetime.now(timezone.utc)
    acknowledging_agency = body.get("acknowledging_agency", "SAMU")

    # Update clearance handshake
    incident.clearance_handshake = {
        "status": "CONFIRMED",
        "declared_by": handshake["declared_by"],
        "declared_at": handshake["declared_at"],
        "acknowledged_by": acknowledging_agency,
        "acknowledged_at": now.isoformat(),
        "scope": handshake.get("scope", {}),
    }

    # If zone-specific, update access restriction to AUTHORIZED
    scope = handshake.get("scope", {})
    if incident.access_restriction and scope.get("zone_id"):
        ar = incident.access_restriction
        if ar.get("zone_specific_access"):
            for zone in ar["zone_specific_access"]:
                if zone["zone_id"] == scope["zone_id"]:
                    zone["access"] = "AUTHORIZED"
            incident.access_restriction = ar

    # Update active zone medical access
    zone_query = select(ActiveZone).where(
        ActiveZone.ehz_incident_id == ehz_incident_id,
        ActiveZone.is_active == True,
    )
    zone_result = await db.execute(zone_query)
    active_zones = zone_result.scalars().all()
    for az in active_zones:
        az.medical_access_authorized = True
        az.last_updated = now

    # Timeline event
    timeline = TimelineEvent(
        incident_id=incident.id,
        timestamp=now,
        event="CLEARANCE_ACKNOWLEDGED",
        actor=acknowledging_agency,
        detail=f"Scene clearance acknowledged for {scope.get('zone_id', 'full scene')}. Medical access AUTHORIZED.",
    )
    db.add(timeline)

    incident.updated_at = now
    await db.commit()

    # Update cache
    if ehz_incident_id in active_incidents_cache:
        active_incidents_cache[ehz_incident_id]["clearance_handshake"] = incident.clearance_handshake

    return {
        "status": "CONFIRMED",
        "ehz_incident_id": ehz_incident_id,
        "declared_by": handshake["declared_by"],
        "acknowledged_by": acknowledging_agency,
        "acknowledged_at": now.isoformat(),
        "scope": scope,
        "message": "Clearance CONFIRMED. Medical access is now AUTHORIZED.",
        "medical_access_authorized": True,
    }


# ============================================================================
# INCIDENT UPDATES (Callback endpoint)
# ============================================================================

@router.post("/incidents/{ehz_incident_id}/updates")
async def receive_incident_update(
    ehz_incident_id: str,
    body: dict,
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Callback endpoint for agencies to push status updates back to E-Huzanya."""
    query = select(IncidentRecord).where(
        IncidentRecord.ehz_incident_id == ehz_incident_id
    )
    result = await db.execute(query)
    incident = result.scalar_one_or_none()

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    now = datetime.now(timezone.utc)
    update_type = body.get("update_type", "STATUS_UPDATE")
    actor = body.get("agency", "UNKNOWN")

    # Add timeline event
    timeline = TimelineEvent(
        incident_id=incident.id,
        timestamp=now,
        event=update_type,
        actor=actor,
        detail=body.get("detail", ""),
    )
    db.add(timeline)

    # Handle specific update types
    if update_type == "RESOURCE_ON_SCENE":
        # Update agencies_involved status
        agencies = incident.agencies_involved or []
        for agency in agencies:
            if agency["agency"] == actor:
                agency["status"] = "ON_SCENE"
                for resource in agency.get("resources", []):
                    if resource.get("unit_ref") == body.get("unit_id"):
                        resource["status"] = "ON_SCENE"
        incident.agencies_involved = agencies

    elif update_type == "INCIDENT_RESOLVED":
        incident.status = "RESOLVED"

    incident.updated_at = now
    await db.commit()

    return {
        "status": "UPDATE_RECEIVED",
        "ehz_incident_id": ehz_incident_id,
        "update_type": update_type,
        "timestamp": now.isoformat(),
    }


# ============================================================================
# SIMULATOR ENDPOINTS
# ============================================================================

@router.post("/simulator/samu-inbox")
async def samu_inbox_endpoint(payload: dict):
    """Catches translated payloads destined for SAMU during the MVP demo.
    In production, this would be e-Banguka's actual webhook endpoint.
    """
    simulator_inbox["SAMU"].append({
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    })
    return {"status": "RECEIVED", "agency": "SAMU"}


@router.post("/simulator/rnp-inbox")
async def rnp_inbox_endpoint(payload: dict):
    """Catches translated payloads destined for RNP during the MVP demo."""
    simulator_inbox["RNP"].append({
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    })
    return {"status": "RECEIVED", "agency": "RNP"}


@router.get("/simulator/inbox/{agency}")
async def get_simulator_inbox(
    agency: str,
    auth: dict = Depends(verify_api_key),
):
    """Read the simulator inbox for an agency. Used by the frontend to poll
    for translated payloads during the demo.
    """
    if agency not in ("SAMU", "RNP"):
        raise HTTPException(status_code=400, detail="Agency must be SAMU or RNP")

    messages = simulator_inbox.get(agency, [])
    return {
        "agency": agency,
        "message_count": len(messages),
        "messages": messages,
    }


@router.delete("/simulator/inbox/{agency}")
async def clear_simulator_inbox(
    agency: str,
    auth: dict = Depends(verify_api_key),
):
    """Clear the simulator inbox for a fresh demo run."""
    if agency not in ("SAMU", "RNP"):
        raise HTTPException(status_code=400, detail="Agency must be SAMU or RNP")

    simulator_inbox[agency] = []
    return {"status": "CLEARED", "agency": agency}


# ============================================================================
# ACTIVE ZONES (for geofencing)
# ============================================================================

@router.get("/zones/active")
async def get_active_zones(
    auth: dict = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get all active threat/hazard zones for geofencing checks."""
    query = select(ActiveZone).where(ActiveZone.is_active == True)
    result = await db.execute(query)
    zones = result.scalars().all()

    return {
        "count": len(zones),
        "zones": [
            {
                "ehz_incident_id": z.ehz_incident_id,
                "zone_type": z.zone_type,
                "center": {"lat": z.center_lat, "lng": z.center_lng},
                "radius_meters": z.radius_meters,
                "plume_corridor": z.plume_corridor,
                "danger_radius_meters": z.danger_radius_meters,
                "evacuation_radius_meters": z.evacuation_radius_meters,
                "staging": {
                    "lat": z.staging_lat,
                    "lng": z.staging_lng,
                    "description": z.staging_description,
                } if z.staging_lat else None,
                "medical_access_authorized": z.medical_access_authorized,
                "requires_hazmat_ppe": z.requires_hazmat_ppe,
            }
            for z in zones
        ],
    }


# ============================================================================
# HELPERS
# ============================================================================

def _get_stripped_fields_summary(source_agency: str) -> list[str]:
    """Return a human-readable list of what the firewall stripped."""
    if source_agency == "RNP":
        return [
            "rnp_restricted.reporting_officer_id",
            "rnp_restricted.radio_channel",
            "rnp_restricted.dispatch_authorization",
            "rnp_restricted.case_file_ref",
            "rnp_restricted.intelligence_flags",
            "rnp_restricted.suspect_descriptions",
            "rnp_restricted.tactical_plan",
        ]
    elif source_agency == "SAMU":
        return [
            "samu_restricted.lead_paramedic_id",
            "samu_restricted.clinical_protocol_activated",
            "samu_restricted.receiving_hospital_alert",
            "samu_restricted.blood_product_request",
        ]
    return []


def _db_record_to_canonical(incident: IncidentRecord) -> dict:
    """Reconstruct a canonical dict from a database record."""
    return {
        "ehz_incident_id": incident.ehz_incident_id,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        "version": incident.version,
        "source_agency": incident.source_agency,
        "source_incident_ref": incident.source_incident_ref,
        "linked_refs": incident.linked_refs or [],
        "canonical_type": incident.canonical_type,
        "canonical_severity": incident.canonical_severity,
        "mci_declared": incident.mci_declared,
        "estimated_casualties": incident.estimated_casualties,
        "location": {
            "latitude": incident.latitude,
            "longitude": incident.longitude,
            "address_text": incident.address_text,
            "road_name": incident.road_name,
            "venue_type": incident.venue_type,
            "admin_hierarchy": {
                "sector": incident.sector,
                "district": incident.district,
                "province": incident.province,
            },
            "confidence": incident.location_confidence,
        },
        "situation": incident.situation or {},
        "access_restriction": incident.access_restriction,
        "hazard_zone": incident.hazard_zone,
        "delayed_casualty_risk": incident.delayed_casualty_risk,
        "agencies_involved": incident.agencies_involved or [],
        "cross_agency_requests": incident.cross_agency_requests or [],
        "clearance_handshake": incident.clearance_handshake or {},
        "restricted_compartments": incident.restricted_compartments or {},
        "timeline": [],
    }
