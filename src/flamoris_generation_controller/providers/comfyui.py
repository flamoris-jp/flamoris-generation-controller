"""ComfyUI implementation of the provider-neutral generation contract."""

import asyncio
import logging
from pathlib import Path

from ..comfyui import ComfyUIClient
from ..image_decode import decode_image
from ..models import ModelCatalog
from ..reference_comfy import IMAGE_NODES, ReferenceRecipe
from ..video import VIDEO_BYTES, owned_video_output, verify_mp4, video_parameters
from ..workflows import (
    DeclaredRecipe,
    Recipe,
    RegisteredRecipe,
    VideoRecipe,
    WorkflowStore,
    build_prompt,
)
from .base import (
    GenerationRequest,
    JobSnapshot,
    OutputRole,
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
    ".mp4": ("video", "video/mp4"),
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
        self._videos: dict[str, tuple[str, dict]] = {}

    def restore_video(self, recipe, job_id, execution_id):
        """Restore output expectations for acknowledged work, without posting again."""
        if isinstance(recipe, VideoRecipe):
            from ..declared_comfy import render, validate

            _name, graph, decl, _reference = self.workflows.definitions.get(recipe.definition_id)
            prompt = render(graph, decl, {**recipe.parameters, **recipe.images})
            declaration = decl.model_dump()
            for spec in declaration["parameters"].values():
                if spec["role"] != "managed_input":
                    spec.update(default=None, required=True)
            validate(
                prompt,
                declaration,
                self.workflows.definitions.declared_specs(recipe.definition_id),
                {node["class_type"] for node in graph.values()},
            )
            self._videos[execution_id] = (job_id, video_parameters(prompt, decl))

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
        operation = (
            "video.generate" if isinstance(request.payload, VideoRecipe) else "image.generate"
        )
        if request.operation != operation or not isinstance(
            request.payload, (Recipe, RegisteredRecipe, ReferenceRecipe, DeclaredRecipe)
        ):
            raise SubmissionRejected("ComfyUI recipe/capability mismatch")
        try:
            if self.workflows is not None:
                prompt = self.workflows.prompt(request.payload, job_id)
            else:
                prompt = build_prompt(request.payload, self.catalog)
                prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        except ValueError as exc:
            raise SubmissionRejected(str(exc)[:300]) from exc
        output_node = (
            self.workflows.definitions.output_node(request.payload) if self.workflows else "7"
        )
        if isinstance(request.payload, DeclaredRecipe):
            try:
                specs = await self.client.object_info(prompt)
                # Validate before staging/POST; output prefixes are assigned afterwards.
                checked = self.workflows.definitions.prompt(request.payload)
                self.workflows.definitions.validate_declared_prompt(
                    request.payload.definition_id, checked, specs
                )
            except (ValueError, TimeoutError) as exc:
                raise SubmissionRejected(
                    str(exc)[:300] or "Node specification check timed out"
                ) from exc
        if (
            isinstance(request.payload, (ReferenceRecipe, DeclaredRecipe))
            and request.payload.images
            or (isinstance(request.payload, RegisteredRecipe) and request.payload.reference_image)
        ):
            return await self._submit_reference(request.payload, prompt, job_id)
        execution_id = await self.client.submit(prompt, job_id)
        self._output_nodes[execution_id] = output_node
        return ProviderJob(execution_id=execution_id)

    async def _submit_reference(self, recipe, prompt, job_id):
        if (
            self.managed_inputs is None
            or self.input_copies is None
            or not self.input_copies.available()
        ):
            raise SubmissionRejected("Managed reference image storage is not available")
        output_node = self.workflows.definitions.output_node(recipe) if self.workflows else "7"
        posted = False
        try:
            images = (
                recipe.images
                if isinstance(recipe, (ReferenceRecipe, DeclaredRecipe))
                else {"reference_image": recipe.reference_image}
            )
            async with self.managed_inputs.stage(
                job_id,
                images,
                {key: {"image/png", "image/jpeg", "image/webp"} for key in images},
            ) as readers:
                metadata = {}
                for role, reader in readers.items():
                    data = bytearray()
                    async for part in reader.chunks():
                        data.extend(part)
                        if len(data) > 8 * 1024**2:
                            raise ValueError("Reference image exceeds byte limit")
                    payload = bytes(data)
                    decode_image(payload, reader.metadata["mime_type"])
                    name = self.input_copies.stage(job_id, payload, reader.metadata["mime_type"])
                    if isinstance(recipe, DeclaredRecipe):
                        declaration = self.workflows.definitions.get(recipe.definition_id)[2]
                        for target in declaration.parameters[role].targets:
                            prompt[target.node]["inputs"][target.input] = name
                    else:
                        node = IMAGE_NODES[role] if isinstance(recipe, ReferenceRecipe) else "8"
                        prompt[node]["inputs"]["image"] = name
                    metadata[role] = dict(reader.metadata)
                self.input_copies.before_post(job_id)
                posted = True
                execution_id = await self.client.submit(prompt, job_id)
                self._output_nodes[execution_id] = output_node
                if isinstance(recipe, VideoRecipe):
                    declaration = self.workflows.definitions.get(recipe.definition_id)[2]
                    self._videos[execution_id] = (job_id, video_parameters(prompt, declaration))
                self.input_copies.bind(job_id, execution_id)
                return ProviderJob(execution_id, metadata)

        except BaseException as exc:
            if not posted or isinstance(exc, SubmissionRejected):
                try:
                    self.input_copies.rejected(job_id)
                except (OSError, ValueError):
                    logger.warning("Rejected reference copy requires reconciliation")
                if isinstance(exc, Exception):
                    raise SubmissionRejected(
                        "Reference image staging or validation rejected"
                    ) from None
            raise

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
        video = self._videos.get(execution_id)
        if video and status == "completed" and len(selected) != 1:
            raise ProviderError("Video returned an unexpected output count")
        for index, item in enumerate(selected):
            try:
                filename = item["filename"]
                suffix = Path(filename).suffix.lower()
                media_kind, mime_type = MEDIA_TYPES[suffix]
                if video:
                    owned_video_output(item, video[0])
                    if suffix != ".mp4":
                        raise ValueError("Expected MP4")
                elif media_kind != "image":
                    raise ValueError("Expected an image output")
            except (KeyError, TypeError, ValueError):
                raise ProviderError("ComfyUI returned an unsupported output") from None
            output_id = f"{index:03d}"
            outputs.append(
                ProviderOutput(
                    output_id=output_id,
                    filename=filename,
                    media_kind=media_kind,
                    mime_type=mime_type,
                    role=OutputRole("video", "video_clip") if video else None,
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
        data = await self.client.download(output)
        if execution_id in self._videos:
            await self._verify_video(execution_id, data)
        return data

    async def close(self) -> None:
        await self.client.close()

    async def stream_output(self, execution_id: str, output_id: str):
        from contextlib import aclosing

        output = self._outputs.get((execution_id, output_id))
        if output is None:
            raise ProviderError("Unknown ComfyUI output; inspect before retrieval")
        async with aclosing(self.client.stream_output(output)) as stream:
            if execution_id not in self._videos:
                async for chunk in stream:
                    yield chunk
                return
            data = bytearray()
            async for chunk in stream:
                if len(data) + len(chunk) > VIDEO_BYTES:
                    raise ProviderError("Video exceeds the 64 MiB clip limit")
                data.extend(chunk)
        await self._verify_video(execution_id, data)
        from ..transfers import CHUNK_BYTES

        for offset in range(0, len(data), CHUNK_BYTES):
            yield bytes(data[offset : offset + CHUNK_BYTES])

    async def _verify_video(self, execution_id, data):
        try:
            async with asyncio.timeout(30):
                await asyncio.to_thread(verify_mp4, data, self._videos[execution_id][1])
        except (ValueError, TimeoutError):
            raise ProviderError("MP4 output failed bounded decode/recipe verification") from None
