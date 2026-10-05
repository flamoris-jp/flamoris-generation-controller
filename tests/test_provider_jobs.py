import asyncio

import pytest

from flamoris_generation_controller.capabilities import Capability, CapabilityRegistry
from flamoris_generation_controller.jobs import GenerationBusyError, JobStore
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.music import MUSIC_TEMPLATE
from flamoris_generation_controller.providers import (
    GenerationRequest,
    JobSnapshot,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    ProviderRegistry,
)
from flamoris_generation_controller.providers.base import SubmissionRejected
from flamoris_generation_controller.workflows import Parameters, WorkflowStore


class FakeProvider:
    def __init__(self, provider_id="fake"):
        self.provider_id = provider_id
        self.requests: list[tuple[GenerationRequest, str]] = []
        self.snapshots: dict[str, JobSnapshot] = {}
        self.submit_error: ProviderError | None = None
        self.submit_entered: asyncio.Event | None = None
        self.submit_release: asyncio.Event | None = None
        self.cancel_snapshot: JobSnapshot | None = None
        self.closed = False

    async def health(self):
        return ProviderHealth(available=True, details={"queue_depth": 0})

    async def submit(self, request, job_id):
        if self.submit_entered is not None:
            self.submit_entered.set()
        if self.submit_release is not None:
            await self.submit_release.wait()
        if self.submit_error is not None:
            raise self.submit_error
        self.requests.append((request, job_id))
        execution_id = f"execution-{len(self.requests)}"
        self.snapshots[execution_id] = JobSnapshot(status="queued")
        return ProviderJob(execution_id=execution_id)

    async def inspect(self, execution_id):
        return self.snapshots[execution_id]

    async def cancel(self, execution_id):
        if self.cancel_snapshot is not None:
            self.snapshots[execution_id] = self.cancel_snapshot
        return self.snapshots[execution_id]

    async def materialize(self, execution_id, output_id):
        assert execution_id in self.snapshots
        assert output_id == "image-0"
        return b"provider-neutral image"

    async def close(self):
        self.closed = True


def make_store(settings):
    workflows = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    provider = FakeProvider("comfyui")
    providers = ProviderRegistry((provider,))
    capabilities = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="fake-image",
                workflow_templates=("text-to-image", "text-to-image-lora"),
            ),
        )
    )
    jobs = JobStore(workflows, providers, capabilities, settings.output_dir)
    workflow = workflows.build(
        "text-to-image",
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers"),
    )
    return workflows, provider, providers, jobs, workflow["workflow_id"]


async def test_job_store_tracks_provider_neutral_identity_and_outputs(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)

    submitted = await jobs.submit(workflow_id)

    assert submitted["operation"] == "image.generate"
    assert submitted["provider"] == submitted["provider_id"] == "comfyui"
    assert submitted["provider_execution_id"] == "execution-1"
    request, hub_job_id = provider.requests[0]
    assert hub_job_id == submitted["job_id"]
    assert request.operation == "image.generate"
    assert request.workflow_id == workflow_id

    provider.snapshots["execution-1"] = JobSnapshot(
        status="completed",
        metadata={"job_id": "provider-override", "provider_id": "provider-override"},
        outputs=(
            ProviderOutput(
                output_id="image-0",
                filename="provider-name.png",
                media_kind="image",
                mime_type="image/png",
            ),
        ),
    )
    result = await jobs.result(submitted["job_id"])

    assert result["job_id"] == submitted["job_id"]
    assert result["provider_id"] == "comfyui"
    assert result["outputs"] == [
        {
            "output_id": "image-0",
            "filename": "provider-name.png",
            "media_kind": "image",
            "mime_type": "image/png",
        }
    ]
    assert result["files"][0]["size_bytes"] == len(b"provider-neutral image")


async def test_provider_submit_failure_releases_hub_reservation(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    provider.submit_error = SubmissionRejected("test provider rejected request")

    with pytest.raises(ProviderError, match="rejected"):
        await jobs.submit(workflow_id)

    provider.submit_error = None
    assert (await jobs.submit(workflow_id))["status"] == "queued"


async def test_provider_neutral_submit_reservation_is_race_safe(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    provider.submit_entered = asyncio.Event()
    provider.submit_release = asyncio.Event()

    first = asyncio.create_task(jobs.submit(workflow_id))
    await provider.submit_entered.wait()

    with pytest.raises(GenerationBusyError, match="is submitting"):
        await jobs.submit(workflow_id)

    provider.submit_release.set()
    assert (await first)["provider_execution_id"] == "execution-1"
    assert len(provider.requests) == 1


async def test_capability_routes_two_operations_through_one_job_authority(settings):
    workflows = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, music_enabled=True)
    image_provider = FakeProvider("comfyui")
    music_provider = FakeProvider("yue2")
    providers = ProviderRegistry((image_provider, music_provider))
    capabilities = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="image-runtime",
                workflow_templates=("text-to-image",),
            ),
            Capability(
                capability_id="music.generate",
                provider_id="yue2",
                runtime_id="music-runtime",
                workflow_templates=(MUSIC_TEMPLATE,),
            ),
        )
    )
    jobs = JobStore(workflows, providers, capabilities, settings.output_dir)
    image_workflow = workflows.build(
        "text-to-image",
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers"),
    )["workflow_id"]
    music_workflow = workflows.build(
        MUSIC_TEMPLATE,
        {"style": "melody"},
    )["workflow_id"]

    image_job = await jobs.submit(image_workflow)
    with pytest.raises(GenerationBusyError, match="Generation is busy"):
        await jobs.submit(music_workflow)

    image_provider.snapshots["execution-1"] = JobSnapshot(status="completed")
    assert (await jobs.status(image_job["job_id"]))["status"] == "completed"
    music_job = await jobs.submit(music_workflow)

    assert image_job["operation"] == "image.generate"
    assert image_job["provider_id"] == "comfyui"
    assert image_provider.requests[0][0].operation == "image.generate"
    assert music_job["operation"] == "music.generate"
    assert music_job["provider_id"] == "yue2"
    assert music_provider.requests[0][0].operation == "music.generate"


def test_provider_metadata_cannot_override_normalized_job_fields():
    snapshot = JobSnapshot(
        status="running",
        error=None,
        outputs=(),
        metadata={
            "status": "failed",
            "error": {"code": "provider_override"},
            "outputs": [{"provider": "override"}],
            "provider_note": "retained",
        },
    )

    serialized = snapshot.as_dict()

    assert serialized["status"] == "running"
    assert serialized["error"] is None
    assert serialized["outputs"] == []
    assert serialized["provider_note"] == "retained"


async def test_cancel_requested_keeps_capacity_until_terminal_confirmation(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    first = await jobs.submit(workflow_id)
    provider.cancel_snapshot = JobSnapshot(
        status="cancel_requested",
        metadata={"cancel_supported": True},
    )

    cancelled = await jobs.cancel(first["job_id"])
    assert cancelled["status"] == "cancel_requested"
    with pytest.raises(GenerationBusyError, match="is cancel_requested"):
        await jobs.submit(workflow_id)

    provider.snapshots["execution-1"] = JobSnapshot(status="cancelled")
    assert (await jobs.status(first["job_id"]))["status"] == "cancelled"
    assert (await jobs.submit(workflow_id))["provider_execution_id"] == "execution-2"


async def test_registry_health_and_lifecycle_are_process_owned():
    provider = FakeProvider()
    registry = ProviderRegistry((provider,))

    assert await registry.health() == [{"id": "fake", "available": True, "queue_depth": 0}]
    await registry.close()
    assert provider.closed is True
