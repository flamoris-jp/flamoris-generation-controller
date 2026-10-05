import json

import httpx
import pytest
from conftest import validation_rejection
from test_provider_jobs import make_store

from flamoris_generation_controller.jobs import GenerationBusyError, JobStore
from flamoris_generation_controller.providers.base import (
    ProviderJob,
    SubmissionRejected,
    SubmissionUnknown,
)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, text="gateway rejected request"),
        httpx.Response(400, json={"error": "gateway error"}),
        httpx.Response(
            400, json={"error": {"type": "gateway_error", "message": "bad"}, "node_errors": {}}
        ),
        httpx.Response(
            400,
            json={
                "error": {"type": "prompt_no_outputs", "message": "bad"},
                "node_errors": {},
                "prompt_id": "accepted",
            },
        ),
        httpx.Response(
            400,
            json={
                "error": {"type": "prompt_no_outputs", "message": "bad"},
                "node_errors": {},
                "prompt_id": None,
            },
        ),
        httpx.Response(200, json={"error": {"message": "bad"}, "node_errors": {}}),
        httpx.Response(200, json={"prompt_id": " "}),
        httpx.Response(200, json={"prompt_id": "x\n"}),
        httpx.Response(200, json={"prompt_id": "x" * 513}),
    ],
)
async def test_unproven_rejection_retains_slot_across_restart(stores, response):
    workflows, jobs, client = stores
    calls = []

    def handle(request):
        calls.append(request)
        return response

    client.http._transport = httpx.MockTransport(handle)
    key = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )["workflow_id"]
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(key)
    job_id = jobs.activity()["active_job_id"]
    assert (await jobs.status(job_id))["status"] == "unknown"
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    assert restarted.activity() == jobs.activity()
    assert (await restarted.cancel(job_id))["status"] == "unknown"
    with pytest.raises(GenerationBusyError):
        await restarted.submit(key)
    assert len(calls) == 1


async def test_structured_validation_rejection_releases_slot(stores, fake):
    workflows, jobs, client = stores
    response = validation_rejection()
    client.http._transport = httpx.MockTransport(lambda request: response)
    key = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )["workflow_id"]
    with pytest.raises(SubmissionRejected):
        await jobs.submit(key)
    assert not jobs.activity()["busy"]
    client.http._transport = httpx.MockTransport(fake.handle)
    assert (await jobs.submit(key))["status"] == "queued"


async def test_lost_accepted_response_never_replays_even_after_provider_finishes(stores, fake):
    workflows, jobs, client = stores

    def accepted_then_lost(request):
        fake.handle(request)
        raise httpx.ReadTimeout("acknowledgement lost", request=request)

    client.http._transport = httpx.MockTransport(accepted_then_lost)
    key = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )["workflow_id"]
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(key)
    assert len(fake.prompts) == 1
    assert fake.pending[0][1] == "prompt-1"
    fake.finish()
    client.http._transport = httpx.MockTransport(fake.handle)
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    job_id = restarted.activity()["active_job_id"]
    assert (await restarted.status(job_id))["status"] == "unknown"
    assert (await restarted.cancel(job_id))["status"] == "unknown"
    with pytest.raises(GenerationBusyError):
        await restarted.submit(key)
    # Without the acknowledged ID, unrelated queue/history cannot prove ownership.
    assert len(fake.calls) == len(fake.prompts) == 1


@pytest.mark.parametrize("ack", [None, ProviderJob(""), ProviderJob(None), ProviderJob("x" * 513)])
async def test_invalid_adapter_ack_is_unknown_and_journal_stays_recoverable(
    settings, monkeypatch, ack
):
    workflows, provider, providers, jobs, key = make_store(settings)

    async def accepted(request, job_id):
        provider.requests.append((request, job_id))
        return ack

    monkeypatch.setattr(provider, "submit", accepted)
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(key)
    job_id = jobs.activity()["active_job_id"]
    assert (await jobs.status(job_id))["status"] == "unknown"
    restarted = JobStore(workflows, providers, jobs.capabilities, jobs.output_dir)
    assert (await restarted.status(job_id))["status"] == "unknown"
    with pytest.raises(GenerationBusyError):
        await restarted.submit(key)
    assert len(provider.requests) == 1


async def test_accepted_journal_failure_reconciles_by_known_id(settings, monkeypatch):
    workflows, provider, providers, jobs, key = make_store(settings)
    persist = jobs._persist_active

    def fail_ack(job):
        if job.provider_execution_id:
            raise OSError("journal unavailable")
        persist(job)

    monkeypatch.setattr(jobs, "_persist_active", fail_ack)
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(key)
    job_id = jobs.activity()["active_job_id"]
    assert jobs.activity()["busy"]
    # The prior durable pre-POST record still fences an unidentifiable accepted job.
    restarted = JobStore(workflows, providers, jobs.capabilities, jobs.output_dir)
    assert (await restarted.status(job_id))["status"] == "unknown"
    assert restarted.activity()["busy"]
    # In the original process the acknowledged identity permits terminal reconciliation.
    from flamoris_generation_controller.providers.base import JobSnapshot

    provider.snapshots["execution-1"] = JobSnapshot(status="cancelled")
    assert (await jobs.status(job_id))["status"] == "cancelled"
    assert not jobs.activity()["busy"]
    record = json.loads((jobs.output_dir / "job-authority" / "active.json").read_text())
    assert record == {"active": None}


@pytest.mark.parametrize("identity", [None, [], 42, "x\n", "x" * 513])
async def test_invalid_recovered_identity_fails_startup(settings, identity):
    workflows, _, providers, jobs, key = make_store(settings)
    await jobs.submit(key)
    record = jobs._authority.read("active.json")
    record["active"]["execution_id"] = identity
    jobs._authority.write("active.json", record)
    with pytest.raises(ValueError, match="Invalid execution reservation"):
        JobStore(workflows, providers, jobs.capabilities, jobs.output_dir)
