import base64
import hashlib
import io
import json
import wave
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from test_irodori_http import Server, finish, wav

from flamoris_generation_controller.contracts import CallerContext
from flamoris_generation_controller.providers.base import SubmissionRejected
from flamoris_generation_controller.reference_audio import validate_reference_audio
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.speech import (
    ReferenceSpeechRecipe,
    reference_speech_descriptor,
    speech_profile,
)


class ReferenceServer(Server):
    def __init__(self):
        super().__init__()
        self.checkpoint = "Aratako/Irodori-TTS-v4.1-Small"
        self.voices = {}
        self.uploads = []
        self.deleted = []
        self.upload_status = 201
        self.cleanup_status = 200

    async def handle(self, request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "model": {"id": self.model, "hf_checkpoint": self.checkpoint},
                    "runtime": {"loaded": self.loaded},
                },
            )
        if request.url.path == "/v1/audio/voices":
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: "
                + request.headers["content-type"].encode()
                + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + request.content
            )
            parts = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            key = parts["voice_id"].decode()
            assert key.startswith("flamoris_") and len(key) == 41
            self.uploads.append(parts)
            if self.upload_status != 201:
                return httpx.Response(self.upload_status)
            assert key not in self.voices
            self.voices[key] = parts["file"]
            return httpx.Response(
                201,
                json={
                    "id": key,
                    "object": "voice_file",
                    "filename": key + ".wav",
                    "bytes": len(parts["file"]),
                },
            )
        if request.method == "DELETE":
            key = request.url.path.rsplit("/", 1)[-1]
            if self.cleanup_status != 200:
                return httpx.Response(self.cleanup_status)
            self.deleted.append(key)
            self.voices.pop(key, None)
            return httpx.Response(200, json={"id": key, "deleted": True})
        return await super().handle(request)


async def upload(instance, data=None):
    data = wav() if data is None else data
    key = "c" * 32
    args = {
        "upload_id": key,
        "mime_type": "audio/wav",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    await instance.uploads.begin(**args)
    await instance.uploads.write(key, 0, base64.b64encode(data).decode(), args["sha256"])
    return await instance.uploads.finish(key)


def controller(settings, server):
    settings = settings.model_copy(update={"irodori_url": httpx.URL("http://127.0.0.1:8088")})
    return GenerationController(settings, transport=httpx.MockTransport(server.handle))


async def build(instance, key):
    return await instance.invoke(
        "workflows.build",
        {
            "template": "speech-reference",
            "parameters": {"text": "Reference speech", "reference_audio": key},
        },
        context=CallerContext.internal(),
    )


async def test_reference_upload_generate_cleanup_and_retained_identity(settings):
    server = ReferenceServer()
    instance = controller(settings, server)
    try:
        metadata = await upload(instance)
        assert metadata["source_kind"] == "upload"
        assert metadata["media_kind"] == "audio"
        recipe = await build(instance, metadata["input_id"])
        assert recipe["schema_version"] == 9
        assert recipe["speech"] == speech_profile(True, True)
        assert reference_speech_descriptor() in instance.workflows.list()["descriptors"]
        instance.workflows.save(recipe["workflow_id"])
        instance.workflows._recipes.clear()
        assert isinstance(instance.workflows.get(recipe["workflow_id"]), ReferenceSpeechRecipe)
        job = await instance.jobs.submit(recipe["workflow_id"])
        speech = instance.providers.get("irodori")
        execution = job["provider_execution_id"]
        assert (await finish(speech, execution)).status == "completed"
        result = await instance.jobs.status(job["job_id"])
        assert result["managed_inputs"]["reference_audio"]["sha256"] == metadata["sha256"]
        assert server.uploads[0]["file"] == wav()
        assert server.posts[0]["voice"] == "flamoris_" + execution
        assert server.posts[0]["irodori"]["no_ref"] is False
        assert server.posts[0]["irodori"]["max_ref_seconds"] == 30
        assert "ref_wav" not in server.posts[0]["irodori"]
        assert server.deleted == [server.posts[0]["voice"]]
        assert server.voices == {}
        receipt = settings.output_dir / "irodori-http-staging" / execution / "reference.json"
        assert not receipt.exists()
        # References remain reusable; remote temporary voices are not a second input owner.
        assert instance.inputs.get(metadata["input_id"])["sha256"] == metadata["sha256"]
    finally:
        await instance.close()


@pytest.mark.parametrize(
    "checkpoint", ["Aratako/Irodori-TTS-500M-v2-VoiceDesign", None, {}, "other"]
)
async def test_non_reference_models_never_upload_or_synthesize(settings, checkpoint):
    server = ReferenceServer()
    server.checkpoint = checkpoint
    instance = controller(settings, server)
    try:
        metadata = await upload(instance)
        recipe = await build(instance, metadata["input_id"])
        with pytest.raises(SubmissionRejected, match="cannot accept"):
            await instance.jobs.submit(recipe["workflow_id"])
        assert not server.posts and not server.uploads
        assert instance.jobs.activity()["busy"] is False
    finally:
        await instance.close()


async def test_bad_or_missing_reference_never_falls_back(settings):
    server = ReferenceServer()
    instance = controller(settings, server)
    try:
        with pytest.raises(ValueError):
            await upload(instance, b"RIFFinvalid")
        recipe = await build(instance, "d" * 32)
        with pytest.raises(SubmissionRejected):
            await instance.jobs.submit(recipe["workflow_id"])
        assert not server.posts and not server.uploads
    finally:
        await instance.close()


@pytest.mark.parametrize("upload_status", [400, 409, 500])
async def test_upload_failure_never_submits_or_deletes_another_voice(settings, upload_status):
    server = ReferenceServer()
    server.upload_status = upload_status
    instance = controller(settings, server)
    try:
        metadata = await upload(instance)
        recipe = await build(instance, metadata["input_id"])
        job = await instance.jobs.submit(recipe["workflow_id"])
        snapshot = await finish(instance.providers.get("irodori"), job["provider_execution_id"])
        assert snapshot.status == "failed"
        assert not server.posts and not server.deleted
    finally:
        await instance.close()


async def test_ambiguous_speech_retains_voice_and_never_replays(settings):
    server = ReferenceServer()
    server.failure = httpx.ReadTimeout("private response")
    instance = controller(settings, server)
    try:
        metadata = await upload(instance)
        recipe = await build(instance, metadata["input_id"])
        job = await instance.jobs.submit(recipe["workflow_id"])
        speech = instance.providers.get("irodori")
        snapshot = await finish(speech, job["provider_execution_id"])
        assert snapshot.status == "unknown"
        assert len(server.voices) == 1 and not server.deleted
        await instance.jobs.status(job["job_id"])
        assert instance.jobs.activity()["busy"] is True
    finally:
        await instance.close()
    restarted = controller(settings, server)
    try:
        assert (await restarted.jobs.status(job["job_id"]))["status"] == "unknown"
        assert len(server.posts) == 1 and len(server.uploads) == 1
        assert len(server.voices) == 1
    finally:
        await restarted.close()


async def test_cleanup_failure_retains_receipt_without_losing_valid_output(settings):
    server = ReferenceServer()
    server.cleanup_status = 500
    instance = controller(settings, server)
    try:
        metadata = await upload(instance)
        recipe = await build(instance, metadata["input_id"])
        job = await instance.jobs.submit(recipe["workflow_id"])
        speech = instance.providers.get("irodori")
        assert (await finish(speech, job["provider_execution_id"])).status == "completed"
        receipt = (
            settings.output_dir
            / "irodori-http-staging"
            / job["provider_execution_id"]
            / "reference.json"
        )
        assert json.loads(receipt.read_text())["voice_id"] in server.voices
        assert await speech.materialize(job["provider_execution_id"], "speech.wav") == wav()
    finally:
        await instance.close()


@pytest.mark.parametrize("channels,rate", [(1, 24000), (2, 44100), (2, 48000)])
def test_pcm_reference_bounds_and_truncation(channels, rate):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * channels * 100)
    data = stream.getvalue()
    assert validate_reference_audio(data)["channels"] == channels
    with pytest.raises(ValueError):
        validate_reference_audio(data[:-1])


@pytest.mark.parametrize("raw", [None, [], {}, {"status": "ok", "model": None}])
async def test_malformed_health_is_unavailable_without_reference_side_effects(settings, raw):
    async def handle(request):
        assert request.url.path == "/health"
        return httpx.Response(200, json=raw)

    instance = controller(settings, ReferenceServer())
    speech = instance.providers.get("irodori")
    await speech.client.aclose()
    speech.client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8088", transport=httpx.MockTransport(handle)
    )
    try:
        health = await speech.health()
        assert not health.available and health.details["reference_audio"] is False
    finally:
        await instance.close()
