"""ComfyUI implementation of the provider-neutral generation contract."""

import logging
from pathlib import Path

from ..comfyui import ComfyUIClient
from ..models import ModelCatalog
from ..workflows import Recipe, WorkflowStore, build_prompt
from .base import (
    GenerationRequest,
    JobSnapshot,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    SubmissionRejected,
)
from .comfyui_inputs import ComfyUIInputs
from .comfyui_retention import ComfyUIRetention

logger = logging.getLogger(__name__)

MEDIA_TYPES = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
}
STATUSES = {
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "unknown",
}


class ComfyUIProvider:
    provider_id = "comfyui"

    def __init__(
        self,
        client: ComfyUIClient,
        catalog: ModelCatalog,
        workflows: WorkflowStore | None = None,
        managed_inputs=None,
    ):
        self.client = client
        self.catalog = catalog
        self.workflows = workflows
        self.managed_inputs = managed_inputs
        settings = client.settings
        self.input_copies = (
            ComfyUIInputs(
                settings.comfyui_input_root,
                state_root=settings.output_dir,
                max_files=settings.provider_input_max_files,
                max_bytes=settings.provider_input_max_bytes,
            )
            if settings.comfyui_input_root is not None
            else None
        )
        self._outputs: dict[tuple[str, str], dict] = {}
        self._output_nodes: dict[str, str] = {}

    def retention(self) -> ComfyUIRetention | None:
        root = self.client.settings.comfyui_output_root
        return ComfyUIRetention(root, self._outputs) if root is not None else None

    async def health(self) -> ProviderHealth:
        raw = await self.client.health()
        available = raw.get("available") is True
        return ProviderHealth(
            available=available,
            details={key: value for key, value in raw.items() if key != "available"},
        )

    async def submit(self, request: GenerationRequest, job_id: str) -> ProviderJob:
        if request.operation != "image.generate" or not isinstance(request.payload, Recipe):
            raise SubmissionRejected("ComfyUI supports only retained builtin image recipes")
        try:
            if self.workflows is not None:
                prompt = self.workflows.prompt(request.payload, job_id)
            else:
                prompt = build_prompt(request.payload, self.catalog)
                prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        except ValueError as exc:
            raise SubmissionRejected(str(exc)[:300]) from exc
        # No custom graph/managed-reference injection and no retry after an
        # uncertain POST. ComfyUIClient preserves the accepted/unknown distinction.
        execution_id = await self.client.submit(prompt, job_id)
        self._output_nodes[execution_id] = "7"
        return ProviderJob(execution_id=execution_id)

    def _normalize(self, execution_id: str, raw: dict) -> JobSnapshot:
        status = raw.get("status")
        if status not in STATUSES:
            raise ProviderError("ComfyUI returned an invalid normalized execution state")

        outputs = []
        declared_node = self._output_nodes.get(execution_id)
        if declared_node is None:
            raise ProviderError("Unknown ComfyUI execution; inspect only submitted jobs")
        selected = [item for item in raw.get("outputs", []) if item.get("node_id") == declared_node]
        if status == "completed" and not selected:
            raise ProviderError("ComfyUI returned no declared recipe output")
        for index, item in enumerate(selected):
            try:
                filename = item["filename"]
                suffix = Path(filename).suffix.lower()
                media_kind, mime_type = MEDIA_TYPES[suffix]
            except (KeyError, TypeError, ValueError):
                raise ProviderError("ComfyUI returned an unsupported output") from None
            output_id = f"{index:03d}"
            outputs.append(
                ProviderOutput(
                    output_id=output_id,
                    filename=filename,
                    media_kind=media_kind,
                    mime_type=mime_type,
                )
            )
            self._outputs[(execution_id, output_id)] = dict(item)

        error = raw.get("error")
        if error is not None and not isinstance(error, dict):
            raise ProviderError("ComfyUI returned an invalid normalized error")
        metadata = {key: raw[key] for key in ("cancel_supported", "reason") if key in raw}
        return JobSnapshot(
            status=status,
            error=error,
            outputs=tuple(outputs),
            metadata=metadata,
        )

    async def inspect(self, execution_id: str) -> JobSnapshot:
        raw = await self.client.inspect(execution_id)
        self._release_inputs(execution_id, raw)
        return self._normalize(execution_id, raw)

    async def cancel(self, execution_id: str) -> JobSnapshot:
        raw = await self.client.cancel(execution_id)
        self._release_inputs(execution_id, raw)
        return self._normalize(execution_id, raw)

    def _release_inputs(self, execution_id, raw):
        if self.input_copies is not None:
            try:
                self.input_copies.observe(execution_id, raw.get("status"))
            except (OSError, ValueError):
                # A storage fault must not erase a known provider outcome. The durable
                # charge remains until bounded cleanup succeeds on a later observation.
                logger.warning("ComfyUI input cleanup deferred; reconcile storage")

    @staticmethod
    def _owned_observation(raw):
        """Status only; old identities never restore output publication authority."""
        status = raw.get("status")
        supported = raw.get("cancel_supported")
        if status not in STATUSES or (supported is not None and type(supported) is not bool):
            raise ProviderError("ComfyUI returned an invalid owned execution observation")
        return JobSnapshot(
            status=status,
            metadata={"cancel_supported": supported} if supported is not None else {},
        )

    async def inspect_owned(self, execution_id: str) -> JobSnapshot:
        raw = await self.client.inspect(execution_id)
        self._release_inputs(execution_id, raw)
        return self._owned_observation(raw)

    async def cancel_owned(self, execution_id: str) -> JobSnapshot:
        raw = await self.client.cancel(execution_id)
        self._release_inputs(execution_id, raw)
        return self._owned_observation(raw)

    async def materialize(self, execution_id: str, output_id: str) -> bytes:
        try:
            output = self._outputs[(execution_id, output_id)]
        except KeyError:
            raise ProviderError(
                "Unknown ComfyUI output; inspect the job before retrieval"
            ) from None
        return await self.client.download(output)

    async def close(self) -> None:
        await self.client.close()

    async def stream_output(self, execution_id: str, output_id: str):
        from contextlib import aclosing

        output = self._outputs.get((execution_id, output_id))
        if output is None:
            raise ProviderError("Unknown ComfyUI output; inspect before retrieval")
        async with aclosing(self.client.stream_output(output)) as stream:
            async for chunk in stream:
                yield chunk
