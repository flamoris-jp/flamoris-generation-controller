import asyncio
import json

import httpx
import pytest

from flamoris_generation_controller.comfyui import ComfyUIClient, ProviderError
from flamoris_generation_controller.jobs import GenerationBusyError
from flamoris_generation_controller.providers.base import SubmissionRejected
from flamoris_generation_controller.workflows import Parameters


async def submit(stores):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image",
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers", seed=42),
    )
    job = await jobs.submit(workflow["workflow_id"])
    return job["job_id"]


async def test_complete_lifecycle_and_repeated_result(stores, fake, settings):
    _, jobs, client = stores
    key = await submit(stores)
    assert (await jobs.status(key))["status"] == "queued"
    assert (await jobs.result(key))["files"] == []
    assert (await client.health())["queued"] == 1
    fake.running, fake.pending = fake.pending, []
    assert (await jobs.status(key))["status"] == "running"
    fake.finish()
    result = await jobs.result(key)
    assert result["status"] == "completed"
    assert result["operation"] == "image.generate"
    assert result["provider_id"] == "comfyui"
    assert result["provider_execution_id"] == "prompt-1"
    assert result["outputs"] == [
        {
            "output_id": "000",
            "filename": "result.png",
            "media_kind": "image",
            "mime_type": "image/png",
        }
    ]
    assert result["parameters"]["seed"] == 42
    assert result["template"] == "text-to-image"
    assert len(result["files"]) == 1
    assert json.loads((settings.output_dir / key / "metadata.json").read_text()) == result
    assert await jobs.result(key) == result
    assert fake.download_count == 1
    assert (await jobs.cancel(key))["status"] == "completed"
    assert not any(call[1] == "/interrupt" for call in fake.calls)


async def test_rejects_queued_and_running_then_allows_after_completion(stores, fake):
    _, jobs, _ = stores
    first = await submit(stores)

    with pytest.raises(GenerationBusyError, match=first):
        await submit(stores)

    fake.running, fake.pending = fake.pending, []
    assert (await jobs.status(first))["status"] == "running"
    with pytest.raises(GenerationBusyError, match="is running"):
        await submit(stores)

    fake.finish()
    assert (await jobs.status(first))["status"] == "completed"
    second = await submit(stores)
    assert second != first


async def test_simultaneous_submits_cannot_both_reach_provider(stores, fake, monkeypatch):
    _, jobs, _ = stores
    entered = asyncio.Event()
    release = asyncio.Event()
    provider = jobs.providers.get("comfyui")
    original_submit = provider.submit

    async def blocked_submit(request, job_id):
        entered.set()
        await release.wait()
        return await original_submit(request, job_id)

    monkeypatch.setattr(provider, "submit", blocked_submit)
    first_task = asyncio.create_task(submit(stores))
    await entered.wait()

    with pytest.raises(GenerationBusyError, match="is submitting"):
        await submit(stores)

    release.set()
    await first_task
    assert len(fake.prompts) == 1


async def test_provider_submit_failure_releases_exclusivity(stores, fake, monkeypatch):
    _, jobs, _ = stores
    provider = jobs.providers.get("comfyui")
    original_submit = provider.submit

    async def failed_submit(request, job_id):
        raise SubmissionRejected("provider rejected test submission")

    monkeypatch.setattr(provider, "submit", failed_submit)
    with pytest.raises(ProviderError, match="provider rejected"):
        await submit(stores)

    monkeypatch.setattr(provider, "submit", original_submit)
    await submit(stores)
    assert len(fake.prompts) == 1


async def test_unknown_failure_and_interrupted(stores, fake):
    _, jobs, _ = stores
    key = await submit(stores)
    fake.pending = []
    assert (await jobs.status(key))["status"] == "unknown"
    fake.finish(
        state="error",
        messages=[
            [
                "execution_error",
                {
                    "node_id": "5",
                    "exception_type": "OutOfMemoryError",
                    "traceback": ["private detail"],
                },
            ]
        ],
    )
    failed = await jobs.result(key)
    assert failed["status"] == "failed"
    assert failed["error"] == {
        "code": "execution_error",
        "node_id": "5",
        "exception_type": "OutOfMemoryError",
    }
    assert failed["files"] == []
    second = await submit(stores)
    fake.finish("prompt-2", state="error", messages=[["execution_interrupted", {}]])
    assert (await jobs.status(second))["status"] == "cancelled"


async def test_queued_cancel_is_scoped_and_absence_retains_exclusivity(stores, fake):
    _, jobs, _ = stores
    key = await submit(stores)
    with pytest.raises(GenerationBusyError, match="Generation is busy"):
        await submit(stores)

    assert (await jobs.cancel(key))["status"] == "unknown"
    assert (await jobs.status(key))["status"] == "unknown"
    assert ("POST", "/queue", {"delete": ["prompt-1"]}) in fake.calls
    assert not any(call[1] == "/interrupt" for call in fake.calls)

    with pytest.raises(GenerationBusyError, match="is unknown"):
        await submit(stores)
    fake.finish(state="error", messages=[["execution_interrupted", {}]])
    assert (await jobs.status(key))["status"] == "cancelled"
    second = await submit(stores)
    assert second != key
    assert [entry[1] for entry in fake.pending] == ["prompt-2"]


async def test_queued_delete_racing_with_lost_history_is_not_settlement(stores, fake, monkeypatch):
    _, jobs, _ = stores
    key = await submit(stores)
    handle = fake.handle

    def lost(request):
        response = handle(request)
        if request.method == "POST" and request.url.path == "/queue":
            fake.running = []
            fake.pending = []
            fake.history = {}
        return response

    # The next queue/history snapshot cannot distinguish removed from running/lost.
    fake.race_to_running = True
    monkeypatch.setattr(
        jobs.providers.get("comfyui").client.http, "_transport", httpx.MockTransport(lost)
    )
    assert (await jobs.cancel(key))["status"] == "unknown"
    assert jobs.activity()["busy"]
    assert not any(call[1] == "/interrupt" for call in fake.calls)


async def test_running_cancel_requires_explicit_capability(stores, fake, settings):
    _, jobs, _ = stores
    key = await submit(stores)
    fake.race_to_running = True
    result = await jobs.cancel(key)
    assert result["status"] == "running" and result["cancel_supported"] is False
    assert not any(call[1] == "/interrupt" for call in fake.calls)
    settings.targeted_interrupt = True
    assert (await jobs.cancel(key))["status"] == "cancel_requested"
    assert ("POST", "/interrupt", {"prompt_id": "prompt-1"}) in fake.calls
    with pytest.raises(GenerationBusyError, match="is cancel_requested"):
        await submit(stores)
    fake.finish()  # Completion wins the race; do not incorrectly label cancelled.
    assert (await jobs.status(key))["status"] == "completed"


async def test_failed_download_can_retry_without_partial_file(stores, fake, settings):
    _, jobs, _ = stores
    key = await submit(stores)
    fake.finish()
    fake.output_error = True
    with pytest.raises(ProviderError):
        await jobs.result(key)
    assert not (settings.output_dir / key / "000.png").exists()
    fake.output_error = False
    assert len((await jobs.result(key))["files"]) == 1


async def test_no_foreign_job_and_models_revalidated_at_submit(stores, fake, settings):
    workflows, jobs, _ = stores
    with pytest.raises(ValueError, match="Unknown job"):
        await jobs.cancel("0" * 32)
    assert not fake.calls
    workflow = workflows.build(
        "text-to-image", Parameters(checkpoint="base.safetensors", positive_prompt="x")
    )
    (settings.model_root / "checkpoints/base.safetensors").unlink()
    with pytest.raises(ValueError, match="Model not found"):
        await jobs.submit(workflow["workflow_id"])
    assert not fake.calls


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "bad prompt"}),
        httpx.Response(200, text="invalid"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"prompt_id": "x", "node_errors": {"5": {}}}),
    ],
)
async def test_submit_error_responses(settings, response):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    client = ComfyUIClient(settings, httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError):
            await client.submit({}, "client")
        assert len(calls) == 1
    finally:
        await client.close()


async def test_timeout_unavailable_health_and_no_submit_retry(settings):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    client = ComfyUIClient(settings, httpx.MockTransport(handler))
    try:
        assert (await client.health())["available"] is False
        with pytest.raises(ProviderError, match="submission_unknown"):
            await client.submit({}, "client")
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.parametrize(
    "filename,subfolder",
    [("../bad.png", ""), ("x.png", "../outside"), ("/bad.png", ""), ("bad\\x.png", "")],
)
async def test_provider_output_traversal_rejected(stores, fake, filename, subfolder):
    _, jobs, _ = stores
    key = await submit(stores)
    fake.finish()
    fake.history["prompt-1"]["outputs"]["7"]["images"][0].update(
        filename=filename, subfolder=subfolder
    )
    with pytest.raises(ProviderError, match="invalid execution history"):
        await jobs.result(key)
    assert fake.download_count == 0


async def test_output_size_bound_and_prefix_url(settings, monkeypatch):
    monkeypatch.setattr("flamoris_generation_controller.comfyui.MAX_OUTPUT_BYTES", 4)
    settings.comfyui_url = "http://localhost:8188/api/"
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b"12345")

    client = ComfyUIClient(settings, httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError, match="download limit"):
            await client.download({"filename": "x.png", "subfolder": "", "type": "output"})
        assert seen == ["/api/view"]
    finally:
        await client.close()


async def test_rejection_contains_actionable_node_error(settings):
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "prompt_outputs_failed_validation",
                    "message": "Prompt outputs failed validation",
                },
                "node_errors": {
                    "5": {"errors": [{"message": "Value not in list", "details": "sampler_name"}]}
                },
            },
        )

    client = ComfyUIClient(settings, httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError, match="node 5: Value not in list sampler_name"):
            await client.submit({}, "test")
    finally:
        await client.close()


@pytest.mark.parametrize(
    "history",
    [
        [],
        {},
        {"status": None},
        {"status": {"status_str": "success", "completed": True}, "outputs": {}},
    ],
)
async def test_malformed_history_does_not_report_success(stores, fake, history):
    _, jobs, _ = stores
    key = await submit(stores)
    fake.history["prompt-1"] = history
    with pytest.raises(ProviderError, match="invalid execution history"):
        await jobs.status(key)
