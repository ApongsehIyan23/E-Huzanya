"""
E-Huzanya Gateway — WebSocket Server
======================================

Real-time communication layer for the E-Huzanya Gateway.

Endpoints:
    WS /ws/stream          — Main resource stream (dispatchers connect here)
    POST /api/v1/resources/position  — Vehicle position update (fleet GPS systems push here)

Features:
    1. Live vehicle position broadcasting to subscribed dispatchers
    2. Geofencing engine — checks positions against active hazard/hot zones
    3. Clearance handshake status broadcasting
    4. Incident creation notifications
"""

import asyncio
import json
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import verify_api_key
from app.core.config import GEOFENCE_APPROACH_THRESHOLD_METERS
from app.core.mappings import get_resource_display
from app.db.session import get_db
from app.db.models import ActiveZone

router = APIRouter()


# ============================================================================
# CONNECTION MANAGER
# ============================================================================

class ConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self):
        # All connected clients
        self.active_connections: list[WebSocket] = []
        # Incident-specific subscriptions: {incident_id: [websocket, ...]}
        self.incident_subscriptions: dict[str, list[WebSocket]] = {}
        # Agency-specific connections: {agency: [websocket, ...]}
        self.agency_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, agency: Optional[str] = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if agency:
            if agency not in self.agency_connections:
                self.agency_connections[agency] = []
            self.agency_connections[agency].append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        # Remove from incident subscriptions
        for incident_id in list(self.incident_subscriptions.keys()):
            if websocket in self.incident_subscriptions[incident_id]:
                self.incident_subscriptions[incident_id].remove(websocket)
            if not self.incident_subscriptions[incident_id]:
                del self.incident_subscriptions[incident_id]
        # Remove from agency connections
        for agency in list(self.agency_connections.keys()):
            if websocket in self.agency_connections[agency]:
                self.agency_connections[agency].remove(websocket)

    def subscribe_to_incident(self, websocket: WebSocket, incident_id: str):
        if incident_id not in self.incident_subscriptions:
            self.incident_subscriptions[incident_id] = []
        if websocket not in self.incident_subscriptions[incident_id]:
            self.incident_subscriptions[incident_id].append(websocket)

    async def broadcast_to_all(self, message: dict):
        """Send a message to ALL connected clients."""
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_to_incident(self, incident_id: str, message: dict):
        """Send a message to clients subscribed to a specific incident."""
        subscribers = self.incident_subscriptions.get(incident_id, [])
        disconnected = []
        for ws in subscribers:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def broadcast_to_agency(self, agency: str, message: dict):
        """Send a message to all clients connected as a specific agency."""
        connections = self.agency_connections.get(agency, [])
        disconnected = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


# Global connection manager instance
manager = ConnectionManager()


# ============================================================================
# GEOFENCING ENGINE
# ============================================================================

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in meters."""
    R = 6371000  # Earth's radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def point_in_polygon(lat: float, lng: float, polygon: list[dict]) -> bool:
    """Ray casting algorithm to check if a point is inside a polygon.
    
    polygon is a list of dicts with 'latitude'/'lat' and 'longitude'/'lng' keys.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    coords = []
    for p in polygon:
        plat = p.get("latitude", p.get("lat", 0))
        plng = p.get("longitude", p.get("lng", 0))
        coords.append((plat, plng))

    j = n - 1
    for i in range(n):
        yi, xi = coords[i]
        yj, xj = coords[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside


def expand_plume_to_polygon(corridor: list[dict], width_meters: float = 200) -> list[dict]:
    """Expand a plume corridor center line into a polygon with width.
    
    Takes the corridor points and creates a buffer polygon around them.
    Simplified approach: offset points perpendicular to the corridor direction.
    """
    if len(corridor) < 2:
        return corridor

    # Approximate degrees per meter at Kigali's latitude (~-2 degrees)
    meters_per_deg_lat = 111320
    meters_per_deg_lng = 111320 * math.cos(math.radians(-2))
    offset_lat = width_meters / meters_per_deg_lat
    offset_lng = width_meters / meters_per_deg_lng

    left_side = []
    right_side = []

    for p in corridor:
        plat = p.get("latitude", p.get("lat", 0))
        plng = p.get("longitude", p.get("lng", 0))
        left_side.append({"latitude": plat + offset_lat, "longitude": plng - offset_lng})
        right_side.append({"latitude": plat - offset_lat, "longitude": plng + offset_lng})

    # Create polygon: left side forward, right side reversed
    right_side.reverse()
    return left_side + right_side


async def check_geofence(
    unit_id: str,
    agency: str,
    lat: float,
    lng: float,
    active_zones: list,
) -> Optional[dict]:
    """Check if a resource position violates any active geofence.
    
    Returns a RESOURCE_THREAT_WARNING dict if violated, None otherwise.
    """
    for zone in active_zones:
        if zone.medical_access_authorized:
            # Zone has been cleared, skip
            continue

        warning = None

        if zone.zone_type == "HOT_ZONE" and zone.center_lat and zone.center_lng:
            distance = haversine_distance(lat, lng, zone.center_lat, zone.center_lng)
            radius = zone.radius_meters or 150
            threshold = radius + GEOFENCE_APPROACH_THRESHOLD_METERS

            if distance <= threshold:
                warning = {
                    "message_type": "RESOURCE_THREAT_WARNING",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "incident_id": zone.ehz_incident_id,
                    "alert_level": "CRITICAL" if distance <= radius else "WARNING",
                    "warning": {
                        "affected_unit": unit_id,
                        "agency": agency,
                        "unit_current_position": {"lat": lat, "lng": lng},
                        "threat_type": "INSIDE_HAZARD_ZONE" if distance <= radius else "APPROACHING_HAZARD_ZONE",
                        "hazard_agent": "ACTIVE_ARMED_THREAT",
                        "instruction": "EVACUATE_IMMEDIATELY" if distance <= radius else "DO_NOT_APPROACH",
                        "safe_direction": "AWAY_FROM_ZONE",
                        "nearest_safe_point": {
                            "lat": zone.staging_lat,
                            "lng": zone.staging_lng,
                            "description": zone.staging_description or "Designated staging area",
                            "distance_meters": round(distance),
                        } if zone.staging_lat else None,
                        "geofence_trigger": {
                            "zone_type": zone.zone_type,
                            "boundary_distance_meters": round(distance),
                            "zone_radius_meters": radius,
                        },
                    },
                }

        elif zone.zone_type == "CHEMICAL_PLUME" and zone.plume_corridor:
            # Check against expanded plume polygon
            polygon = expand_plume_to_polygon(zone.plume_corridor, width_meters=300)
            inside = point_in_polygon(lat, lng, polygon)

            if inside:
                warning = {
                    "message_type": "RESOURCE_THREAT_WARNING",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "incident_id": zone.ehz_incident_id,
                    "alert_level": "CRITICAL",
                    "warning": {
                        "affected_unit": unit_id,
                        "agency": agency,
                        "unit_current_position": {"lat": lat, "lng": lng},
                        "threat_type": "INSIDE_HAZARD_ZONE",
                        "hazard_agent": "CHEMICAL_PLUME",
                        "instruction": "EVACUATE_IMMEDIATELY",
                        "safe_direction": "SOUTHEAST",
                        "nearest_safe_point": {
                            "lat": zone.staging_lat,
                            "lng": zone.staging_lng,
                            "description": zone.staging_description or "Upwind staging area",
                        } if zone.staging_lat else None,
                        "geofence_trigger": {
                            "zone_type": zone.zone_type,
                            "boundary_crossed": "PLUME_CORRIDOR_POLYGON",
                            "requires_hazmat_ppe": zone.requires_hazmat_ppe,
                        },
                    },
                }

            else:
                # Check proximity to plume source
                if zone.center_lat and zone.center_lng:
                    distance = haversine_distance(lat, lng, zone.center_lat, zone.center_lng)
                    danger_radius = zone.danger_radius_meters or 800
                    if distance <= danger_radius + GEOFENCE_APPROACH_THRESHOLD_METERS:
                        warning = {
                            "message_type": "RESOURCE_THREAT_WARNING",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "incident_id": zone.ehz_incident_id,
                            "alert_level": "WARNING",
                            "warning": {
                                "affected_unit": unit_id,
                                "agency": agency,
                                "unit_current_position": {"lat": lat, "lng": lng},
                                "threat_type": "APPROACHING_HAZARD_ZONE",
                                "hazard_agent": "CHEMICAL_PLUME",
                                "instruction": "DO_NOT_APPROACH",
                                "safe_direction": "SOUTHEAST",
                                "nearest_safe_point": {
                                    "lat": zone.staging_lat,
                                    "lng": zone.staging_lng,
                                    "description": zone.staging_description or "Upwind staging area",
                                } if zone.staging_lat else None,
                                "geofence_trigger": {
                                    "zone_type": zone.zone_type,
                                    "distance_to_source_meters": round(distance),
                                    "danger_radius_meters": danger_radius,
                                },
                            },
                        }

        if warning:
            return warning

    return None


# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """Main WebSocket endpoint for real-time data streaming.
    
    Clients connect and send subscription messages to indicate which
    incidents they want updates for. The server broadcasts position
    updates, status changes, clearance events, and geofence alerts.
    
    Client messages:
        {"action": "SUBSCRIBE", "incident_id": "EHZ-...", "agency": "SAMU"}
        {"action": "SUBSCRIBE_ALL", "agency": "RNP"}
        {"action": "PING"}
    
    Server broadcasts:
        RESOURCE_POSITION_UPDATE
        RESOURCE_STATUS_CHANGE
        SCENE_CLEARANCE_DECLARED
        SCENE_CLEARANCE_ACKNOWLEDGED
        RESOURCE_THREAT_WARNING
        INCIDENT_CREATED
        HAZARD_ZONE_UPDATE
    """
    agency = None

    # Accept connection (auth is simplified for MVP WebSocket)
    await manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON"})
                continue

            action = message.get("action", "").upper()

            if action == "SUBSCRIBE":
                incident_id = message.get("incident_id")
                agency = message.get("agency", agency)
                if incident_id:
                    manager.subscribe_to_incident(websocket, incident_id)
                    # Also register agency connection
                    if agency and agency not in manager.agency_connections:
                        manager.agency_connections[agency] = []
                    if agency and websocket not in manager.agency_connections.get(agency, []):
                        manager.agency_connections.setdefault(agency, []).append(websocket)
                    await websocket.send_json({
                        "status": "SUBSCRIBED",
                        "incident_id": incident_id,
                        "agency": agency,
                    })

            elif action == "SUBSCRIBE_ALL":
                agency = message.get("agency", agency)
                if agency:
                    if agency not in manager.agency_connections:
                        manager.agency_connections[agency] = []
                    if websocket not in manager.agency_connections[agency]:
                        manager.agency_connections[agency].append(websocket)
                await websocket.send_json({
                    "status": "SUBSCRIBED_ALL",
                    "agency": agency,
                })

            elif action == "PING":
                await websocket.send_json({"action": "PONG", "timestamp": datetime.now(timezone.utc).isoformat()})

            else:
                await websocket.send_json({"error": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================================
# RESOURCE POSITION UPDATE ENDPOINT (REST)
# ============================================================================

@router.post("/api/v1/resources/position")
async def update_resource_position(
    body: dict,
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Receive a vehicle position update from a fleet GPS system (or simulator).
    
    Checks geofence boundaries and broadcasts to subscribed dispatchers.
    """
    # Validate API key
    from app.core.config import API_KEYS
    key_info = API_KEYS.get(x_api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")

    unit_id = body.get("unit_id", "")
    agency = body.get("agency", "")
    resource_type = body.get("resource_type", "")
    lat = body.get("latitude", 0)
    lng = body.get("longitude", 0)
    heading = body.get("heading_degrees")
    speed = body.get("speed_kmh")
    status = body.get("status", "EN_ROUTE")
    incident_id = body.get("incident_id", "")
    eta_minutes = body.get("eta_minutes")

    # Get display properties
    display = get_resource_display(resource_type)

    now = datetime.now(timezone.utc)

    # Build broadcast message
    position_update = {
        "message_type": "RESOURCE_POSITION_UPDATE",
        "timestamp": now.isoformat(),
        "incident_id": incident_id,
        "resource": {
            "unit_id": unit_id,
            "agency": agency,
            "resource_type": resource_type,
            "icon": display["icon"],
            "color": display["color"],
            "position": {
                "latitude": lat,
                "longitude": lng,
                "heading_degrees": heading,
                "speed_kmh": speed,
            },
            "status": status,
            "eta_minutes": eta_minutes,
            "last_status_change": now.isoformat(),
        },
    }

    # Broadcast to subscribers of this incident
    if incident_id:
        await manager.broadcast_to_incident(incident_id, position_update)
    # Also broadcast to all connected clients
    await manager.broadcast_to_all(position_update)

    # Geofence check
    geofence_warning = None
    if lat and lng:
        query = select(ActiveZone).where(ActiveZone.is_active == True)
        result = await db.execute(query)
        active_zones = result.scalars().all()

        if active_zones:
            geofence_warning = await check_geofence(
                unit_id, agency, lat, lng, active_zones
            )

            if geofence_warning:
                # Broadcast warning to all connected clients
                await manager.broadcast_to_all(geofence_warning)
                # Also broadcast to the specific agency
                await manager.broadcast_to_agency(agency, geofence_warning)

    return {
        "status": "POSITION_UPDATED",
        "unit_id": unit_id,
        "position": {"lat": lat, "lng": lng},
        "geofence_alert": geofence_warning is not None,
        "geofence_warning": geofence_warning,
    }


# ============================================================================
# RESOURCE STATUS CHANGE ENDPOINT
# ============================================================================

@router.post("/api/v1/resources/status")
async def update_resource_status(
    body: dict,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Update a resource's operational status and broadcast to subscribers."""
    from app.core.config import API_KEYS
    key_info = API_KEYS.get(x_api_key)
    if not key_info:
        raise HTTPException(status_code=401, detail="Invalid API key")

    unit_id = body.get("unit_id", "")
    agency = body.get("agency", "")
    resource_type = body.get("resource_type", "")
    incident_id = body.get("incident_id", "")
    previous_status = body.get("previous_status", "")
    new_status = body.get("new_status", "")
    detail = body.get("detail", "")
    scene_update = body.get("scene_update")

    display = get_resource_display(resource_type)
    now = datetime.now(timezone.utc)

    status_change = {
        "message_type": "RESOURCE_STATUS_CHANGE",
        "timestamp": now.isoformat(),
        "incident_id": incident_id,
        "resource": {
            "unit_id": unit_id,
            "agency": agency,
            "resource_type": resource_type,
            "icon": display["icon"],
            "color": display["color"],
        },
        "previous_status": previous_status,
        "new_status": new_status,
        "detail": detail,
        "scene_update": scene_update,
    }

    # Broadcast
    if incident_id:
        await manager.broadcast_to_incident(incident_id, status_change)
    await manager.broadcast_to_all(status_change)

    return {
        "status": "STATUS_UPDATED",
        "unit_id": unit_id,
        "new_status": new_status,
    }


# ============================================================================
# BROADCAST HELPERS (called from routes.py)
# ============================================================================

async def broadcast_incident_created(incident_data: dict):
    """Broadcast a new incident notification to all connected clients."""
    message = {
        "message_type": "INCIDENT_CREATED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident": {
            "ehz_incident_id": incident_data.get("ehz_incident_id"),
            "canonical_type": incident_data.get("canonical_type"),
            "canonical_severity": incident_data.get("canonical_severity"),
            "mci_declared": incident_data.get("mci_declared"),
            "estimated_casualties": incident_data.get("estimated_casualties"),
            "location": {
                "lat": incident_data.get("location", {}).get("latitude"),
                "lng": incident_data.get("location", {}).get("longitude"),
                "description": incident_data.get("location", {}).get("address_text"),
            },
            "source_agency": incident_data.get("source_agency"),
            "access_restriction": incident_data.get("access_restriction"),
            "hazard_zone": incident_data.get("hazard_zone"),
        },
    }
    await manager.broadcast_to_all(message)


async def broadcast_clearance_declared(incident_id: str, declaring_agency: str, scope: dict):
    """Broadcast a clearance declaration to all connected clients."""
    message = {
        "message_type": "SCENE_CLEARANCE_DECLARED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "declared_by": declaring_agency,
        "clearance_scope": scope,
        "clearance_status": "AWAITING_ACKNOWLEDGMENT",
    }
    await manager.broadcast_to_all(message)


async def broadcast_clearance_acknowledged(incident_id: str, acknowledging_agency: str, scope: dict):
    """Broadcast a clearance acknowledgment to all connected clients."""
    message = {
        "message_type": "SCENE_CLEARANCE_ACKNOWLEDGED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "acknowledged_by": acknowledging_agency,
        "clearance_scope": scope,
        "clearance_status": "CONFIRMED",
    }
    await manager.broadcast_to_all(message)


async def broadcast_hazard_zone_update(incident_id: str, updated_zone: dict):
    """Broadcast a hazard zone update (e.g., wind shift) to all clients."""
    message = {
        "message_type": "HAZARD_ZONE_UPDATE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "updated_hazard_zone": updated_zone,
    }
    await manager.broadcast_to_all(message)
