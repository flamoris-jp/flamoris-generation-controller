import asyncio
import io
import json
import wave

import httpx
import pytest
from pydantic import ValidationError

from flamoris_generation_controller.music import MusicRecipe, music_descriptor
from flamoris_generation_controller.providers import yue2
from flamoris_generation_controller.providers.base import (
    GenerationRequest,
    ProviderError,
    SubmissionRejected,
    SubmissionUnknown,
)

EXECUTION = "a" * 16
JOB = "b" * 32


def request(**parameters):
    return GenerationRequest(
        "music.generate",
        "c" * 32,
        MusicRecipe(parameters={"style": "quiet piano", **parameters}),
    )


def wav(*, channels=2, rate=48000, width=2):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(b"\0" * channels * width * 480)
    return stream.getvalue()


def result(**changes):
    replay = {
        "style": "quiet piano",
        "duration": 30,
        "lm_seed": 0,
        "seed": 0,
        "output_format": "wav16",
        "abc": "X:1\nM:4/4\nL:1/4\nK:C\nC D E F|",
        "semantic_tokens": "0,1,2",
        **changes,
    }
    return yue2.JSON_HEAD + json.dumps(replay).encode() + yue2.AUDIO_HEAD + wav() + yue2.END


def response(status=200, *, content=None, mime="application/json", headers=None):
    body = json.dumps(content).encode() if not isinstance(content, bytes) else content
    return httpx.Response(
        status,
        stream=httpx.ByteStream(body),
        headers={"Content-Type": mime, **(headers or {})},
    )


class Server:
    def __init__(self):
        self.calls = []
        self.state = "running"
        self.body = result()
        self.submit_response = None
        self.job_response = None
        self.result_headers = {}
        self.health_response = None

    async def __call__(self, req):
        self.calls.append(req)
        if req.url.path == "/health":
            if isinstance(self.health_response, Exception):
                raise self.health_response
            if self.health_response is not None:
                return self.health_response
            return response(content={"status": "ok"})
        if req.url.path == "/synth":
            assert req.method == "POST"
            payload = json.loads(req.content)
            assert payload["cot"] == "full" and payload["output_format"] == "wav16"
            assert payload["lm_batch_size"] == payload["synth_batch_size"] == 1
            assert "abc" not in payload and "semantic_tokens" not in payload
            if isinstance(self.submit_response, Exception):
                raise self.submit_response
            return self.submit_response or response(content={"id": EXECUTION})
        assert req.url.path == "/job" and req.url.params["id"] == EXECUTION
        if "result" in req.url.params:
            return response(
                content=self.body, mime=yue2.MULTIPART_MIME, headers=self.result_headers
            )
        if req.method == "POST":
            assert req.url.params["cancel"] == "1"
        return self.job_response or response(content={"status": self.state})


@pytest.fixture
async def provider(tmp_path):
    server = Server()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    config = yue2.Yue2Config(
        url="http://provider.test",
        source_revision=yue2.YUE2_SOURCE_REVISION,
        model_revision="fixture-BF16",
    )
    value = yue2.Yue2Provider(config, tmp_path / "stage", client=client)
    value.test_server = server
    yield value
    server.state = "cancelled"
    await value.close()


async def test_health_declares_configuration_without_runtime_attestation(provider):
    health = await provider.health()
    assert health.available
    assert health.details["runtime_verified"] is False
    assert health.details["qualification"] == "configured-http-contract"
    assert "provider.test" not in str(health.as_dict())
    descriptor = music_descriptor()
    assert descriptor["readiness"] == {"status": "not-attested"}
    assert descriptor["music"]["tracks"] == 1


async def test_multipart_assets_and_hash_checked_materialization(provider):
    accepted = await provider.submit(request(), JOB)
    assert accepted.execution_id == EXECUTION
    assert (await provider.inspect(EXECUTION)).status == "running"
    provider.test_server.state = "done"
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "completed"
    assert [item.role.role for item in snapshot.outputs] == ["audio", "score", "metadata"]
    assert [item.mime_type for item in snapshot.outputs] == [
        "audio/wav",
        "text/vnd.abc",
        "application/json",
    ]
    assert await provider.materialize(EXECUTION, "audio") == wav()
    assert b"K:C" in await provider.materialize(EXECUTION, "score")
    replay = json.loads(await provider.materialize(EXECUTION, "metadata"))
    assert replay["cot"] == "full" and replay["seed"] == 0 and replay["lm_seed"] == 0
    chunks = [chunk async for chunk in provider.stream_output(EXECUTION, "audio")]
    assert b"".join(chunks) == wav()
    path = provider.stage_root / JOB / "audio.wav"
    path.write_bytes(wav()[:-1])
    with pytest.raises(ProviderError, match="unavailable"):
        await provider.materialize(EXECUTION, "audio")


async def test_fractional_duration_matches_upstream_float32_roundtrip(provider):
    await provider.submit(request(seconds=1.123456789), JOB)
    provider.test_server.state = "done"
    provider.test_server.body = result(duration=1.1234568)
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "completed"
    replay = json.loads(await provider.materialize(EXECUTION, "metadata"))
    assert yue2._float32(replay["duration"]) == yue2._float32(1.123456789)
    assert json.loads(provider.test_server.calls[1].content)["duration"] == replay["duration"]


async def test_scoped_cancel_does_not_claim_running_job_settled(provider):
    await provider.submit(request(), JOB)
    snapshot = await provider.cancel(EXECUTION)
    assert snapshot.status == "cancel_requested"
    call = provider.test_server.calls[-1]
    assert call.method == "POST" and dict(call.url.params) == {"id": EXECUTION, "cancel": "1"}
    provider.test_server.state = "cancelled"
    assert (await provider.inspect(EXECUTION)).status == "cancelled"


async def test_cancel_race_preserves_completed_output(provider):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    assert (await provider.cancel(EXECUTION)).status == "completed"
    assert await provider.materialize(EXECUTION, "audio") == wav()


async def test_missing_job_history_cannot_release_cancel_fence(provider):
    await provider.submit(request(), JOB)
    provider.test_server.job_response = response(404, content={"error": "private detail"})
    assert (await provider.cancel(EXECUTION)).status == "unknown"
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "unknown"
    assert "private detail" not in str(snapshot.as_dict())


async def test_deadline_waits_for_targeted_cancellation_settlement(provider):
    await provider.submit(request(), JOB)
    provider._runs[EXECUTION].deadline = 0
    assert (await provider.inspect(EXECUTION)).status == "cancel_requested"
    assert provider.test_server.calls[-1].method == "POST"
    provider.test_server.state = "cancelled"
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "failed"
    assert snapshot.error == {"code": "execution_timeout"}


async def test_deadline_cancels_owned_job_even_when_client_stops_polling(provider):
    provider.config = provider.config.model_copy(update={"execution_timeout_seconds": 0.02})
    await provider.submit(request(), JOB)
    async with asyncio.timeout(1):
        while len(provider.test_server.calls) < 3:
            await asyncio.sleep(0.01)
    assert provider.test_server.calls[-1].method == "POST"
    assert provider.test_server.calls[-1].url.params["cancel"] == "1"
    assert provider._runs[EXECUTION].snapshot.status == "cancel_requested"
    provider.test_server.state = "cancelled"
    assert (await provider.inspect(EXECUTION)).error == {"code": "execution_timeout"}


async def test_late_completion_after_deadline_is_failure(provider):
    await provider.submit(request(), JOB)
    provider._runs[EXECUTION].deadline = 0
    provider.test_server.state = "done"
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "failed" and snapshot.error["code"] == "execution_timeout"
    assert not any("result" in call.url.params for call in provider.test_server.calls)


@pytest.mark.parametrize("status", [301, 401, 404, 500])
async def test_ambiguous_submit_never_retries(provider, status):
    provider.test_server.submit_response = response(status, content={"error": "provider secret"})
    with pytest.raises(SubmissionUnknown, match="outcome unknown"):
        await provider.submit(request(), JOB)
    assert len(provider.test_server.calls) == 2
    assert sum(call.url.path == "/synth" for call in provider.test_server.calls) == 1
    assert not provider._runs


async def test_confirmed_parameter_rejection(provider):
    provider.test_server.submit_response = response(400, content={"error": "private input"})
    with pytest.raises(SubmissionRejected, match="rejected"):
        await provider.submit(request(), JOB)
    assert not provider._runs


async def test_submit_transport_error_keeps_unknown_without_retry(provider):
    provider.test_server.submit_response = httpx.ReadError("secret transport detail")
    with pytest.raises(SubmissionUnknown) as error:
        await provider.submit(request(), JOB)
    assert "secret" not in str(error.value) and len(provider.test_server.calls) == 2


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("offline runtime"),
        response(503, content={"status": "stopped"}),
        response(content={"status": "not-ready"}),
    ],
)
async def test_offline_provider_is_definitive_rejection_without_generation_post(provider, failure):
    provider.test_server.health_response = failure
    with pytest.raises(SubmissionRejected, match="unavailable"):
        await provider.submit(request(), JOB)
    assert len(provider.test_server.calls) == 1
    assert provider.test_server.calls[0].method == "GET"
    assert not provider._runs
    assert not provider.stage_root.exists()


@pytest.mark.parametrize("ack", [{"id": "../../unsafe"}, {"id": 123}, {"wrong": EXECUTION}])
async def test_malformed_acknowledgement_is_ambiguous(provider, ack):
    provider.test_server.submit_response = response(content=ack)
    with pytest.raises(SubmissionUnknown):
        await provider.submit(request(), JOB)


@pytest.mark.parametrize(
    "changes",
    [
        {"abc": "not a score"},
        {"abc": "K:C\n\0"},
        {"abc": "K:C\n%%include /private/source.abc"},
        {"abc": "K:C\n%%exec command"},
        {"style": "different recipe"},
        {"seed": True},
        {"semantic_tokens": ""},
        {"semantic_tokens": "0,path"},
        {"semantic_tokens": "32768"},
        {"semantic_tokens": "99999"},
        {"semantic_tokens": "1," * 10000 + "1"},
        {"provider_path": "/private/weights"},
        {"duration": float("nan")},
        {"synth_batch_size": 2},
    ],
)
async def test_invalid_replay_cannot_publish_success(provider, changes):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    provider.test_server.body = result(**changes)
    snapshot = await provider.inspect(EXECUTION)
    assert snapshot.status == "failed" and snapshot.error == {"code": "music_output_invalid"}
    assert not snapshot.outputs


@pytest.mark.parametrize(
    "body",
    [
        result()[:-1],
        result() + result(),
        yue2.JSON_HEAD + b'{"seed":0,"seed":0}' + yue2.AUDIO_HEAD + wav() + yue2.END,
        yue2.JSON_HEAD + b"[]" + yue2.AUDIO_HEAD + wav() + yue2.END,
        result().replace(yue2.AUDIO_HEAD, b"audio/mpeg"),
        result().replace(wav(), wav(channels=1)),
        result().replace(wav(), wav(rate=24000)),
        result().replace(wav(), wav(width=4)),
    ],
)
async def test_invalid_multipart_or_wav_is_terminal_failure(provider, body):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    provider.test_server.body = body
    assert (await provider.inspect(EXECUTION)).status == "failed"


async def test_bounded_result_rejects_declared_oversize(provider):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    provider.test_server.result_headers = {"Content-Length": str(yue2.MAX_RESULT_BYTES + 1)}
    assert (await provider.inspect(EXECUTION)).error == {"code": "music_output_invalid"}


async def test_bounded_result_rejects_streamed_oversize(provider, monkeypatch):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    monkeypatch.setattr(yue2, "MAX_RESULT_BYTES", 256)
    assert (await provider.inspect(EXECUTION)).error == {"code": "music_output_invalid"}


async def test_encoded_result_is_not_decompressed(provider):
    await provider.submit(request(), JOB)
    provider.test_server.state = "done"
    provider.test_server.result_headers = {"Content-Encoding": "gzip"}
    assert (await provider.inspect(EXECUTION)).status == "failed"


async def test_unknown_execution_is_never_adopted_or_cancelled_after_restart(provider):
    await provider.submit(request(), JOB)
    replacement = yue2.Yue2Provider(provider.config, provider.stage_root, client=provider.client)
    calls = len(provider.test_server.calls)
    assert (await replacement.inspect(EXECUTION)).status == "unknown"
    assert (await replacement.cancel(EXECUTION)).status == "unknown"
    assert len(provider.test_server.calls) == calls
    with pytest.raises(ProviderError):
        await replacement.materialize(EXECUTION, "audio")


async def test_unsafe_local_stage_rejected_before_post(provider, tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    provider.stage_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(SubmissionRejected, match="staging unavailable"):
        await provider.submit(request(), JOB)
    assert not provider.test_server.calls


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@provider",
        "http://provider?token=secret",
        "ftp://provider",
        "http://provider/#route",
        "http://provider:99999",
        "http://provider/\nprivate",
    ],
)
def test_operator_url_must_not_embed_credentials_or_non_http_route(url):
    with pytest.raises(ValidationError):
        yue2.Yue2Config(url=url, source_revision=yue2.YUE2_SOURCE_REVISION, model_revision="test")


@pytest.mark.parametrize(
    "parameters",
    [
        {"style": " "},
        {"style": "x\0"},
        {"seconds": 0},
        {"seconds": float("inf")},
        {"steps": True},
        {"steps": 65},
        {"seed": -1},
        {"seed": 2**53},
        {"lm_seed": -1},
        {"abc": "caller raw score"},
        {"style": "a" * 1025},
    ],
)
def test_music_recipe_rejects_unsafe_or_unbounded_inputs(parameters):
    with pytest.raises(ValidationError):
        request(**parameters)


def test_only_deployed_source_contract_can_be_configured():
    with pytest.raises(ValidationError):
        yue2.Yue2Config(url="http://provider", source_revision="wrong", model_revision="test")
