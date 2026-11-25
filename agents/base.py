"""Common base interface, metadata, execution context and exceptions for agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import threading
from typing import Any, Callable, Optional


class AgentCancelledError(Exception):
    """Raised when an agent execution is cancelled by user request or parent stop."""


class AgentConfigurationError(ValueError):
    """Raised when agent configuration or enablement prevents execution."""


class AgentExecutionError(RuntimeError):
    """Raised when an agent fails during execution."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class AgentMetadata:
    """Static description and safety boundaries for a registered agent."""

    agent_id: str
    name: str
    role: str  # 'orchestrator' or 'specialist'
    description: str
    capabilities: tuple[str, ...] = ()
    default_enabled: bool = True
    requires_ollama: bool = True
    can_publish: bool = False
    can_spend: bool = False


@dataclass
class AgentResult:
    """Structured output returned by an agent execution."""

    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    model_used: str = ''
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'summary': self.summary,
            'data': self.data,
            'model_used': self.model_used,
            'warnings': list(self.warnings),
        }


class AgentContext:
    """Runtime execution context passed into BaseAgent.execute()."""

    def __init__(
        self,
        *,
        run_id: str,
        mode: str,
        model: str,
        payload: dict[str, Any],
        ollama: Any,
        cancel_event: threading.Event,
        report_progress: Callable[[int, str], None],
        log: Callable[[str, str, str], None],
        run_child: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    ):
        self.run_id = run_id
        self.mode = mode
        self.model = model
        self.payload = dict(payload or {})
        self.ollama = ollama
        self.cancel_event = cancel_event
        self._report_progress = report_progress
        self._log = log
        self._run_child = run_child

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise AgentCancelledError('Execution stopped by user.')

    def progress(self, percent: int, message: str) -> None:
        self.check_cancelled()
        clamped = max(0, min(100, int(percent)))
        self._report_progress(clamped, message)

    def log(self, level: str, message: str, details: str = '') -> None:
        self._log(level.upper(), message, details)

    def execute_child(self, agent_id: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.check_cancelled()
        if self._run_child is None:
            raise AgentExecutionError('Child execution is only available inside an orchestrator run.', retryable=False)
        return self._run_child(agent_id, payload or {})


class BaseAgent(ABC):
    """Abstract base class implemented by every ProdXMart agent."""

    metadata: AgentMetadata

    def validate_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize optional task input payload."""
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise AgentConfigurationError('Agent input payload must be a JSON object.')
        return dict(payload)

    @abstractmethod
    def execute(self, ctx: AgentContext) -> AgentResult:
        """Execute the agent task and return a grounded AgentResult."""
