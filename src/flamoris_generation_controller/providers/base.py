"""Small provider-neutral contracts used by the process-owned Hub authority."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

JobStatus = Literal[
    "submitting",
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "unknown",
]


class ProviderError(RuntimeError):
    """A bounded provider failure safe to surface as an MCP tool error."""


class SubmissionRejected(ProviderError, ValueError):
    """Definite rejection: no generation POST can have been accepted."""


class SubmissionUnknown(ProviderError):
    """Acceptance is uncertain; retain the ordinary JobStore reservation. Never replay."""


def execution_identity(value: object, *, allow_empty: bool = False) -> str:
    """Validate an opaque bounded provider identity before observation or journaling."""
    if (
        not isinstance(value, str)
        or len(value) > 512
        or (not value and not allow_empty)
        or value != value.strip()
        or (value and not value.isprintable())
    ):
        raise ValueError("Invalid provider execution identity")
    return value


@dataclass(frozen=True)
class ProviderHealth:
    available: bool
    details: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {**self.details, "available": self.available}


@dataclass(frozen=True)
class GenerationRequest:
    """Validated generation recipe passed to the explicitly selected provider."""

    operation: str
    workflow_id: str
    payload: object


@dataclass(frozen=True)
class ProviderJob:
    execution_id: str
    managed_inputs: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputRole:
    """Adapter-assigned public role identity; never inferred from a filename.

    The index distinguishes items of one declared collection. Domain/format
    qualification belongs to the reviewed adapter, not this transport envelope.
    """

    port: str
    role: str
    index: int = 0

    def __post_init__(self):
        if any(
            type(value) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value)
            for value in (self.port, self.role)
        ):
            raise ValueError("Invalid output port/role")
        if type(self.index) is not int or not 0 <= self.index < 128:
            raise ValueError("Invalid output role index")

    def as_dict(self) -> dict[str, object]:
        return {"port": self.port, "role": self.role, "role_index": self.index}


def output_roles(outputs: list[dict]) -> list[dict[str, object]]:
    """Validate a bounded projection, including archives from before role metadata."""
    if type(outputs) is not list or len(outputs) > 64:
        raise ValueError("Invalid output role manifest")
    roles, identities, port_roles, role_ports = [], set(), {}, {}
    for output in outputs:
        if type(output) is not dict:
            raise ValueError("Invalid output role manifest")
        fields = {name for name in ("port", "role", "role_index") if name in output}
        if not fields:
            roles.append({})
            continue
        if fields != {"port", "role", "role_index"}:
            raise ValueError("Incomplete output role identity")
        role = OutputRole(output["port"], output["role"], output["role_index"])
        identity = (role.role, role.index)
        if identity in identities:
            raise ValueError("Duplicate output role identity")
        if port_roles.get(role.port, role.role) != role.role or (
            role_ports.get(role.role, role.port) != role.port
        ):
            raise ValueError("Conflicting output port/role mapping")
        identities.add(identity)
        port_roles[role.port] = role.role
        role_ports[role.role] = role.port
        roles.append(role.as_dict())
    return roles


@dataclass(frozen=True)
class ProviderOutput:
    output_id: str
    filename: str
    media_kind: str
    mime_type: str
    role: OutputRole | None = None

    def as_dict(self) -> dict[str, object]:
        if self.role is not None and not isinstance(self.role, OutputRole):
            raise ValueError("Invalid provider output role")
        return {
            "output_id": self.output_id,
            "filename": self.filename,
            "media_kind": self.media_kind,
            "mime_type": self.mime_type,
            **(self.role.as_dict() if self.role is not None else {}),
        }


@dataclass(frozen=True)
class JobSnapshot:
    status: JobStatus
    error: Mapping[str, object] | None = None
    outputs: tuple[ProviderOutput, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            **self.metadata,
            "status": self.status,
            "error": dict(self.error) if self.error is not None else None,
            "outputs": [output.as_dict() for output in self.outputs],
        }


@runtime_checkable
class GenerationProvider(Protocol):
    provider_id: str

    async def health(self) -> ProviderHealth: ...

    async def submit(self, request: GenerationRequest, job_id: str) -> ProviderJob: ...

    async def inspect(self, execution_id: str) -> JobSnapshot: ...

    async def cancel(self, execution_id: str) -> JobSnapshot: ...

    async def materialize(self, execution_id: str, output_id: str) -> bytes: ...

    async def close(self) -> None: ...
