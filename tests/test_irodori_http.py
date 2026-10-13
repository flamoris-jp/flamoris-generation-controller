import asyncio
import base64
import io
import json
import wave

import httpx
import pytest
from pydantic import ValidationError

from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.jobs import GenerationBusyError
from flamoris_generation_controller.providers.base import GenerationRequest, SubmissionRejected
from flamoris_generation_controller.providers.irodori_http import IrodoriHTTPProvider
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.speech import SpeechRecipe, speech_descriptor, speech_profile


def wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\0\0" * 2400)
    return stream.getvalue()


class Server:
    def __init__(self):
        self.loaded = True
        self.model = "irodori-tts"
        self.posts = []
        self.response = httpx.Response(200, content=wav(), headers={"content-type": "audio/wav"})
        self.failure = None
        self.started = asyncio.Event()
        self.release = None

    async def handle(self, request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model": {"id": self.model},
                    "runtime": {"loaded": self.loaded},
                },
            )
        assert request.url.path == "/v1/audio/speech"
        self.posts.append(json.loads(request.content))
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.failure:
            raise self.failure
        return self.response


def provider(tmp_path, server):
    settings = Settings(irodori_url="http://127.0.0.1:8088", irodori_token="private-test-token")
    return IrodoriHTTPProvider(settings, tmp_path, transport=httpx.MockTransport(server.handle))


def request():
    return GenerationRequest(
        "speech.generate",
        "a" * 32,
        SpeechRecipe(
            parameters={
                "text": "こんにちは",
                "caption": "穏やかな声",
                "seconds": 3.0,
                "steps": 20,
                "seed": 17,
            }
        ),
    )


async def finish(instance, execution):
    await instance._runs[execution].worker
    return await instance.inspect(execution)


@pytest.mark.parametrize(
    "values",
    [
        {"irodori_url": "http://host/v1"},
        {"irodori_url": "http://user:pass@host"},
        {"irodori_url": "http://host?key=private"},
        {"irodori_url": "http://host#fragment"},
        {"irodori_url": "file:///tmp/server"},
        {"irodori_url": "http://host", "irodori_config": "/tmp/config"},
        {"irodori_token": "private\n"},
        {"irodori_timeout": float("nan")},
    ],
)
def test_config_rejects_ambiguous_or_unsafe_transport(values):
    with pytest.raises(ValidationError):
        Settings(**values)


async def test_loaded_health_and_single_bounded_payload(tmp_path):
    server = Server()
    instance = provider(tmp_path, server)
    try:
        assert (await instance.health()).available
        made = await instance.submit(request(), "b" * 32)
        snapshot = await finish(instance, made.execution_id)
        assert snapshot.status == "completed"
        assert snapshot.outputs[0].role.as_dict() == {
            "port": "audio",
            "role": "audio",
            "role_index": 0,
        }
        assert await instance.materialize(made.execution_id, "speech.wav") == wav()
        assert len(server.posts) == 1
        assert server.posts[0] == {
            "model": "irodori-tts",
            "input": "こんにちは",
            "response_format": "wav",
            "speed": 1.0,
            "irodori": {
                "no_ref": True,
                "caption": "穏やかな声",
                "seconds": 3.0,
                "num_steps": 20,
                "seed": 17,
                "num_candidates": 1,
                "decode_mode": "sequential",
                "chunking_enabled": False,
                "min_seconds": 0.5,
                "max_seconds": 30,
                "max_text_len": 256,
                "max_caption_len": 256,
            },
        }
        path = tmp_path / made.execution_id / "speech.wav"
        path.write_bytes(wav() + b"changed")
        with pytest.raises(ValueError, match="changed"):
            await instance.materialize(made.execution_id, "speech.wav")
    finally:
        await instance.close()


@pytest.mark.parametrize(
    "loaded,model", [(False, "irodori-tts"), (1, "irodori-tts"), (True, "other")]
)
async def test_unloaded_or_mismatched_runtime_never_submits(tmp_path, loaded, model):
    server = Server()
    server.loaded, server.model = loaded, model
    instance = provider(tmp_path, server)
    try:
        assert not (await instance.health()).available
        with pytest.raises(SubmissionRejected):
            await instance.submit(request(), "b" * 32)
        assert server.posts == []
    finally:
        await instance.close()


@pytest.mark.parametrize(
    "response,status",
    [
        (httpx.Response(400), "failed"),
        (httpx.Response(500), "unknown"),
        (httpx.Response(302, headers={"location": "http://other/speech"}), "unknown"),
        (httpx.Response(200, content=b"invalid", headers={"content-type": "audio/wav"}), "failed"),
        (httpx.Response(200, content=wav(), headers={"content-type": "text/html"}), "failed"),
        (
            httpx.Response(200, content=wav(), headers={"content-length": str(9 * 1024**2)}),
            "unknown",
        ),
    ],
)
async def test_response_settlement_is_truthful(tmp_path, response, status):
    server = Server()
    server.response = response
    instance = provider(tmp_path, server)
    try:
        made = await instance.submit(request(), "b" * 32)
        assert (await finish(instance, made.execution_id)).status == status
        assert len(server.posts) == 1
    finally:
        await instance.close()


async def test_cancel_keeps_original_request_until_response(tmp_path):
    server = Server()
    server.release = asyncio.Event()
    instance = provider(tmp_path, server)
    try:
        made = await instance.submit(request(), "b" * 32)
        await server.started.wait()
        assert (await instance.cancel(made.execution_id)).status == "cancel_requested"
        assert not instance._runs[made.execution_id].worker.done()
        server.release.set()
        assert (await finish(instance, made.execution_id)).status == "completed"
        assert len(server.posts) == 1
    finally:
        await instance.close()


async def test_lost_response_shared_reservation_and_restart_never_replay(settings):
    server = Server()
    server.failure = httpx.ReadTimeout("private upstream detail")
    settings = settings.model_copy(update={"irodori_url": httpx.URL("http://127.0.0.1:8088")})
    context = CallerContext.internal()
    transport = httpx.MockTransport(server.handle)
    instance = GenerationController(settings, transport=transport)
    recipe = await instance.invoke(
        "workflows.build",
        {
            "template": "speech-no-reference",
            "parameters": {"text": "こんにちは"},
        },
        context=context,
    )
    assert recipe["speech"] == speech_profile(http=True)
    assert instance.workflows.list()["descriptors"][-1] == speech_descriptor(http=True)
    made = await instance.invoke(
        "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
    )
    speech = instance.providers.get("irodori")
    await finish(speech, next(iter(speech._runs)))
    assert (await instance.invoke("jobs.status", {"job_id": made["job_id"]}, context=context))[
        "status"
    ] == "unknown"
    with pytest.raises(GenerationBusyError, match="busy"):
        await instance.invoke(
            "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
        )
    await instance.close()
    restarted = GenerationController(settings, transport=transport)
    try:
        assert (await restarted.invoke("jobs.status", {"job_id": made["job_id"]}, context=context))[
            "status"
        ] == "unknown"
        assert len(server.posts) == 1
    finally:
        await restarted.close()


async def test_success_uses_controller_assets_and_releases_shared_capacity(settings):
    server = Server()
    settings = Settings(**{**settings.model_dump(), "irodori_url": "http://127.0.0.1:8088"})
    instance = GenerationController(settings, transport=httpx.MockTransport(server.handle))
    context = CallerContext.internal()
    try:
        recipe = await instance.invoke(
            "workflows.build",
            {
                "template": "speech-no-reference",
                "parameters": {"text": "こんにちは"},
            },
            context=context,
        )
        made = await instance.invoke(
            "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
        )
        speech = instance.providers.get("irodori")
        await finish(speech, next(iter(speech._runs)))
        assert (await instance.invoke("jobs.status", {"job_id": made["job_id"]}, context=context))[
            "status"
        ] == "completed"
        assets = await instance.invoke("assets.list", {"job_id": made["job_id"]}, context=context)
        asset_id = assets["assets"][0]["asset_id"]
        prepared = await instance.invoke("assets.prepare", {"asset_id": asset_id}, context=context)
        content = await instance.invoke(
            "assets.read",
            {
                "asset_id": asset_id,
                "sha256": prepared["sha256"],
                "offset": 0,
            },
            context=context,
        )
        assert base64.b64decode(content["data_base64"]) == wav()
        assert prepared["mime_type"] == "audio/wav"
        again = await instance.invoke(
            "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
        )
        assert again["job_id"] != made["job_id"]
    finally:
        await instance.close()
