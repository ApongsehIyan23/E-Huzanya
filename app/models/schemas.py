"""
E-Huzanya Gateway — Pydantic Schemas
=====================================

These models enforce the JSON contracts defined in our API specification.
Three layers:
    1. Source schemas (RNP and SAMU native payloads)
    2. Canonical Intermediate Representation (CIR)
    3. Destination schemas (translated payloads after firewall)

Plus WebSocket message schemas for the resource stream.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# SHARED / COMMON SCHEMAS
# ============================================================================

class Coordinates(BaseModel):
    latitude: float
    longitude: float


class AdminHierarchy(BaseModel):
    sector: str
    district: str
    province: str = "Kigali"


class StagingPoint(BaseModel):
    latitude: float
    longitude: float
    description: str
    approach_route: Optional[str] = None


# ============================================================================
# SOURCE SCHEMAS — RNP NATIVE PAYLOAD
# ============================================================================

class RNPCallerInfo(BaseModel):
    phone_hash: str
    language: str
    caller_type: str = "BYSTANDER"


class RNPLocation(BaseModel):
    description: str
    sector: str
    district: str
    province: str = "Kigali"
    coordinates: Coordinates
    location_confidence: str
    road_name: Optional[str] = None
    venue_type: Optional[str] = None
    estimated_civilian_density: Optional[int] = None


class RNPUnit(BaseModel):
    unit_id: str
    unit_type: str
    eta_minutes: int
    status: str = "DISPATCHED"


class RNPFuelSpill(BaseModel):
    fuel_type: str
    fuel_volume_liters_est: Optional[int] = None
    flow_direction: Optional[str] = None


class RNPChemicalHazard(BaseModel):
    identified: bool
    agents: list[str] = []
    toxic_gas_detected: Optional[str] = None
    visible_indicators: Optional[str] = None


class RNPSuspects(BaseModel):
    count: int
    armed: bool
    status: str
    fleeing_direction: Optional[str] = None


class RNPIncidentZone(BaseModel):
    zone_id: str
    description: str
    center: Coordinates
    threat_level: str
    casualties_present: int
    casualty_types: list[str] = []


class RNPIncidentDetails(BaseModel):
    estimated_casualties: int
    casualties_description: Optional[str] = None
    casualties_breakdown: Optional[dict] = None
    vehicle_count: Optional[int] = None
    vehicle_types: Optional[list[str]] = None
    hazards_reported: list[str] = []
    weapons_involved: bool = False
    weapon_types: Optional[list[str]] = None
    fire_active: bool = False
    fuel_type: Optional[str] = None
    fuel_volume_liters_est: Optional[int] = None
    fuel_flow_direction: Optional[str] = None
    chemical_hazard: Optional[RNPChemicalHazard] = None
    suspects: Optional[RNPSuspects] = None


class RNPRestricted(BaseModel):
    """RNP-sovereign data — NEVER transmitted to non-RNP agencies."""
    reporting_officer_id: str
    radio_channel: str
    dispatch_authorization: str
    case_file_ref: Optional[str] = None
    intelligence_flags: list[str] = []
    suspect_descriptions: Optional[list[dict]] = None
    tactical_plan: Optional[str] = None
    negotiation_status: Optional[str] = None
    preliminary_cause: Optional[str] = None
    facility_owner: Optional[str] = None
    prior_safety_violations: Optional[int] = None


class RNPCrossAgencyRequest(BaseModel):
    agency_needed: str
    request_type: str
    urgency: str
    requested_units: Optional[int] = None
    reason: str


class RNPSourcePayload(BaseModel):
    """The full JSON payload from the RNP 112 system."""
    rnp_incident_id: str
    call_timestamp: datetime
    call_source: str = "112"
    incident_type: str
    severity: str
    location: RNPLocation
    caller: RNPCallerInfo
    incident_details: RNPIncidentDetails
    incident_zones: Optional[list[RNPIncidentZone]] = None
    rnp_response: dict
    rnp_restricted: RNPRestricted
    cross_agency_request: RNPCrossAgencyRequest


# ============================================================================
# SOURCE SCHEMAS — SAMU / e-BANGUKA NATIVE PAYLOAD
# ============================================================================

class SAMUSceneLocation(BaseModel):
    lat: float
    lng: float
    description: str
    sector: str
    district: str


class SAMUClinicalSummary(BaseModel):
    mechanism_of_injury: str
    reported_injuries: list[str] = []
    patient_conscious: Optional[bool] = None
    bleeding_severity: Optional[str] = None
    mass_casualty_triage: Optional[str] = None
    special_considerations: Optional[str] = None


class SAMUAmbulance(BaseModel):
    unit_id: str
    crew_type: str
    eta_minutes: int
    status: str
    current_location: Optional[Coordinates] = None
    hold_reason: Optional[str] = None


class SAMUHospitalAlert(BaseModel):
    hospitals_notified: list[str] = []
    surge_capacity_requested: bool = False
    expected_severity_mix: Optional[dict] = None


class SAMURestricted(BaseModel):
    """SAMU-sovereign data — NEVER transmitted to non-MoH agencies."""
    lead_paramedic_id: str
    clinical_protocol_activated: Optional[str] = None
    receiving_hospital_alert: Optional[SAMUHospitalAlert] = None
    blood_product_request: Optional[dict] = None


class SAMUCrossAgencyRequest(BaseModel):
    agency_needed: str
    request_type: str
    urgency: str
    reason: str


class SAMUSourcePayload(BaseModel):
    """The full JSON payload from the SAMU e-Banguka system."""
    ebanguka_case_id: str
    call_received: datetime
    call_source: str = "912"
    medical_category: str
    triage_priority: str
    patient_count: int
    scene_location: SAMUSceneLocation
    clinical_summary: SAMUClinicalSummary
    samu_response: dict
    samu_restricted: SAMURestricted
    cross_agency_request: SAMUCrossAgencyRequest


# ============================================================================
# CANONICAL INTERMEDIATE REPRESENTATION (CIR)
# ============================================================================

class CIRLocation(BaseModel):
    latitude: float
    longitude: float
    address_text: str
    road_name: Optional[str] = None
    venue_type: Optional[str] = None
    admin_hierarchy: AdminHierarchy
    confidence: str
    civilian_density_estimate: Optional[int] = None


class CIRFuelSpill(BaseModel):
    active: bool
    fuel_type: str
    flow_direction: Optional[str] = None
    ignition_risk: Optional[str] = None


class CIRChemicalHazard(BaseModel):
    agents_identified: list[str] = []
    toxic_gas: Optional[str] = None
    exposure_route: Optional[str] = None
    lethal_concentration_ppm: Optional[int] = None
    visible_indicators: Optional[str] = None


class CIRZone(BaseModel):
    zone_id: str
    description: str
    center: Coordinates
    status: str
    casualties_present: int
    threat_level: str


class CIRSituation(BaseModel):
    hazards: list[str] = []
    weapons_present: bool = False
    weapon_category: Optional[str] = None
    fire_active: bool = False
    scene_status: str
    vehicle_count: Optional[int] = None
    vehicle_types: Optional[list[str]] = None
    fuel_spill: Optional[CIRFuelSpill] = None
    chemical_hazard: Optional[CIRChemicalHazard] = None
    multi_zone: bool = False
    zones: Optional[list[CIRZone]] = None


class CIRZoneAccess(BaseModel):
    zone_id: str
    access: str
    reason: str


class CIRAccessRestriction(BaseModel):
    restriction_type: str  # "HOT_ZONE"
    restricted_area: dict  # center + radius_meters
    safe_staging: Optional[StagingPoint] = None
    restriction_reason: str
    medical_access_authorized: bool = False
    zone_specific_access: Optional[list[CIRZoneAccess]] = None


class CIRPlumeCorridor(BaseModel):
    latitude: float
    longitude: float


class CIRMeteorological(BaseModel):
    wind_direction_degrees: int
    wind_speed_kmh: int
    temperature_celsius: Optional[int] = None
    atmospheric_stability: Optional[str] = None


class CIRHazardZone(BaseModel):
    type: str  # "CHEMICAL_PLUME"
    source_point: Coordinates
    hazard_agent: str
    meteorological: CIRMeteorological
    estimated_plume: dict  # drift_direction, danger/evac radii, plume_corridor
    safe_approach: dict  # direction, staging_point, approach_route
    medical_access_authorized: bool = False
    requires_hazmat_ppe: bool = True
    last_updated: datetime
    update_frequency_seconds: int = 300


class CIRAgencyResource(BaseModel):
    unit_ref: str
    type: str
    eta_minutes: Optional[int] = None
    status: str


class CIRAgencyInvolved(BaseModel):
    agency: str
    status: str
    resources: list[CIRAgencyResource] = []


class CIRCrossAgencyRequest(BaseModel):
    request_id: str
    target_agency: str
    request_type: str
    urgency: str
    requested_units: Optional[int] = None
    status: str = "PENDING"
    reason: str


class CIRClearanceHandshake(BaseModel):
    status: str = "NOT_INITIATED"
    declared_by: Optional[str] = None
    declared_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    scope: Optional[dict] = None


class CIRDelayedCasualtyRisk(BaseModel):
    expected: bool
    mechanism: str
    onset_window_hours: str
    potential_affected_population: int
    clinical_presentation: str


class CIRRestrictedCompartments(BaseModel):
    rnp_restricted: Optional[dict] = None
    samu_restricted: Optional[dict] = None
    minema_restricted: Optional[dict] = None


class CIRTimelineEvent(BaseModel):
    timestamp: datetime
    event: str
    actor: str
    detail: Optional[str] = None


class CanonicalIncident(BaseModel):
    """The Canonical Intermediate Representation (CIR).
    
    This is the single source of truth for every incident in E-Huzanya.
    All source payloads normalize TO this format.
    All destination payloads are derived FROM this format.
    """
    ehz_incident_id: str
    created_at: datetime
    updated_at: datetime
    version: int = 1
    source_agency: str
    source_incident_ref: str
    linked_refs: list[str] = []

    canonical_type: str
    canonical_severity: str
    mci_declared: bool = False
    estimated_casualties: int = 0

    location: CIRLocation
    situation: CIRSituation

    access_restriction: Optional[CIRAccessRestriction] = None
    hazard_zone: Optional[CIRHazardZone] = None

    agencies_involved: list[CIRAgencyInvolved] = []
    cross_agency_requests: list[CIRCrossAgencyRequest] = []
    clearance_handshake: CIRClearanceHandshake = CIRClearanceHandshake()

    delayed_casualty_risk: Optional[CIRDelayedCasualtyRisk] = None

    restricted_compartments: CIRRestrictedCompartments = CIRRestrictedCompartments()
    timeline: list[CIRTimelineEvent] = []


# ============================================================================
# DESTINATION SCHEMAS — TO SAMU (after firewall)
# ============================================================================

class DestSAMUSceneSafety(BaseModel):
    scene_status: str
    hazards: list[str] = []
    weapons_present: bool = False
    weapon_category: Optional[str] = None
    fire_active: bool = False
    fuel_spill: Optional[dict] = None
    chemical_hazard: Optional[dict] = None
    police_on_scene: Optional[bool] = None
    police_eta_minutes: Optional[int] = None
    medical_access_authorized: bool = False
    requires_hazmat_ppe: bool = False


class DestSAMUZoneAccess(BaseModel):
    zone_id: str
    description: str
    access: str
    casualties_present: int


class DestSAMUAccessRestriction(BaseModel):
    restriction_type: str
    restricted_area: dict
    safe_staging: Optional[dict] = None
    zone_access: Optional[list[DestSAMUZoneAccess]] = None


class DestSAMUHazardZone(BaseModel):
    type: str
    source_point: dict
    hazard_agent: str
    wind: dict
    danger_radius_meters: int
    evacuation_radius_meters: int
    plume_corridor: list[dict]
    safe_staging: dict
    last_updated: datetime


class DestSAMUCoordination(BaseModel):
    rnp_units_dispatched: int
    rnp_first_arrival_eta: Optional[int] = None
    tactical_unit_eta: Optional[int] = None
    fire_brigade_dispatched: Optional[bool] = None
    fire_brigade_eta: Optional[int] = None
    clearance_status: str = "NOT_INITIATED"
    clearance_type: Optional[str] = None


class DestSAMURequest(BaseModel):
    type: str
    urgency: str
    requested_units: Optional[int] = None
    reason: str
    ppe_requirements: Optional[list[str]] = None


class DestSAMUPayload(BaseModel):
    """Payload received by SAMU after the Clinical Firewall has executed.
    
    STRIPPED: All rnp_restricted data (officer IDs, radio channels,
    case files, intelligence flags, suspect descriptions, tactical plans).
    
    RETAINED: Scene safety fields (weapons, hazards, access restrictions,
    hazard zones, staging coordinates) that protect paramedic lives.
    """
    external_ref: str
    source_system: str = "E-HUZANYA_GATEWAY"
    originating_agency: str
    originating_ref: str

    incident_category: str
    triage_priority: str
    mci_declared: bool = False
    patient_estimate: int = 0

    delayed_casualty_warning: Optional[CIRDelayedCasualtyRisk] = None

    scene_location: dict
    scene_safety: DestSAMUSceneSafety

    access_restriction: Optional[DestSAMUAccessRestriction] = None
    hazard_zone: Optional[DestSAMUHazardZone] = None

    coordination: DestSAMUCoordination
    request: DestSAMURequest

    callback_url: str


# ============================================================================
# DESTINATION SCHEMAS — TO RNP (after firewall)
# ============================================================================

class DestRNPSceneAssessment(BaseModel):
    weapons_reported: Optional[bool] = None
    reports_of_gunfire: Optional[bool] = None
    scene_secure: bool = False
    civilian_density: Optional[int] = None


class DestRNPCoordination(BaseModel):
    samu_ambulances_dispatched: int
    samu_first_arrival_eta: Optional[int] = None
    samu_status: Optional[str] = None
    action_needed: str


class DestRNPRequest(BaseModel):
    type: str
    urgency: str
    reason: str


class DestRNPPayload(BaseModel):
    """Payload received by RNP after the Clinical Firewall has executed.
    
    STRIPPED: All samu_restricted data (paramedic IDs, clinical protocols,
    hospital alerts, triage classifications, blood product requests).
    
    RETAINED: Scene safety fields and coordination data (ambulance count,
    ETAs, staging status).
    """
    external_ref: str
    source_system: str = "E-HUZANYA_GATEWAY"
    originating_agency: str
    originating_ref: str

    incident_type: str
    severity: str
    estimated_casualties: int = 0

    location: dict
    scene_assessment: Optional[DestRNPSceneAssessment] = None

    coordination: DestRNPCoordination
    request: DestRNPRequest

    callback_url: str


# ============================================================================
# WEBSOCKET MESSAGE SCHEMAS
# ============================================================================

class WSSubscribe(BaseModel):
    action: str = "SUBSCRIBE"
    incident_id: str
    subscribe_to: list[str] = ["ALL_AGENCIES"]


class WSResourcePosition(BaseModel):
    latitude: float
    longitude: float
    heading_degrees: Optional[int] = None
    speed_kmh: Optional[int] = None


class WSResourceUpdate(BaseModel):
    message_type: str = "RESOURCE_POSITION_UPDATE"
    timestamp: datetime
    incident_id: str
    resource: dict  # unit_id, agency, type, icon, color, position, status, eta


class WSStatusChange(BaseModel):
    message_type: str = "RESOURCE_STATUS_CHANGE"
    timestamp: datetime
    incident_id: str
    resource: dict  # unit_id, agency, type, icon, color
    previous_status: str
    new_status: str
    detail: Optional[str] = None
    scene_update: Optional[dict] = None


class WSClearanceDeclared(BaseModel):
    message_type: str = "SCENE_CLEARANCE_DECLARED"
    timestamp: datetime
    incident_id: str
    declared_by: str
    clearance_scope: dict  # zone_id, description, boundary, cleared_for
    clearance_status: str = "AWAITING_ACKNOWLEDGMENT"


class WSClearanceAcknowledged(BaseModel):
    message_type: str = "SCENE_CLEARANCE_ACKNOWLEDGED"
    timestamp: datetime
    incident_id: str
    acknowledged_by: str
    clearance_scope: dict
    clearance_status: str = "CONFIRMED"


class WSHazardZoneUpdate(BaseModel):
    message_type: str = "HAZARD_ZONE_UPDATE"
    timestamp: datetime
    incident_id: str
    previous_wind_direction: Optional[int] = None
    updated_hazard_zone: dict


class WSResourceThreatWarning(BaseModel):
    message_type: str = "RESOURCE_THREAT_WARNING"
    timestamp: datetime
    incident_id: str
    alert_level: str = "CRITICAL"
    warning: dict  # affected_unit, threat_type, hazard_agent, instruction, safe_direction


class WSIncidentResolved(BaseModel):
    message_type: str = "INCIDENT_STATUS_CHANGE"
    timestamp: datetime
    incident_id: str
    new_status: str
    resolved_by: str
    summary: dict
