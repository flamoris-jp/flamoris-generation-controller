"""Process-owned Hub jobs, exclusivity and generated asset authority."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .asset_files import AssetFiles
from .capabilities import CapabilityRegistry
from .durable import CommitUnknown, Records
from .provenance import ExternalProvenance, archived_provenance, provenance_metadata
from .providers import GenerationRequest, JobSnapshot, ProviderRegistry
from .providers.base import (
    ProviderJob,
    SubmissionRejected,
    SubmissionUnknown,
    execution_identity,
    output_roles,
)
from .retention import RetentionStore
from .workflows import NATIVE_RECIPES, AnyRecipe, RetiredRecipeError, WorkflowStore, checked_id

TERMINAL = {"completed", "failed", "cancelled"}


class GenerationBusyError(ValueError):
    """A second generation cannot start while this process owns active work."""


MAX_ASSET_BYTES = 64 * 1024 * 1024
MEDIA_TYPES = {
    ".wav": ("audio", "audio/wav", "wav"),
    ".mp3": ("audio", "audio/mpeg", "mp3"),
    ".mp4": ("video", "video/mp4", "mp4"),
    ".mid": ("midi", "audio/midi", "midi"),
    ".abc": ("score", "text/vnd.abc", "abc"),
    ".json": ("metadata", "application/json", "json"),
    ".psd": ("image", "image/vnd.adobe.photoshop", "psd"),
    ".png": ("image", "image/png", "png"),
    ".jpg": ("image", "image/jpeg", "jpeg"),
    ".jpeg": ("image", "image/jpeg", "jpeg"),
    ".webp": ("image", "image/webp", "webp"),
}


@dataclass
class Job:
    job_id: str
    workflow_id: str
    operation: str
    recipe: AnyRecipe
    provider_id: str
    provider_execution_id: str
    managed_inputs: dict[str, dict] = field(default_factory=dict)
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="queued"))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    deleted_outputs: set[int] = field(default_factory=set)
    external_provenance: ExternalProvenance | None = None


class JobStore:
    def __init__(
        self,
        workflows: WorkflowStore,
        providers: ProviderRegistry,
        capabilities: CapabilityRegistry,
        output_dir: Path,
        retention: RetentionStore | None = None,
    ):
        self.workflows = workflows
        self.providers = providers
        self.capabilities = capabilities
        self.output_dir = output_dir
        self.retention = retention
        self._jobs: dict[str, Job] = {}
        self._archived_locks = tuple(asyncio.Lock() for _ in range(64))
        self._submit_lock = asyncio.Lock()
        self._active_job_id: str | None = None
        self._authority = Records(output_dir, "job-authority")
        self._retired_active: dict | None = None
        self._recover()

    def _recover(self):
        record = self._authority.read("active.json")
        if record is None or record == {"active": None}:
            return
        try:
            raw = record["active"]
            job_id = checked_id(raw["job_id"])
            workflow_id = checked_id(raw["workflow_id"])
            execution_id = execution_identity(raw["execution_id"], allow_empty=True)
            recipe_data = raw["recipe"]
            if not isinstance(recipe_data, dict):
                raise ValueError("Invalid reservation recipe")
            retired = (
                recipe_data.get("schema_version") in (2, 3)
                or raw.get("runtime_delegation") is not None
                or raw.get("definition") is not None
                or bool(raw.get("verification"))
            )
            if retired:
                # Preserve opaque feature debt without importing/executing retired
                # code, polling providers or modifying the original journal.
                self._retired_active = {
                    "job_id": job_id,
                    "workflow_id": workflow_id,
                    "provider_execution_id": execution_id,
                    "status": "unknown",
                    "error": {"code": "retired_feature_requires_reconciliation"},
                    "retired_feature": True,
                }
                self._active_job_id = job_id
                return
            recipe = self.workflows.parse(recipe_data)
            provider = self.providers.get(raw["provider_id"])
            if isinstance(recipe, NATIVE_RECIPES) and (
                (raw["provider_id"], raw["operation"]) != self.workflows.routing(recipe)
            ):
                raise ValueError("Invalid native reservation route")
            if not isinstance(recipe, NATIVE_RECIPES) and (
                raw["provider_id"],
                raw["operation"],
            ) != ("comfyui", "image.generate"):
                raise ValueError("Invalid builtin reservation route")
            job = Job(
                job_id,
                workflow_id,
                raw["operation"],
                recipe,
                raw["provider_id"],
                execution_id,
                managed_inputs=self._managed_input_metadata(raw.get("managed_inputs", {})),
                snapshot=JobSnapshot(status="unknown"),
                external_provenance=ExternalProvenance.model_validate(raw["external_provenance"])
                if "external_provenance" in raw
                else None,
            )
            if job.provider_execution_id and hasattr(provider, "_output_nodes"):
                provider._output_nodes[job.provider_execution_id] = "7"
            self._jobs[job_id] = job
            self._active_job_id = job_id
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid execution reservation; reconcile before starting") from exc

    def _persist_active(self, job):
        self._authority.write(
            "active.json",
            {
                "active": {
                    "job_id": job.job_id,
                    "workflow_id": job.workflow_id,
                    "operation": job.operation,
                    "provider_id": job.provider_id,
                    "execution_id": job.provider_execution_id,
                    "recipe": job.recipe.model_dump(mode="json"),
                    "managed_inputs": job.managed_inputs,
                    **provenance_metadata(job.external_provenance),
                }
            },
        )

    def _active_job(self) -> tuple[str, str] | None:
        if self._active_job_id is None:
            return None
        if self._retired_active is not None:
            return self._active_job_id, "unknown (retired feature; reconcile)"
        job = self._jobs.get(self._active_job_id)
        return self._active_job_id, job.snapshot.status if job else "submitting"

    @staticmethod
    def _managed_input_metadata(value):
        """Persist only bounded immutable identity fields, never adapter staging paths."""
        if not isinstance(value, dict) or len(value) > 4:
            raise ValueError("Invalid managed input reproducibility metadata")
        result = {}
        for name, metadata in value.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name)
                or not isinstance(metadata, dict)
            ):
                raise ValueError("Invalid managed input reproducibility metadata")
            keys = ("input_id", "source_asset_id", "sha256", "mime_type", "size_bytes")
            try:
                record = {key: metadata[key] for key in keys}
                checked_id(record["input_id"])
                if record["source_asset_id"] is None:
                    if (
                        metadata.get("source_kind") != "upload"
                        or record["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}
                        or type(record["size_bytes"]) is not int
                        or not 1 <= record["size_bytes"] <= 8 * 1024**2
                    ):
                        raise ValueError("Invalid uploaded input provenance")
                    record["source_kind"] = "upload"
                else:
                    JobStore._parse_asset_id(record["source_asset_id"])
                if (
                    not isinstance(record["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                    or record["mime_type"]
                    not in {"image/png", "image/jpeg", "image/webp", "audio/wav"}
                    or type(record["size_bytes"]) is not int
                    or not 1 <= record["size_bytes"] <= 64 * 1024**2
                ):
                    raise ValueError("Invalid managed input reproducibility metadata")
            except (KeyError, TypeError):
                raise ValueError("Invalid managed input reproducibility metadata") from None
            result[name] = record
        return result

    async def _release_if_terminal(self, job_id: str, job: Job) -> None:
        if job.snapshot.status not in TERMINAL:
            return
        async with self._submit_lock:
            if self._active_job_id == job_id:
                self._authority.write("active.json", {"active": None})
                self._active_job_id = None

    def _validated_request(self, workflow_id: str) -> GenerationRequest:
        recipe = self.workflows.get(workflow_id)
        capability = self.capabilities.resolve_workflow(recipe.template)
        route = capability.provider_id, capability.capability_id
        if route != self.workflows.routing(recipe):
            raise ValueError("Recipe provider/capability registration mismatch")
        self.providers.get(capability.provider_id)
        return GenerationRequest(
            operation=capability.capability_id,
            workflow_id=workflow_id,
            payload=recipe,
        )

    def _provider_route(self, request: GenerationRequest) -> str:
        return self.workflows.routing(request.payload)[0]

    async def _reserve(self, workflow_id: str, *, provenance=None) -> Job:
        if provenance is not None and not isinstance(provenance, ExternalProvenance):
            raise ValueError("External provenance must come from trusted ingress")
        request = self._validated_request(workflow_id)
        async with self._submit_lock:
            active = self._active_job()
            if active is not None:
                active_job_id, state = active
                raise GenerationBusyError(
                    f"Generation is busy: active job {active_job_id} is {state}; "
                    "poll jobs.status/jobs.result or cancel it before submitting again"
                )
            if len(self._jobs) >= 1024:
                raise ValueError("Job session is full; retrieve results before restarting")
            job_id = uuid4().hex
            request = self._validated_request(workflow_id)
            job = Job(
                job_id,
                workflow_id,
                request.operation,
                request.payload,
                self._provider_route(request),
                "",
                snapshot=JobSnapshot(status="submitting"),
                external_provenance=provenance,
            )
            # Journal the ordinary reservation before any provider await. Restart is
            # never proof that work vanished, including the accepted-POST crash window.
            self._active_job_id = job_id
            self._jobs[job_id] = job
            try:
                self._persist_active(job)
            except CommitUnknown:
                job.snapshot = JobSnapshot(status="unknown")
                raise
            except BaseException:
                self._active_job_id = None
                del self._jobs[job_id]
                raise
        return job

    async def submit(self, workflow_id: str, *, provenance=None) -> dict:
        job = await self._reserve(workflow_id, provenance=provenance)
        job_id, recipe = job.job_id, job.recipe
        operation = job.operation
        provider = self.providers.get(job.provider_id)

        try:
            provider_job = await provider.submit(
                GenerationRequest(
                    operation=operation,
                    workflow_id=workflow_id,
                    payload=recipe,
                ),
                job_id,
            )
        except SubmissionRejected:
            job.snapshot = JobSnapshot(status="failed")
            await self._release_if_terminal(job_id, job)
            raise
        except BaseException as exc:
            job.snapshot = JobSnapshot(status="unknown", error={"code": "submission_unknown"})
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SubmissionUnknown(
                "submission_unknown: keep reservation; reconcile before retry"
            ) from exc

        try:
            if not isinstance(provider_job, ProviderJob):
                raise ValueError("Invalid provider submission acknowledgement")
            job.provider_execution_id = execution_identity(provider_job.execution_id)
            job.snapshot = JobSnapshot(status="queued")
            job.managed_inputs = self._managed_input_metadata(dict(provider_job.managed_inputs))
            self._persist_active(job)
        except BaseException as exc:
            job.snapshot = JobSnapshot(status="unknown", error={"code": "submission_unknown"})
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SubmissionUnknown(
                "submission_unknown: invalid acknowledgement or accepted job journal failed"
            ) from None
        return self._metadata(job)

    def _get(self, job_id: str) -> Job:
        checked_id(job_id)
        if self._retired_active is not None and self._retired_active["job_id"] == job_id:
            raise RetiredRecipeError(
                "Retired execution remains reserved; reconcile with the previous version"
            )
        if job_id not in self._jobs:
            raise ValueError("Unknown job ID; jobs belong to this server process")
        return self._jobs[job_id]

    def _metadata(self, job: Job) -> dict:
        snapshot = job.snapshot.as_dict()
        # Provider response metadata is untrusted and cannot grant client provenance.
        snapshot.pop("external_provenance", None)
        return {
            **snapshot,
            "job_id": job.job_id,
            "operation": job.operation,
            "provider": job.provider_id,
            "provider_id": job.provider_id,
            "provider_execution_id": job.provider_execution_id,
            "workflow_id": job.workflow_id,
            "managed_inputs": {
                name: dict(metadata) for name, metadata in job.managed_inputs.items()
            },
            **provenance_metadata(job.external_provenance),
            **job.recipe.model_dump(mode="json"),
        }

    async def _refresh(self, job_id: str, job: Job) -> None:
        if job.snapshot.status not in TERMINAL and job.provider_execution_id:
            provider = self.providers.get(job.provider_id)
            job.snapshot = await provider.inspect(job.provider_execution_id)
        await self._release_if_terminal(job_id, job)
        if job.snapshot.status == "completed":
            self._archive_metadata(job)
            if self.retention is not None:
                try:
                    self.retention.record(job.provider_id, job.provider_execution_id, job_id)
                except (ValueError, OSError, TypeError, KeyError):
                    # Maintenance receipts must not turn successful generation into failure.
                    logging.getLogger(__name__).warning("Retention capture skipped job=%s", job_id)

    def _archive_metadata(self, job: Job) -> None:
        """Persist identities without downloading media or restoring execution authority."""
        record = self._metadata(job)
        output_roles(record["outputs"])
        record["files"] = []
        for index in range(len(job.snapshot.outputs)):
            if index in job.deleted_outputs:
                continue
            _, path = self._asset_metadata(job.job_id, job, index)
            with AssetFiles(self.output_dir, job.job_id) as files:
                record["files"].append({"file": str(path), "size_bytes": files.size(path.name)})
        payload = json.dumps(record, indent=2).encode()
        if len(payload) > 1024 * 1024:
            raise ValueError("Asset metadata exceeds archive limit")
        with AssetFiles(self.output_dir, job.job_id) as files:
            files.write("metadata.json", payload)

    async def status(self, job_id: str) -> dict:
        checked_id(job_id)
        if self._retired_active is not None and self._retired_active["job_id"] == job_id:
            return dict(self._retired_active)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_status(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            return self._metadata(job)

    async def _archived_status(self, job_id: str) -> dict:
        """Bounded completed catalog metadata; no execution recovery or materialization."""
        async with self._archived_lock(job_id):
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                raw = files.read("metadata.json", 1024 * 1024)
                try:
                    record = json.loads(raw) if raw is not None else None
                    if not isinstance(record, dict) or record.get("status") != "completed":
                        raise ValueError
                    if record.get("job_id") != job_id:
                        raise ValueError
                    outputs = record["outputs"]
                    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 64:
                        raise ValueError
                    output_roles(outputs)
                    provider = record["provider_id"]
                    operation = record["operation"]
                    if (
                        not isinstance(provider, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", provider)
                        or record.get("provider", provider) != provider
                        or not isinstance(operation, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", operation)
                    ):
                        raise ValueError
                    workflow_id = checked_id(record["workflow_id"])
                    provenance = archived_provenance(record)
                    managed_inputs = self._managed_input_metadata(record.get("managed_inputs", {}))
                    available = 0
                    for index in range(len(outputs)):
                        path, deleted = self._archived_file(job_id, index, files)
                        if path is not None and not deleted:
                            files.size(path.name)  # Reject symlinks without fetching asset bytes.
                            available += 1
                    if not available:
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise ValueError("Invalid or unavailable archived completed job") from None
                return {
                    "job_id": job_id,
                    "status": "completed",
                    "provider_id": provider,
                    "provider": provider,
                    "operation": operation,
                    "workflow_id": workflow_id,
                    "archived": True,
                    "output_count": available,
                    **({"managed_inputs": managed_inputs} if managed_inputs else {}),
                    **provenance,
                }

    def _local_output_path(self, job_id: str, index: int, suffix: str) -> Path:
        checked_id(job_id)
        if suffix not in MEDIA_TYPES:
            raise ValueError("Unsupported generated media output extension")
        return self.output_dir / job_id / f"{index:03d}{suffix}"

    async def result(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            result = self._metadata(job)
            result["files"] = []
            if job.snapshot.status == "completed":
                provider = self.providers.get(job.provider_id)
                for index, output in enumerate(job.snapshot.outputs):
                    suffix = Path(output.filename).suffix.lower()
                    if index in job.deleted_outputs:
                        continue
                    path = self._local_output_path(job_id, index, suffix)
                    with AssetFiles(self.output_dir, job_id) as files:
                        present = files.size(path.name) is not None
                    if not present:
                        data = await provider.materialize(
                            job.provider_execution_id, output.output_id
                        )
                        with AssetFiles(self.output_dir, job_id) as files:
                            files.write(path.name, data)
                    with AssetFiles(self.output_dir, job_id) as files:
                        result["files"].append(
                            {"file": str(path), "size_bytes": files.size(path.name)}
                        )
                with AssetFiles(self.output_dir, job_id) as files:
                    files.write("metadata.json", json.dumps(result, indent=2).encode())
            return result

    @staticmethod
    def _asset_id(job_id: str, index: int) -> str:
        return f"{job_id}:{index:03d}"

    @staticmethod
    def _parse_asset_id(asset_id: str) -> tuple[str, int]:
        match = re.fullmatch(r"([0-9a-f]{32}):([0-9]{3})", asset_id)
        if not match:
            raise ValueError("Asset ID must come from assets.list")
        return match.group(1), int(match.group(2))

    def _archived_lock(self, job_id: str) -> asyncio.Lock:
        # A fixed number of locks bounds memory without limiting stored job history.
        return self._archived_locks[int(job_id[:8], 16) % len(self._archived_locks)]

    def _archived_file(
        self, job_id: str, index: int, files: AssetFiles
    ) -> tuple[Path | None, bool]:
        """Resolve only materialized outputs recorded by this Hub, never metadata paths."""
        manifest = files.read("metadata.json", 1024 * 1024)
        tombstone = files.read(".deleted-assets.json", 4096)
        if manifest is None:
            raise ValueError("Unknown archived asset ID")
        try:
            record = json.loads(manifest)
            deleted = json.loads(tombstone) if tombstone is not None else []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid archived asset record") from exc
        if (
            not isinstance(record, dict)
            or record.get("job_id") != job_id
            or record.get("status") != "completed"
            or not isinstance(record.get("files"), list)
            or len(record["files"]) > 64
            or not isinstance(deleted, list)
            or len(deleted) > 64
            or any(type(i) is not int or i < 0 or i > 999 for i in deleted)
        ):
            raise ValueError("Invalid archived asset record")
        prefix = f"{index:03d}"
        paths = []
        for item in record["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("file"), str):
                raise ValueError("Invalid archived asset record")
            name = Path(item["file"]).name
            suffix = Path(name).suffix.lower()
            if name == prefix + suffix and suffix in MEDIA_TYPES:
                paths.append(self._local_output_path(job_id, index, suffix))
        if len(paths) > 1:
            raise ValueError("Ambiguous archived asset")
        if not paths and index not in deleted:
            raise ValueError("Unknown archived asset ID")
        return (paths[0] if paths else None), index in deleted

    @staticmethod
    def _archived_roles(files: AssetFiles, index: int) -> dict:
        raw = files.read("metadata.json", 1024 * 1024)
        try:
            record = json.loads(raw) if raw is not None else None
            # Very old archives may have no output vector. Absence is unclassified.
            outputs = record.get("outputs", [])
            roles = output_roles(outputs)
            return roles[index] if index < len(roles) else {}
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid archived output role manifest") from None

    @staticmethod
    def _archived_provenance(files: AssetFiles) -> dict:
        raw = files.read("metadata.json", 1024 * 1024)
        try:
            record = json.loads(raw) if raw is not None else None
            if not isinstance(record, dict):
                raise ValueError("Invalid archived external provenance")
            return archived_provenance(record)
        except (ValueError, TypeError):
            raise ValueError("Invalid archived external provenance") from None

    async def _archived_get(self, job_id: str, index: int) -> tuple[dict, bytes, str]:
        async with self._archived_lock(job_id):
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                path, deleted = self._archived_file(job_id, index, files)
                if deleted or path is None:
                    raise ValueError("Unknown archived asset ID")
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("Use assets.prepare/assets.read for this media type")
                data = files.read(path.name, MAX_ASSET_BYTES)
                if data is None:
                    raise ValueError("Unknown archived asset ID")
                roles = self._archived_roles(files, index)
                provenance = self._archived_provenance(files)
            kind, mime, media_format = MEDIA_TYPES[path.suffix.lower()]
            size = len(data)
            asset = {
                "asset_id": self._asset_id(job_id, index),
                "job_id": job_id,
                "filename": path.name,
                "media_kind": kind,
                "mime_type": mime,
                "size_bytes": size,
                "materialized": True,
                "output_index": index,
                **roles,
                **provenance,
            }
            return asset, data, media_format

    async def _archived_delete(self, job_id: str, index: int) -> dict:
        async with self._archived_lock(job_id):
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                path, already_deleted = self._archived_file(job_id, index, files)
                if path is None and already_deleted:
                    # An old result call may have rewritten the manifest.
                    matches = [
                        self._local_output_path(job_id, index, suffix)
                        for suffix in MEDIA_TYPES
                        if files.size(f"{index:03d}{suffix}") is not None
                    ]
                    if len(matches) > 1:
                        raise ValueError("Ambiguous archived asset")
                    path = matches[0] if matches else None
                if not already_deleted:
                    prior_bytes = files.read(".deleted-assets.json", 4096)
                    prior = json.loads(prior_bytes) if prior_bytes is not None else []
                    files.write(
                        ".deleted-assets.json",
                        json.dumps(sorted(set(prior) | {index})).encode(),
                    )
                materialized_deleted = files.delete(path.name) if path is not None else False
            return {
                "asset_id": self._asset_id(job_id, index),
                "deleted": True,
                "already_deleted": already_deleted,
                "materialized_deleted": materialized_deleted,
            }

    def _asset_metadata(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        if index in job.deleted_outputs:
            raise ValueError("Unknown asset ID")
        if index >= len(job.snapshot.outputs):
            raise ValueError("Unknown asset ID")
        output = job.snapshot.outputs[index]
        roles = output_roles([item.as_dict() for item in job.snapshot.outputs])[index]
        suffix = Path(output.filename).suffix.lower()
        media_kind, mime_type, _ = MEDIA_TYPES.get(suffix, (None, None, None))
        if media_kind is None:
            raise ValueError("Unsupported generated media output extension")
        if (output.media_kind, output.mime_type) != (media_kind, mime_type):
            raise ValueError("Provider output metadata does not match its filename")
        path = self._local_output_path(job_id, index, suffix)
        with AssetFiles(self.output_dir, job_id) as files:
            size = files.size(path.name)
        materialized = size is not None
        asset = {
            "asset_id": self._asset_id(job_id, index),
            "job_id": job_id,
            "filename": path.name,
            "media_kind": media_kind,
            "mime_type": mime_type,
            "size_bytes": size,
            "materialized": materialized,
            "output_index": index,
            **roles,
            **provenance_metadata(job.external_provenance),
        }
        return asset, path

    async def _materialize_asset(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        asset, path = self._asset_metadata(job_id, job, index)
        if not asset["materialized"]:
            output = job.snapshot.outputs[index]
            provider = self.providers.get(job.provider_id)
            data = await provider.materialize(job.provider_execution_id, output.output_id)
            with AssetFiles(self.output_dir, job_id) as files:
                files.write(path.name, data)
                asset["size_bytes"] = files.size(path.name)
            asset["materialized"] = True
        return asset, path

    async def list_assets(self, job_id: str) -> dict:
        checked_id(job_id)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_list(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            assets = [
                self._asset_metadata(job_id, job, index)[0]
                for index in range(len(job.snapshot.outputs))
                if index not in job.deleted_outputs
            ]
            return {"job_id": job_id, "assets": assets}

    async def _archived_list(self, job_id: str) -> dict:
        async with self._archived_lock(job_id):
            assets = []
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                raw = files.read("metadata.json", 1024 * 1024)
                try:
                    record = json.loads(raw) if raw is not None else None
                    outputs = record["outputs"]
                    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 64:
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ValueError("Invalid archived asset record") from None
                for index in range(len(outputs)):
                    path, deleted = self._archived_file(job_id, index, files)
                    if deleted or path is None:
                        continue
                    kind, mime, _ = MEDIA_TYPES[path.suffix.lower()]
                    size = files.size(path.name)
                    roles = self._archived_roles(files, index)
                    provenance = self._archived_provenance(files)
                    assets.append(
                        {
                            "asset_id": self._asset_id(job_id, index),
                            "job_id": job_id,
                            "filename": path.name,
                            "media_kind": kind,
                            "mime_type": mime,
                            "size_bytes": size,
                            "materialized": size is not None,
                            "output_index": index,
                            **roles,
                            **provenance,
                        }
                    )
            return {"job_id": job_id, "assets": assets}

    async def get_asset(self, asset_id: str) -> tuple[dict, bytes, str]:
        job_id, index = self._parse_asset_id(asset_id)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_get(job_id, index)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            asset, path = await self._materialize_asset(job_id, job, index)
            suffix = path.suffix.lower()
            _, _, media_format = MEDIA_TYPES[suffix]
            with AssetFiles(self.output_dir, job_id) as files:
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    raise ValueError("Use assets.prepare/assets.read for this media type")
                data = files.read(path.name, MAX_ASSET_BYTES)
            if data is None:
                raise ValueError("Generated asset changed while being read")
            return asset, data, media_format

    async def delete_asset(self, asset_id: str) -> dict:
        """Remove one Hub-managed output; provider originals are unaffected."""
        job_id, index = self._parse_asset_id(asset_id)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_delete(job_id, index)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            if index >= len(job.snapshot.outputs):
                raise ValueError("Unknown asset ID")
            # Check the destination even for retries; never unlink an attacker-supplied path.
            suffix = Path(job.snapshot.outputs[index].filename).suffix.lower()
            path = self._local_output_path(job_id, index, suffix)
            already_deleted = index in job.deleted_outputs
            with AssetFiles(self.output_dir, job_id) as files:
                if not already_deleted:
                    files.write(
                        ".deleted-assets.json",
                        json.dumps(sorted(job.deleted_outputs | {index})).encode(),
                    )
                    job.deleted_outputs.add(index)
                materialized_deleted = files.delete(path.name)
            return {
                "asset_id": asset_id,
                "deleted": True,
                "already_deleted": already_deleted,
                "materialized_deleted": materialized_deleted,
            }

    async def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            if job.snapshot.status not in TERMINAL and job.provider_execution_id:
                provider = self.providers.get(job.provider_id)
                job.snapshot = await provider.cancel(job.provider_execution_id)
            await self._release_if_terminal(job_id, job)
            return self._metadata(job)

    def activity(self) -> dict[str, object]:
        active = self._active_job()
        return {
            "busy": active is not None,
            "active_job_id": active[0] if active is not None else None,
            **(
                {"retired_execution_requires_reconciliation": True}
                if self._retired_active is not None
                else {}
            ),
        }
