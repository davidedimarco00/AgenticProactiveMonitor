from .anomalies import AnomalyObservation
from .contracts import (
    AgentTaskRepositoryPort,
    AnomalyInboxPort,
    DetectorContextPort,
    IncidentAssigneePort,
    IncidentAssigneeReceipt,
    IncidentRepositoryPort,
    IncidentTriageReceipt,
    InvestigationTaskDispatchReceipt,
    InvestigationTaskResultReceipt,
)
from .coordinator import IncidentCoordinator as BaseIncidentCoordinator
from .ingestion import AnomalyIntake
from .models import IncidentWorkflowResult
from .policies import ACTIVE_INCIDENT_STATUSES, IncidentCorrelationPolicy
from .react_coordinator import ReActIncidentCoordinator
from .reporting import build_incident_report
from .tasks import (
    AgentTaskState,
    AgentTaskWorkflow,
    InvalidTaskTransition,
    TaskRecoverySummary,
    validate_task_transition,
)
from .workflow import IncidentWorkflow

# The ReAct branch keeps the existing import surface stable: application code and
# tests that import IncidentCoordinator receive the ReAct-aware extension, while
# BaseIncidentCoordinator remains available for isolated state-machine tests.
IncidentCoordinator = ReActIncidentCoordinator

__all__ = [
    "ACTIVE_INCIDENT_STATUSES",
    "AgentTaskRepositoryPort",
    "AgentTaskState",
    "AgentTaskWorkflow",
    "AnomalyInboxPort",
    "AnomalyIntake",
    "AnomalyObservation",
    "BaseIncidentCoordinator",
    "DetectorContextPort",
    "IncidentAssigneePort",
    "IncidentAssigneeReceipt",
    "IncidentCoordinator",
    "IncidentCorrelationPolicy",
    "IncidentRepositoryPort",
    "IncidentTriageReceipt",
    "InvestigationTaskDispatchReceipt",
    "InvestigationTaskResultReceipt",
    "IncidentWorkflow",
    "IncidentWorkflowResult",
    "InvalidTaskTransition",
    "ReActIncidentCoordinator",
    "TaskRecoverySummary",
    "build_incident_report",
    "validate_task_transition",
]
