"""One generation runtime and lifecycle shared by every trusted adapter."""

import inspect

import httpx

from . import __version__
from .authority import Authority
from .capabilities import Capability, CapabilityRegistry
from .comfyui import ComfyUIClient
from .config import Settings
from .contracts import OPERATIONS, AssetContent, CallerContext, ControllerError
from .input_uploads import InputUploads
from .inputs import ManagedInputs
from .jobs import JobStore
from .models import ModelCatalog
from .music import MUSIC_CAPABILITY, MUSIC_PROVIDER, MUSIC_TEMPLATE
from .providers import ProviderRegistry
from .providers.comfyui import ComfyUIProvider
from .providers.irodori import IrodoriConfig, IrodoriProvider
from .providers.sheetsage2 import SheetSage2Config, SheetSage2Provider
from .providers.yue2 import Yue2Config, Yue2Provider
from .retention import RetentionStore
from .speech import SPEECH_CAPABILITY, SPEECH_PROVIDER, SPEECH_TEMPLATE
from .transcription import (
    TRANSCRIPTION_CAPABILITY,
    TRANSCRIPTION_PROVIDER,
    TRANSCRIPTION_TEMPLATE,
)
from .transfers import AssetTransfers
from .workflows import WorkflowStore


class GenerationController:
    def __init__(
        self, settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        settings = settings or Settings.from_env()
        self.owner = Authority(settings.output_dir)
        self._closed = False
        try:
            catalog = ModelCatalog(settings)
            workflows = WorkflowStore(
                catalog,
                settings.workflow_dir,
                speech_enabled=settings.irodori_config is not None,
                music_enabled=settings.yue2_config is not None,
                transcription_enabled=settings.sheetsage2_config is not None,
            )
            client = ComfyUIClient(settings, transport)
            comfyui = ComfyUIProvider(client, catalog, workflows)
            retained_copy_store_available = (
                comfyui.input_copies is not None and comfyui.input_copies.available()
            )
            providers = ProviderRegistry((comfyui,))
            capabilities = CapabilityRegistry(
                (
                    Capability(
                        capability_id="image.generate",
                        provider_id="comfyui",
                        runtime_id="janku",
                        workflow_templates=(
                            "text-to-image",
                            "text-to-image-lora",
                        ),
                    ),
                )
            )
            if settings.irodori_config is not None:
                providers.register(
                    IrodoriProvider(
                        IrodoriConfig.read(settings.irodori_config),
                        settings.output_dir / "irodori-staging",
                    )
                )
                capabilities.register(
                    Capability(
                        capability_id=SPEECH_CAPABILITY,
                        provider_id=SPEECH_PROVIDER,
                        runtime_id="irodori-no-reference-v1",
                        workflow_templates=(SPEECH_TEMPLATE,),
                    )
                )
            if settings.yue2_config is not None:
                providers.register(
                    Yue2Provider(
                        Yue2Config.read(settings.yue2_config), settings.output_dir / "yue2-staging"
                    )
                )
                capabilities.register(
                    Capability(
                        capability_id=MUSIC_CAPABILITY,
                        provider_id=MUSIC_PROVIDER,
                        runtime_id="yue2-synth-v1",
                        workflow_templates=(MUSIC_TEMPLATE,),
                    )
                )
            sheetsage2 = None
            if settings.sheetsage2_config is not None:
                sheetsage2 = SheetSage2Provider(
                    SheetSage2Config.read(settings.sheetsage2_config),
                    settings.output_dir / "sheetsage2-staging",
                )
                providers.register(sheetsage2)
                capabilities.register(
                    Capability(
                        capability_id=TRANSCRIPTION_CAPABILITY,
                        provider_id=TRANSCRIPTION_PROVIDER,
                        runtime_id="sheetsage2-cpu-v1",
                        workflow_templates=(TRANSCRIPTION_TEMPLATE,),
                    )
                )
            retention = None
            maintenance = comfyui.retention()
            if maintenance is not None:
                retention = RetentionStore(
                    settings.output_dir,
                    {
                        "comfyui": maintenance,
                    },
                )
            jobs = JobStore(workflows, providers, capabilities, settings.output_dir, retention)

            transfers = AssetTransfers(
                jobs, max_bytes=settings.transfer_max_bytes, disk_bytes=settings.transfer_disk_bytes
            )

            inputs = ManagedInputs(transfers, settings.output_dir / "managed-inputs")
            uploads = InputUploads(inputs)
            # Provider construction precedes JobStore/ManagedInputs because the input lease
            # validates the shared Hub reservation. Wire the adapter only after both exist.
            comfyui.managed_inputs = inputs
            if sheetsage2 is not None:
                sheetsage2.managed_inputs = inputs

            self.settings = settings
            self.catalog = catalog
            self.workflows = workflows
            self.client = client
            self.comfyui = comfyui
            self.retained_copy_store_available = retained_copy_store_available
            self.providers = providers
            self.capabilities = capabilities
            self.retention = retention
            self.jobs = jobs
            self.transfers = transfers
            self.inputs = inputs
            self.uploads = uploads
        except BaseException:
            self.owner.close()
            raise

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            await self.providers.close()
        finally:
            self.owner.close()

    async def _provider_availability(self):
        health = await self.providers.health()
        return health, {item["id"]: item.get("available") is True for item in health}

    async def health(self):
        provider_health, _ = await self._provider_availability()
        return {
            "healthy": True,
            "version": __version__,
            "deployment": {"reservation_scope": "process", "single_instance_required": True},
            "controller": {"api_version": 1, "authority_lock": "output-root"},
            **self.jobs.activity(),
            "providers": provider_health,
            "managed_input_support": {
                "ready": False,
                "reference_execution": "retired",
                "retained_copy_store_available": self.retained_copy_store_available
                and self.comfyui.input_copies.available(),
            },
            "provider": "comfyui",
            "provider_health": {
                key: value for key, value in provider_health[0].items() if key != "id"
            },
        }

    async def invoke(self, operation: str, arguments: dict, *, context: CallerContext):
        if not isinstance(context, CallerContext) or operation not in context.permissions:
            raise ControllerError("forbidden")
        if self._closed:
            raise ControllerError("authority_unavailable")
        self.owner.check()
        request_type = OPERATIONS.get(operation)
        if request_type is None:
            raise ControllerError("unknown_operation")
        args = request_type.model_validate(arguments).model_dump()
        if operation == "system.health":
            result = await self.health()
        elif operation in {"capabilities.list", "capabilities.get"}:
            _, availability = await self._provider_availability()
            result = (
                {"capabilities": self.capabilities.list(availability)}
                if operation == "capabilities.list"
                else self.capabilities.get(args["capability_id"], availability)
            )
        elif operation == "models.list":
            result = {"models": self.catalog.list(args["kind"])}
        elif operation == "jobs.submit":
            result = await self.jobs.submit(args["workflow_id"], provenance=context.provenance)
        elif operation == "assets.get":
            asset, data, media_format = await self.jobs.get_asset(args["asset_id"])
            result = AssetContent(data, asset["mime_type"], media_format)
        else:
            handlers = {
                "models.get": self.catalog.get,
                "workflows.list": self.workflows.list,
                "workflows.build": self.workflows.build,
                "workflows.save": self.workflows.save,
                "jobs.status": self.jobs.status,
                "jobs.result": self.jobs.result,
                "jobs.cancel": self.jobs.cancel,
                "assets.list": self.jobs.list_assets,
                "assets.delete": self.jobs.delete_asset,
                "assets.prepare": self.transfers.prepare,
                "assets.read": self.transfers.read,
                "inputs.create": self.inputs.create,
                "inputs.get": self.inputs.get,
                "inputs.delete": self.inputs.delete,
                "inputs.upload.begin": self.uploads.begin,
                "inputs.upload.write": self.uploads.write,
                "inputs.upload.finish": self.uploads.finish,
            }
            result = handlers[operation](**args)
            if inspect.isawaitable(result):
                result = await result
        self.owner.check()
        return result
