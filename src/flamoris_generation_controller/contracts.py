"""Versioned, transport-neutral request and trusted ingress contracts."""

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import ModelKind
from .provenance import ExternalProvenance

API_PATH = "/api/v1/generation"
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 2 * 1024**2
OpaqueID = Annotated[str, Field(min_length=1, max_length=512)]
UUID = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", max_length=32)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ModelsList(Request):
    kind: ModelKind | None = None


class ModelGet(Request):
    # Retain the domain's 1024-byte filename plus the longest kind prefix.
    model_id: Annotated[str, Field(min_length=1, max_length=1040)]


class CapabilityGet(Request):
    capability_id: OpaqueID


class Build(Request):
    template: Annotated[str, Field(min_length=1, max_length=128)]
    parameters: dict[str, Any]
    definition_version: Annotated[int, Field(ge=1)] | None = None
    definition_digest: Digest | None = None
    require_ready: bool = False


class Recipe(Request):
    workflow_id: OpaqueID


class Job(Request):
    job_id: OpaqueID


class Asset(Request):
    asset_id: OpaqueID


class AssetRead(Asset):
    sha256: Digest
    offset: Annotated[int, Field(ge=0, le=4 * 1024**3)]
    length: Annotated[int, Field(ge=1, le=256 * 1024)] = 256 * 1024


class Input(Request):
    input_id: OpaqueID


class Upload(Request):
    upload_id: UUID


class UploadBegin(Upload):
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: Annotated[int, Field(ge=1, le=8 * 1024**2)]
    sha256: Digest


class UploadWrite(Upload):
    offset: Annotated[int, Field(ge=0, lt=8 * 1024**2)]
    data_base64: Annotated[str, Field(min_length=1, max_length=349528)]
    chunk_sha256: Digest


OPERATIONS: dict[str, type[Request]] = {
    "system.health": Request,
    "capabilities.list": Request,
    "capabilities.get": CapabilityGet,
    "models.list": ModelsList,
    "models.get": ModelGet,
    "workflows.list": Request,
    "workflows.build": Build,
    "workflows.save": Recipe,
    "jobs.submit": Recipe,
    "jobs.status": Job,
    "jobs.result": Job,
    "jobs.cancel": Job,
    "assets.list": Job,
    "assets.delete": Asset,
    "assets.get": Asset,
    "assets.prepare": Asset,
    "assets.read": AssetRead,
    "inputs.create": Asset,
    "inputs.get": Input,
    "inputs.delete": Input,
    "inputs.upload.begin": UploadBegin,
    "inputs.upload.write": UploadWrite,
    "inputs.upload.finish": Upload,
}


@dataclass(frozen=True)
class CallerContext:
    """Trusted adapter value, never accepted from request JSON or identity headers.

    Service permission is separate from Studio user ownership and provenance.
    Library callers are trusted code in the authority process.
    """

    source: Literal["internal-service", "external-mcp"]
    permissions: frozenset[str]
    provenance: ExternalProvenance | None = None

    @classmethod
    def internal(cls):
        return cls("internal-service", frozenset(OPERATIONS))

    @classmethod
    def external(cls, provenance: ExternalProvenance | None = None):
        return cls("external-mcp", frozenset(OPERATIONS), provenance)


@dataclass(frozen=True)
class AssetContent:
    data: bytes
    mime_type: str
    format: str


class ControllerError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
