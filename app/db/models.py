"""
E-Huzanya Gateway — Database Models
=====================================

SQLAlchemy ORM models for persistent storage.
Using SQLite for MVP, swappable to PostgreSQL via config.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Boolean,
    JSON,
    ForeignKey,
)
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class IncidentRecord(Base):
    """Stores the Canonical Intermediate Representation (CIR) for each incident."""

    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ehz_incident_id = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    version = Column(Integer, default=1)

    # Source
    source_agency = Column(String(20), nullable=False)
    source_incident_ref = Column(String(100), nullable=False)
    linked_refs = Column(JSON, default=list)

    # Canonical classification
    canonical_type = Column(String(50), nullable=False, index=True)
    canonical_severity = Column(String(5), nullable=False)
    mci_declared = Column(Boolean, default=False)
    estimated_casualties = Column(Integer, default=0)

    # Location
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    address_text = Column(Text)
    road_name = Column(String(100))
    venue_type = Column(String(50))
    sector = Column(String(50))
    district = Column(String(50))
    province = Column(String(50), default="Kigali")
    location_confidence = Column(String(30))

    # Situation (stored as JSON for flexibility)
    situation = Column(JSON, nullable=False)

    # Access restriction (null if not applicable)
    access_restriction = Column(JSON, default=None)

    # Hazard zone (null if not applicable)
    hazard_zone = Column(JSON, default=None)

    # Delayed casualty risk (null if not applicable)
    delayed_casualty_risk = Column(JSON, default=None)

    # Agencies and resources (stored as JSON arrays)
    agencies_involved = Column(JSON, default=list)
    cross_agency_requests = Column(JSON, default=list)

    # Clearance handshake
    clearance_handshake = Column(JSON, default=dict)

    # Restricted compartments (sovereign data, never shared cross-agency)
    restricted_compartments = Column(JSON, default=dict)

    # Status
    status = Column(String(20), default="ACTIVE", index=True)

    # Relationships
    timeline_events = relationship("TimelineEvent", back_populates="incident", cascade="all, delete-orphan")
    active_zones = relationship("ActiveZone", back_populates="incident", cascade="all, delete-orphan")


class TimelineEvent(Base):
    """Audit trail for every action on an incident."""

    __tablename__ = "timeline_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    event = Column(String(50), nullable=False)
    actor = Column(String(20), nullable=False)
    detail = Column(Text)

    incident = relationship("IncidentRecord", back_populates="timeline_events")


class ActiveZone(Base):
    """Active threat zones and hazard zones for geofencing checks.
    
    Separated from the incident record for fast spatial queries.
    The geofencing engine queries this table on every resource position update.
    """

    __tablename__ = "active_zones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False)
    ehz_incident_id = Column(String(50), nullable=False, index=True)
    zone_type = Column(String(30), nullable=False)  # "HOT_ZONE" or "CHEMICAL_PLUME"

    # For HOT_ZONE (circular): center + radius
    center_lat = Column(Float)
    center_lng = Column(Float)
    radius_meters = Column(Integer)

    # For CHEMICAL_PLUME (polygon): stored as JSON array of coordinate pairs
    plume_corridor = Column(JSON, default=None)
    danger_radius_meters = Column(Integer, default=None)
    evacuation_radius_meters = Column(Integer, default=None)

    # Staging point
    staging_lat = Column(Float)
    staging_lng = Column(Float)
    staging_description = Column(Text)

    # Status
    is_active = Column(Boolean, default=True)
    medical_access_authorized = Column(Boolean, default=False)
    requires_hazmat_ppe = Column(Boolean, default=False)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    incident = relationship("IncidentRecord", back_populates="active_zones")
