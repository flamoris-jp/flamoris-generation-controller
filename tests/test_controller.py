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
