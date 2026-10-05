import asyncio
import hashlib
import io
import json
import os
import struct
import sys
import wave
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from flamoris_generation_controller.providers import sheetsage2
from flamoris_generation_controller.providers.base import (
    GenerationRequest,
    ProviderError,
    SubmissionRejected,
    SubmissionUnknown,
)
from flamoris_generation_controller.transcription import (
    TranscriptionRecipe,
    transcription_descriptor,
)

MIDI = (
    b"MThd\0\0\0\6"
    + struct.pack(">HHH", 0, 1, 960)
    + b"MTrk\0\0\0\x0c\0\x90\x3c\x64\x60\x80\x3c\0\0\xff\x2f\0"
)
STUB = """import argparse, json, os, signal, struct, sys, time, wave
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('audio')
p.add_argument('--output')
p.add_argument('--model')
p.add_argument('--device')
p.add_argument('--dtype')
p.add_argument('--preset')
p.add_argument('--max-seconds', type=float)
p.add_argument('--melody-only', action='store_true')
p.add_argument('--local-files-only', action='store_true')
a = p.parse_args()
assert a.local_files_only and a.device == 'cpu' and a.dtype == 'fp32'
assert a.preset == 'default' and 1 <= a.max_seconds <= 120
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
assert os.environ['HF_DATASETS_OFFLINE'] == '1'
assert os.environ['CUDA_VISIBLE_DEVICES'] == '' and os.environ['HIP_VISIBLE_DEVICES'] == ''
assert 'FLAMORIS_PROVENANCE_SECRET' not in os.environ and 'HF_TOKEN' not in os.environ
assert 'HTTPS_PROXY' not in os.environ and 'PYTHONPATH' not in os.environ
assert os.environ['PYTHONNOUSERSITE'] == '1'
with wave.open(a.audio, 'rb') as f:
    mode = f.readframes(1)[0]
if mode == 2: sys.exit(1)
if mode == 3: time.sleep(60)
if mode == 4:
    print('private-provider-path ' * 20000, flush=True)
if mode == 9:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print('ready', flush=True)
    time.sleep(60)
out = Path(a.output)
midi = b'MThd\\0\\0\\0\\6' + struct.pack('>HHH', 0, 1, 960)
track = b'\\0\\x90\\x3c\\x64\\x60\\x80\\x3c\\0\\0\\xff\\x2f\\0'
midi += b'MTrk' + struct.pack('>I', len(track)) + track
if mode == 5:
    (out / 'transcription.mid').symlink_to(Path(a.model) / 'model.safetensors')
elif mode == 6:
    (out / 'extra.bin').write_bytes(b'x' * 2**20)
    time.sleep(60)
elif mode != 7:
    (out / 'transcription.mid').write_bytes(b'invalid-midi' if mode == 12 else midi)
events = {'schema_version': 'v1', 'prompts': ['timestamp', 'melody_full'], 'has_eos': True,
          'events': [{'time': 0.0, 'subbeat': 0, 'values': {'melody': [
              {'pitch': 60, 'track': 0, 'end_time': 0.5, 'duration_steps': 4, 'duration_bin': 4}
          ]}, 'tokens_by_field': {'melody': [60]}}]}
if mode == 10: events['events'][0]['private_path'] = '/private/operator/path'
if mode == 11: events['events'][0]['values']['melody'][0]['pitch'] = 128
events['private_path'] = '/private/operator/path'
(out / 'events.json').write_text(json.dumps(events))
result = {'duration_seconds': 1.0, 'melody_notes': 1, 'vocal_notes': 1,
          'instrumental_notes': 0, 'events': 1, 'abc_measures': 1,
          'abc_error': None, 'warnings': [], 'diagnostics': [],
          'audio': '/private/operator/path', 'private_path': '/private/operator/path'}
if mode == 8:
    result['abc_error'] = 'private-provider-path ABC failed'
    result['abc_measures'] = 0
else:
    (out / 'score.abc').write_text('X:1\\nT:Transcription\\nM:4/4\\nL:1/8\\nK:C\\nC2 D2 E2 F2|\\n')
(out / 'result.json').write_text(json.dumps(result))
(out / 'melody_vocal.mid').write_bytes(midi)
(out / 'key.lab').write_text('0.0\\t1.0\\tC major\\n')
(out / 'undeclared-private.json').write_text('do not publish')
"""


def wav_bytes(mode=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24000)
        stream.writeframes(bytes((mode, 0)) * 24000)
    return output.getvalue()


class FakeInputs:
    def __init__(self):
        self.mode = 1
        self.in_use = False
        self.fail = False
        self.content = None

    @asynccontextmanager
    async def stage(self, job_id, references, allowed_types):
        assert job_id == "b" * 32
        assert references == {"audio": "c" * 32}
        assert allowed_types == {"audio": {"audio/wav"}}
        if self.fail:
            raise ValueError("Source input expired")
        self.in_use = True
        data = self.content if self.content is not None else wav_bytes(self.mode)

        class Reader:
            metadata = {
                "input_id": "c" * 32,
                "source_asset_id": "a" * 32 + ":000",
                "sha256": hashlib.sha256(data).hexdigest(),
                "mime_type": "audio/wav",
                "size_bytes": len(data),
            }

            async def chunks(self):
                for offset in range(0, len(data), 8192):
                    yield data[offset : offset + 8192]
                    await asyncio.sleep(0)

        try:
            yield {"audio": Reader()}
        finally:
            self.in_use = False


@pytest.fixture
async def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAMORIS_PROVENANCE_SECRET", "never-pass-server-secret")
    monkeypatch.setenv("HF_TOKEN", "never-pass-hub-token")
    monkeypatch.setenv("HTTPS_PROXY", "never-pass-proxy")
    source = tmp_path / "source"
    source.mkdir()
    (source / "infer.py").write_text(STUB)
    (source / "config.json").write_text("{}")
    (source / "model.safetensors").write_bytes(b"fixture checkpoint; never loaded")
    hashes = {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in (
            "infer.py",
            "config.json",
        )
    }
    monkeypatch.setattr(sheetsage2, "REQUIRED_SOURCE_FILES", set(hashes))
    monkeypatch.setattr(sheetsage2, "SHEETSAGE2_ENTRYPOINT_SHA256", hashes["infer.py"])
    config = sheetsage2.SheetSage2Config(
        python=os.path.abspath(sys.executable),
        source_root=source,
        source_revision="test-fixture",
        source_hashes=hashes,
        timeout_seconds=3,
    )
    result = sheetsage2.SheetSage2Provider(config, tmp_path / "stage", FakeInputs())
    try:
        yield result
    finally:
        await result.close()


def request(**parameters):
    return GenerationRequest(
        "music.transcribe",
        "a" * 32,
        TranscriptionRecipe(parameters={"audio": "c" * 32, **parameters}),
    )


async def terminal(provider, execution_id):
    async with asyncio.timeout(6):
        while True:
            result = await provider.inspect(execution_id)
            if result.status in {"completed", "failed", "cancelled", "unknown"}:
                return result
            await asyncio.sleep(0.01)


async def test_real_cpu_fixture_multiple_assets_safe_receipt_and_integrity(provider):
    health = await provider.health()
    assert health.available and health.details["runtime_verified"] is False
    accepted = await provider.submit(request(), "b" * 32)
    assert accepted.managed_inputs["audio"]["sha256"] == hashlib.sha256(wav_bytes()).hexdigest()
    # The independently copied snapshot survives release of the original input lease.
    assert not provider.managed_inputs.in_use
    provider.managed_inputs.content = b"source no longer available"
    result = await terminal(provider, accepted.execution_id)
    assert result.status == "completed"
    assert [out.output_id for out in result.outputs] == [
        "midi",
        "events",
        "score",
        "summary",
        "part-1",
        "annotations",
    ]
    midi = await provider.materialize(accepted.execution_id, "midi")
    assert sheetsage2.validate_midi(midi, require_notes=True)["notes"] == 1
    for output_id in ("summary", "events", "annotations"):
        data = await provider.materialize(accepted.execution_id, output_id)
        assert b"private-provider-path" not in data and b"/private/operator/path" not in data
        json.loads(data)
    assert not any(out.filename == "undeclared-private.json" for out in result.outputs)
    assert result.outputs[-2].role.index == 1
    path = provider.stage_root / accepted.execution_id / "outputs" / "transcription.mid"
    path.write_bytes(midi[:-1])
    with pytest.raises(ProviderError):
        await provider.materialize(accepted.execution_id, "midi")


async def test_abc_failure_still_returns_valid_midi_and_bounded_warning(provider):
    provider.managed_inputs.mode = 8
    accepted = await provider.submit(request(), "b" * 32)
    result = await terminal(provider, accepted.execution_id)
    assert result.status == "completed"
    assert result.metadata["transcription"]["warnings"] == ["abc_unavailable"]
    assert not any(out.output_id == "score" for out in result.outputs)
    assert "private-provider-path" not in str(result.as_dict())


async def test_requested_melody_score_missing_fails(provider):
    provider.managed_inputs.mode = 8
    accepted = await provider.submit(request(melody_only=True), "b" * 32)
    assert (await terminal(provider, accepted.execution_id)).status == "failed"


@pytest.mark.parametrize("mode", (2, 4, 5, 7, 10, 11, 12))
async def test_failure_invalid_output_and_bounded_private_errors(provider, mode):
    provider.managed_inputs.mode = mode
    accepted = await provider.submit(request(), "b" * 32)
    result = await terminal(provider, accepted.execution_id)
    assert result.status == "failed"
    assert "private-provider-path" not in str(result.as_dict())
    with pytest.raises(ProviderError):
        await provider.materialize(accepted.execution_id, "midi")


async def test_output_budget_kills_owned_work(provider):
    provider.config = provider.config.model_copy(update={"output_max_bytes": 1024})
    provider.managed_inputs.mode = 6
    accepted = await provider.submit(request(), "b" * 32)
    result = await terminal(provider, accepted.execution_id)
    assert result.status == "failed" and result.error["code"] == "output_limit"
    assert provider._settled(provider._runs[accepted.execution_id])


async def test_owned_cancel_and_no_foreign_pid_or_retry(provider):
    provider.managed_inputs.mode = 3
    accepted = await provider.submit(request(), "b" * 32)
    result = await provider.cancel(accepted.execution_id)
    assert result.status == "cancelled" and provider._settled(provider._runs[accepted.execution_id])
    assert (await provider.cancel("f" * 32)).status == "unknown"
    with pytest.raises(ProviderError):
        await provider.cancel("1234; kill all")


async def test_term_resistant_process_requires_group_kill(provider):
    provider.managed_inputs.mode = 9
    accepted = await provider.submit(request(), "b" * 32)
    run = provider._runs[accepted.execution_id]
    async with asyncio.timeout(2):
        while run.log_bytes == 0:
            await asyncio.sleep(0.01)
    assert (await provider.cancel(accepted.execution_id)).status == "cancelled"
    assert run.process.returncode == -9 and provider._settled(run)


async def test_unconfirmed_group_keeps_unknown(provider, monkeypatch):
    provider.managed_inputs.mode = 3
    accepted = await provider.submit(request(), "b" * 32)
    monkeypatch.setattr(provider, "_wait_settled", lambda *_: asyncio.sleep(0, result=False))
    assert (await provider.cancel(accepted.execution_id)).status == "unknown"
    await asyncio.sleep(0.05)


async def test_restart_never_inherits_live_process_ownership(provider):
    provider.managed_inputs.mode = 3
    accepted = await provider.submit(request(), "b" * 32)
    restarted = sheetsage2.SheetSage2Provider(provider.config, provider.stage_root, FakeInputs())
    assert (await restarted.inspect(accepted.execution_id)).status == "unknown"
    assert (await restarted.cancel(accepted.execution_id)).status == "unknown"
    with pytest.raises(ProviderError):
        await restarted.materialize(accepted.execution_id, "midi")
    await restarted.close()
    assert provider._runs[accepted.execution_id].process.returncode is None


async def test_source_hash_change_or_unpinned_code_unavailable(provider):
    (provider.config.source_root / "config.json").write_text('{"changed":true}')
    assert not (await provider.health()).available
    with pytest.raises(SubmissionRejected):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs


async def test_extra_unpinned_source_and_source_overflow_are_rejected(provider):
    extra = provider.config.source_root / "injected.py"
    extra.write_text("raise RuntimeError('must not execute')")
    assert not (await provider.health()).available
    extra.unlink()
    for index in range(128):
        (provider.config.source_root / f"unrelated-{index}").touch()
    assert not (await provider.health()).available


@pytest.mark.parametrize("data", (b"RIFF\0\0\0\0WAVE", wav_bytes()[:-1]))
async def test_invalid_input_rejected_before_process(provider, data):
    provider.managed_inputs.content = data
    with pytest.raises(SubmissionRejected):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs and not provider.managed_inputs.in_use


async def test_expired_input_rejected_before_process(provider):
    provider.managed_inputs.fail = True
    with pytest.raises(SubmissionRejected):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs


async def test_spawn_definite_failure_is_rejected(provider, monkeypatch):
    async def fail(*_, **__):
        raise FileNotFoundError("private operator path")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    with pytest.raises(SubmissionRejected):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs


async def test_lost_spawn_acknowledgement_cleans_late_owned_child(provider, monkeypatch):
    original = asyncio.create_subprocess_exec
    release = asyncio.Event()
    provider.managed_inputs.mode = 3

    async def delayed(*args, **kwargs):
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    monkeypatch.setattr(sheetsage2, "SPAWN_WAIT_SECONDS", 0.01)
    with pytest.raises(SubmissionUnknown):
        await provider.submit(request(), "b" * 32)
    run = next(iter(provider._runs.values()))
    assert run.snapshot.status == "unknown" and run.process is None
    release.set()
    async with asyncio.timeout(3):
        while run.worker is None:
            await asyncio.sleep(0.01)
        await run.worker
    assert provider._settled(run)


async def test_cancel_during_final_drain_cannot_become_completed(provider, monkeypatch):
    original = provider._drain
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def drain(run, reader):
        nonlocal count
        await original(run, reader)
        count += 1
        if count == 2:
            entered.set()
        await release.wait()

    monkeypatch.setattr(provider, "_drain", drain)
    accepted = await provider.submit(request(), "b" * 32)
    await entered.wait()
    run = provider._runs[accepted.execution_id]
    await run.process.wait()
    assert (await provider.cancel(accepted.execution_id)).status == "cancelled"
    release.set()
    await run.worker
    assert (await provider.inspect(accepted.execution_id)).status == "cancelled"


def test_profile_and_strict_managed_ref_parameters():
    descriptor = transcription_descriptor()
    assert descriptor["parameters"]["audio"]["type"] == "managed_input"
    assert descriptor["readiness"]["status"] == "not-attested"
    assert descriptor["transcription"]["device"] == "cpu"
    for parameters in (
        {"audio": "/private/input.wav"},
        {"audio": "c" * 32, "max_seconds": 121},
        {"audio": "c" * 32, "max_seconds": float("nan")},
        {"audio": "c" * 32, "melody_only": "false"},
        {"audio": "c" * 32, "device": "cuda"},
    ):
        with pytest.raises(ValidationError):
            TranscriptionRecipe(parameters=parameters)


def test_source_manifest_has_no_caller_selectable_path_traversal():
    for name in ("../infer.py", "/tmp/infer.py", "source/name.py", "python.exe"):
        with pytest.raises(ValidationError):
            sheetsage2.SheetSage2Config(
                python="/usr/bin/python",
                source_root="/models/transcription",
                source_revision="fixture",
                source_hashes={name: "a" * 64},
            )


def test_midi_domain_and_wav_validation():
    assert sheetsage2.validate_midi(MIDI, require_notes=True)["ticks_per_beat"] == 960
    assert sheetsage2.validate_input_wav(wav_bytes()) == 1.0
    for data in (MIDI[:-1], MIDI + b"x", MIDI[:4] + b"\0" * 4 + MIDI[8:]):
        with pytest.raises(ValueError):
            sheetsage2.validate_midi(data)
    empty_track = b"\0\xff\x2f\0"
    empty = MIDI[:14] + b"MTrk" + struct.pack(">I", len(empty_track)) + empty_track
    assert sheetsage2.validate_midi(empty)["notes"] == 0
    with pytest.raises(ValueError):
        sheetsage2.validate_midi(empty, require_notes=True)
    with pytest.raises(ValueError):
        sheetsage2._json(b'{"events":[],"events":[]}')
    with pytest.raises(ValueError):
        sheetsage2._json(b'{"time":NaN}')


@pytest.mark.parametrize(
    "meta",
    (
        b"\0\xff\x51\x02\x07\xa1",
        b"\0\xff\x51\x03\0\0\0",
        b"\0\xff\x58\x03\x04\x02\x18",
        b"\0\xff\x59\x02\x08\0",
    ),
)
def test_midi_fixed_metadata_is_validated(meta):
    track = meta + b"\0\xff\x2f\0"
    data = MIDI[:14] + b"MTrk" + struct.pack(">I", len(track)) + track
    with pytest.raises(ValueError):
        sheetsage2.validate_midi(data)


class WavSource:
    provider_id = "irodori"

    def __init__(self, mode=1):
        self.mode = mode

    async def health(self):
        from flamoris_generation_controller.providers.base import ProviderHealth

        return ProviderHealth(True)

    async def submit(self, request, job_id):
        from flamoris_generation_controller.providers.base import ProviderJob

        return ProviderJob("e" * 32)

    async def inspect(self, execution_id):
        from flamoris_generation_controller.providers.base import (
            JobSnapshot,
            OutputRole,
            ProviderOutput,
        )

        return JobSnapshot(
            status="completed",
            outputs=(
                ProviderOutput(
                    "audio", "audio.wav", "audio", "audio/wav", OutputRole("audio", "audio")
                ),
            ),
        )

    async def cancel(self, execution_id):
        return await self.inspect(execution_id)

    async def materialize(self, execution_id, output_id):
        return wav_bytes(self.mode)

    async def stream_output(self, execution_id, output_id):
        yield await self.materialize(execution_id, output_id)

    async def close(self):
        pass


async def native_context(provider, tmp_path, mode=1):
    from flamoris_generation_controller.capabilities import Capability, CapabilityRegistry
    from flamoris_generation_controller.config import Settings
    from flamoris_generation_controller.inputs import ManagedInputs
    from flamoris_generation_controller.jobs import JobStore
    from flamoris_generation_controller.models import ModelCatalog
    from flamoris_generation_controller.providers import ProviderRegistry
    from flamoris_generation_controller.transfers import AssetTransfers
    from flamoris_generation_controller.workflows import WorkflowStore

    workflows = WorkflowStore(
        ModelCatalog(Settings(model_root=tmp_path)),
        tmp_path / "recipes",
        speech_enabled=True,
        transcription_enabled=True,
    )
    capabilities = CapabilityRegistry(
        (
            Capability("speech.generate", "irodori", "speech-fixture", ("speech-no-reference",)),
            Capability(
                "music.transcribe", "sheetsage2", "sheetsage-fixture", ("music-transcribe",)
            ),
        )
    )
    providers = ProviderRegistry((WavSource(mode), provider))
    jobs = JobStore(workflows, providers, capabilities, tmp_path / "outputs")
    transfers = AssetTransfers(jobs)
    inputs = ManagedInputs(transfers, tmp_path / "inputs")
    provider.managed_inputs = inputs
    source = workflows.build("speech-no-reference", {"text": "fixture source"})
    source_job = await jobs.submit(source["workflow_id"])
    source_asset = (await jobs.list_assets(source_job["job_id"]))["assets"][0]
    reference = await inputs.create(source_asset["asset_id"])
    recipe = workflows.build("music-transcribe", {"audio": reference["input_id"]})
    return jobs, transfers, inputs, recipe, reference


async def test_real_managed_wav_shared_job_assets_and_archived_restart(provider, tmp_path):
    import base64

    from flamoris_generation_controller.jobs import JobStore

    jobs, transfers, inputs, recipe, reference = await native_context(provider, tmp_path)
    submitted = await jobs.submit(recipe["workflow_id"])
    assert submitted["managed_inputs"]["audio"]["source_asset_id"] == reference["source_asset_id"]
    # Independent copy no longer needs original fd; deletion cannot change inference input.
    inputs.delete(reference["input_id"])
    async with asyncio.timeout(5):
        while (await jobs.status(submitted["job_id"]))["status"] not in {"completed", "failed"}:
            await asyncio.sleep(0.01)
    result = await jobs.result(submitted["job_id"])
    assert result["status"] == "completed" and jobs.activity()["active_job_id"] is None
    assets = (await jobs.list_assets(submitted["job_id"]))["assets"]
    assert {asset["role"] for asset in assets} == {
        "midi",
        "score",
        "summary",
        "events",
        "parts",
        "annotations",
    }
    for asset in assets:
        receipt = await transfers.prepare(asset["asset_id"])
        chunk = await transfers.read(asset["asset_id"], receipt["sha256"], 0)
        assert (
            hashlib.sha256(base64.b64decode(chunk["data_base64"])).hexdigest() == receipt["sha256"]
        )
    restarted = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    archived = await restarted.status(submitted["job_id"])
    assert archived["status"] == "completed"
    assert archived["managed_inputs"] == submitted["managed_inputs"]
    assert len((await restarted.list_assets(submitted["job_id"]))["assets"]) == len(assets)


async def test_real_active_recovery_remains_unknown_and_exclusive(provider, tmp_path):
    from flamoris_generation_controller.jobs import GenerationBusyError, JobStore
    from flamoris_generation_controller.providers import ProviderRegistry

    jobs, _, inputs, recipe, _ = await native_context(provider, tmp_path, mode=3)
    accepted = await jobs.submit(recipe["workflow_id"])
    fresh = sheetsage2.SheetSage2Provider(provider.config, provider.stage_root, inputs)
    recovered = JobStore(
        jobs.workflows, ProviderRegistry((WavSource(), fresh)), jobs.capabilities, jobs.output_dir
    )
    assert (await recovered.status(accepted["job_id"]))["status"] == "unknown"
    with pytest.raises(GenerationBusyError):
        await recovered.submit(recipe["workflow_id"])
    assert (await recovered.cancel(accepted["job_id"]))["status"] == "unknown"
    assert recovered.activity()["active_job_id"] == accepted["job_id"]
    assert (await jobs.cancel(accepted["job_id"]))["status"] == "cancelled"
    await fresh.close()
