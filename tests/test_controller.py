import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from flamoris_generation_controller.authority import Authority
from flamoris_generation_controller.contracts import API_PATH, CallerContext, ControllerError
from flamoris_generation_controller.http_api import HTTPAPI
from flamoris_generation_controller.runtime import GenerationController

TOKEN = "fixture-controller-service-credential-32"
BUILD = {
    "template": "text-to-image",
    "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
}


@pytest.fixture
async def controller(settings, fake):
    instance = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
async def http(controller):
    api = HTTPAPI(controller, TOKEN)
    app = Starlette(routes=[Route(API_PATH + "/{operation}", api.__call__, methods=["POST"])])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://controller.test" + API_PATH + "/",
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        yield client


def test_core_imports_with_mcp_blocked():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['mcp']=None; "
            "from flamoris_generation_controller.runtime import GenerationController; "
            "from flamoris_generation_controller.retention import RetentionStore",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


async def test_duplicate_runtime_is_excluded_before_construction(
    controller, settings, fake, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("duplicate authority must not construct providers or recover jobs")

    monkeypatch.setattr("flamoris_generation_controller.runtime.ModelCatalog", forbidden)
    with pytest.raises(ControllerError, match="authority_busy"):
        GenerationController(settings, transport=httpx.MockTransport(fake.handle))


async def test_ownership_lock_excludes_another_process_then_releases(controller, settings):
    code = (
        "from pathlib import Path; from flamoris_generation_controller.authority import Authority; "
        "from flamoris_generation_controller.contracts import ControllerError; "
        "import sys\ntry: owner=Authority(Path(sys.argv[1]))\n"
        "except ControllerError as e: print(e.code); sys.exit(7)\nowner.close()"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(settings.output_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7 and result.stdout.strip() == "authority_busy"
    await controller.close()
    result = subprocess.run(
        [sys.executable, "-c", code, str(settings.output_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("target", ["lock", "namespace", "root"])
async def test_replaced_authority_paths_fence_all_dispatch(controller, settings, target):
    root = settings.output_dir
    path = (
        root / "controller-authority" / "owner.lock"
        if target == "lock"
        else (root / "controller-authority" if target == "namespace" else root)
    )
    path.rename(path.with_name(path.name + "-retained"))
    if target == "lock":
        path.write_bytes(b"")
    else:
        path.mkdir()
    with pytest.raises(ControllerError, match="authority_unavailable"):
        await controller.invoke("workflows.build", BUILD, context=CallerContext.internal())
    assert controller.workflows._recipes == {}


def test_symlink_or_hardlink_lock_is_rejected(tmp_path):
    root = tmp_path / "outputs"
    directory = root / "controller-authority"
    directory.mkdir(parents=True)
    other = tmp_path / "other"
    other.write_bytes(b"")
    lock = directory / "owner.lock"
    lock.symlink_to(other)
    with pytest.raises((OSError, ControllerError)):
        Authority(root)
    lock.unlink()
    os.link(other, lock)
    with pytest.raises(ControllerError):
        Authority(root)


async def test_permission_checked_before_effects(controller):
    context = CallerContext("internal-service", frozenset({"system.health"}))
    with pytest.raises(ControllerError, match="forbidden"):
        await controller.invoke("workflows.build", BUILD, context=context)
    assert controller.workflows._recipes == {}


@pytest.mark.parametrize("cancel_closer", [False, True])
async def test_shutdown_keeps_authority_until_admitted_submit_finishes(
    settings, fake, cancel_closer
):
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(request):
        if request.url.path == "/prompt":
            started.set()
            await release.wait()
        return fake.handle(request)

    instance = GenerationController(settings, transport=httpx.MockTransport(provider))
    context = CallerContext.internal()
    recipe = await instance.invoke("workflows.build", BUILD, context=context)
    submission = asyncio.create_task(
        instance.invoke("jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context)
    )
    closers = []
    try:
        await asyncio.wait_for(started.wait(), 2)
        closers.append(asyncio.create_task(instance.close()))
        await asyncio.sleep(0)
        closers.append(asyncio.create_task(instance.close()))
        await asyncio.sleep(0)
        if cancel_closer:
            closers[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await closers[0]
        assert not closers[-1].done(), "shutdown must wait for the accepted operation"
        try:
            probe = Authority(settings.output_dir)
        except ControllerError as error:
            assert error.code == "authority_busy"
        else:
            probe.close()
            pytest.fail("another owner was admitted while submit still had effects pending")
        with pytest.raises(ControllerError, match="authority_unavailable"):
            await instance.invoke("workflows.build", BUILD, context=context)
        release.set()
        assert (await submission)["status"] == "queued"
        await closers[-1]
        assert instance.client.http.is_closed
        probe = Authority(settings.output_dir)
        probe.close()
        assert (settings.output_dir / "job-authority" / "active.json").exists()
    finally:
        release.set()
        await asyncio.gather(submission, *closers, return_exceptions=True)
        await instance.close()


async def test_cancelled_admitted_submit_drains_shutdown_and_preserves_unknown(settings, fake):
    started, release = asyncio.Event(), asyncio.Event()

    async def provider(request):
        if request.url.path == "/prompt":
            started.set()
            await release.wait()
        return fake.handle(request)

    instance = GenerationController(settings, transport=httpx.MockTransport(provider))
    context = CallerContext.internal()
    recipe = await instance.invoke("workflows.build", BUILD, context=context)
    submission = asyncio.create_task(
        instance.invoke("jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context)
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        submission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submission
        await asyncio.wait_for(instance.close(), 2)
    finally:
        release.set()
        await asyncio.gather(submission, return_exceptions=True)
        await instance.close()
    restarted = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    try:
        assert restarted.jobs.activity()["busy"] is True
        recipe = await restarted.invoke("workflows.build", BUILD, context=context)
        with pytest.raises(Exception, match="busy"):
            await restarted.invoke(
                "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
            )
        assert fake.prompts == []
    finally:
        await restarted.close()


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        [("Authorization", "Bearer " + TOKEN), ("Authorization", "Bearer " + TOKEN)],
    ],
)
async def test_authentication_before_effects(http, controller, headers):
    response = await http.post("workflows.build", json=BUILD, headers=headers)
    assert response.status_code == 401
    assert controller.workflows._recipes == {}


async def test_unconfigured_api_never_dispatches(controller):
    api = HTTPAPI(controller, None)
    app = Starlette(routes=[Route(API_PATH + "/{operation}", api.__call__, methods=["POST"])])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.post(
            API_PATH + "/workflows.build", json=BUILD, headers={"Authorization": "Bearer " + TOKEN}
        )
    assert response.status_code == 503 and controller.workflows._recipes == {}


async def test_long_listed_model_id_remains_inspectable(http, settings):
    # The retained domain permits model names up to 1024 UTF-8 bytes.
    path = settings.model_root / "diffusion_models"
    for part in ("a" * 200, "b" * 200, "c" * 200):
        path /= part
    path.mkdir(parents=True)
    (path / "model.safetensors").write_bytes(b"fixture only")
    listed = await http.post("models.list", json={"kind": "diffusion_model"})
    assert listed.status_code == 200
    model = listed.json()["models"][0]
    assert len(model["id"]) > 512 and len(model["name"].encode()) <= 1024
    inspected = await http.post("models.get", json={"model_id": model["id"]})
    assert inspected.status_code == 200 and inspected.json() == model


@pytest.mark.parametrize(
    "body",
    [
        b'{"template":"text-to-image","template":"other","parameters":{}}',
        b'{"template":"text-to-image","parameters":{"seed":NaN}}',
        json.dumps({**BUILD, "provenance": {"subject": "forged"}}).encode(),
        json.dumps({**BUILD, "require_ready": "false"}).encode(),
        b"[]",
        b"null",
        b"{",
    ],
)
async def test_strict_body_and_untrusted_context_are_rejected(http, controller, body):
    response = await http.post(
        "workflows.build",
        content=body,
        headers={"Content-Type": "application/json", "X-Provenance-Subject": "forged"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "validation"}}
    assert controller.workflows._recipes == {}


async def test_request_size_type_and_operation_bounds(http, controller):
    large = b"x" * (512 * 1024 + 1)
    response = await http.post(
        "workflows.build", content=large, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413

    async def chunks():
        yield large[: 256 * 1024]
        yield large[256 * 1024 :]

    response = await http.post(
        "workflows.build", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert (
        await http.post("workflows.build", content=b"{}", headers={"Content-Type": "text/plain"})
    ).status_code == 415
    assert (await http.post("workflows.register", json={})).status_code == 404
    assert (
        await http.post("workflows.build", json=BUILD, headers={"Content-Encoding": "gzip"})
    ).status_code == 415
    assert controller.workflows._recipes == {}


async def test_one_admission_across_concurrent_api_requests_and_binary_result(http, fake):
    recipe = (await http.post("workflows.build", json=BUILD)).json()["workflow_id"]
    responses = await asyncio.gather(
        *[http.post("jobs.submit", json={"workflow_id": recipe}) for _ in range(2)]
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(fake.prompts) == 1
    accepted = next(r.json() for r in responses if r.status_code == 200)
    fake.finish()
    result = await http.post("jobs.result", json={"job_id": accepted["job_id"]})
    assert result.json()["status"] == "completed"
    assets = (await http.post("assets.list", json={"job_id": accepted["job_id"]})).json()
    media = await http.post("assets.get", json={"asset_id": assets["assets"][0]["asset_id"]})
    assert media.content == b"image fixture" and media.headers["content-type"] == "image/png"
    assert media.headers["cache-control"] == "no-store"


async def test_unknown_submission_remains_reserved_after_runtime_restart(settings, fake):
    def uncertain(request):
        if request.url.path == "/prompt":
            fake.handle(request)
            raise httpx.ReadTimeout("private provider detail", request=request)
        return fake.handle(request)

    instance = GenerationController(settings, transport=httpx.MockTransport(uncertain))
    context = CallerContext.internal()
    try:
        recipe = await instance.invoke("workflows.build", BUILD, context=context)
        with pytest.raises(Exception, match="uncertain|unknown|Unknown|acknowledged|submission"):
            await instance.invoke(
                "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
            )
        assert instance.jobs.activity()["busy"]
    finally:
        await instance.close()
    restarted = GenerationController(settings, transport=httpx.MockTransport(fake.handle))
    try:
        recipe = await restarted.invoke("workflows.build", BUILD, context=context)
        with pytest.raises(Exception, match="busy"):
            await restarted.invoke(
                "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
            )
        assert len(fake.prompts) == 1 and restarted.jobs.activity()["busy"]
    finally:
        await restarted.close()
