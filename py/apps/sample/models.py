"""Pydantic models for all three FDEBench task contracts.

These match the JSON schemas in py/data/task{1,2,3}/ and the
required response keys validated by the platform's preflight check.
"""

from enum import Enum
from typing import Any
from typing import Literal

from pydantic import EmailStr

from ms.common.models.base import FrozenBaseModel

# Task 1: Signal Triage


class Reporter(FrozenBaseModel):
    name: str
    email: EmailStr
    department: str


class TriageRequest(FrozenBaseModel):
    ticket_id: str
    subject: str
    description: str
    reporter: Reporter
    created_at: str
    channel: Literal["subspace_relay", "holodeck_comm", "bridge_terminal", "emergency_beacon"]
    attachments: list[str] = []


class Category(str, Enum):
    ACCESS = "Crew Access & Biometrics"
    HULL = "Hull & Structural Systems"
    COMMS = "Communications & Navigation"
    SOFTWARE = "Flight Software & Instruments"
    THREAT = "Threat Detection & Containment"
    DATA = "Telemetry & Data Banks"
    BRIEFING = "Mission Briefing Request"
    NOT_SIGNAL = "Not a Mission Signal"


class Team(str, Enum):
    IDENTITY = "Crew Identity & Airlock Control"
    SYSTEMS = "Spacecraft Systems Engineering"
    COMMS = "Deep Space Communications"
    SOFTWARE = "Mission Software Operations"
    THREAT = "Threat Response Command"
    TELEMETRY = "Telemetry & Data Core"
    NONE = "None"


class MissingInfo(str, Enum):
    AFFECTED_SUBSYSTEM = "affected_subsystem"
    ANOMALY_READOUT = "anomaly_readout"
    SEQUENCE_TO_REPRODUCE = "sequence_to_reproduce"
    AFFECTED_CREW = "affected_crew"
    HABITAT_CONDITIONS = "habitat_conditions"
    STARDATE = "stardate"
    PREVIOUS_SIGNAL_ID = "previous_signal_id"
    CREW_CONTACT = "crew_contact"
    MODULE_SPECS = "module_specs"
    SOFTWARE_VERSION = "software_version"
    SECTOR_COORDINATES = "sector_coordinates"
    MISSION_IMPACT = "mission_impact"
    RECURRENCE_PATTERN = "recurrence_pattern"
    SENSOR_LOG_OR_CAPTURE = "sensor_log_or_capture"
    BIOMETRIC_METHOD = "biometric_method"
    SYSTEM_CONFIGURATION = "system_configuration"


# The model returns every field except ticket_id; the server injects ticket_id
# from the request during guardrails. Validating raw model output against
# TriageDecision (not TriageResponse) lets a compliant reply -- which omits
# ticket_id -- parse cleanly.
class TriageDecision(FrozenBaseModel):
    category: Category
    priority: Literal["P1", "P2", "P3", "P4"]
    assigned_team: Team
    needs_escalation: bool
    missing_information: list[MissingInfo]
    next_best_action: str
    remediation_steps: list[str]


class TriageResponse(TriageDecision):
    ticket_id: str


# Task 2: Document Extraction


class ExtractRequest(FrozenBaseModel):
    document_id: str
    content: str  # base64-encoded document image
    content_format: str = "image_base64"
    json_schema: str | None = None  # JSON schema describing expected output


# Task 3: Workflow Orchestration


class ToolParameter(FrozenBaseModel):
    name: str
    type: str
    description: str
    required: bool | None = None


class ToolDefinition(FrozenBaseModel):
    name: str
    description: str
    endpoint: str
    parameters: list[ToolParameter] | dict[str, str]


class OrchestrateRequest(FrozenBaseModel):
    task_id: str
    goal: str
    available_tools: list[ToolDefinition]
    constraints: list[str] = []
    mock_service_url: str | None = None


class StepExecuted(FrozenBaseModel):
    step: int
    tool: str
    parameters: dict[str, Any]
    result_summary: str = ""
    success: bool = True


WorkflowStatus = Literal["completed", "partial", "failed"]


class OrchestrateResponse(FrozenBaseModel):
    task_id: str
    status: WorkflowStatus
    steps_executed: list[StepExecuted]
    accounts_processed: int | None = None
    emails_sent: int | None = None
    emails_skipped: int | None = None
    skip_reasons: dict[str, int] | None = None
    constraints_satisfied: list[str] = []
