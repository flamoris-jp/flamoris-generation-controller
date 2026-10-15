"""Bounded adapter for the reviewed resident Irodori HTTP speech contract."""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from uuid import uuid4

import httpx

from ..asset_files import AssetFiles
from ..reference_audio import REFERENCE_CHECKPOINTS, validate_reference_audio
from ..speech import SPEECH_CAPABILITY, ReferenceSpeechRecipe, SpeechRecipe, speech_profile
from .base import (
    GenerationRequest,
    JobSnapshot,
    OutputRole,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    SubmissionRejected,
)
from .irodori import MAX_RUNS, MAX_WAV_BYTES, validate_wav


@dataclass
class _Run:
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="running"))
    worker: asyncio.Task | None = None
    digest: str | None = None


class IrodoriHTTPProvider:
    provider_id = "irodori"

    def __init__(self, settings, stage_root: Path, *, transport=None):
        self.stage_root = stage_root.absolute()
        self.model = settings.irodori_model
        self.timeout = settings.irodori_timeout
        self.client = httpx.AsyncClient(
            base_url=str(settings.irodori_url),
            headers={"Authorization": "Bearer " + settings.irodori_token}
            if settings.irodori_token
            else {},
            timeout=httpx.Timeout(self.timeout, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._runs: dict[str, _Run] = {}
        self._closed = False
        self.managed_inputs = None

    async def health(self):
        available = False
        reference = False
        try:
            async with asyncio.timeout(5), self.client.stream("GET", "/health") as response:
                if response.status_code == 200:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 65536:
                            raise ValueError("Health response too large")
                    raw = json.loads(data)
                    available = (
                        type(raw) is dict
                        and raw.get("status") == "ok"
                        and type(raw.get("model")) is dict
                        and raw["model"].get("id") == self.model
                        and type(raw.get("runtime")) is dict
                        and raw["runtime"].get("loaded") is True
                        and not self._closed
                    )
                    checkpoint = raw["model"].get("hf_checkpoint")
                    reference = (
                        available and isinstance(checkpoint, str)
                        and checkpoint in REFERENCE_CHECKPOINTS
                    )
        except (httpx.HTTPError, TimeoutError, ValueError):
            pass
        return ProviderHealth(
            available,
            {
                "qualification": "configured-http-contract",
                "runtime_verified": False,
                "reference_audio": reference,
            },
        )

    async def submit(self, request: GenerationRequest, job_id):
        if (
            not isinstance(request.payload, SpeechRecipe)
            or request.operation != SPEECH_CAPABILITY
            or not re.fullmatch(r"[0-9a-f]{32}", job_id)
        ):
            raise SubmissionRejected("Invalid native speech request")
        health = await self.health()
        reference = isinstance(request.payload, ReferenceSpeechRecipe)
        if reference and (
            not health.details.get("reference_audio") or self.managed_inputs is None
        ):
            raise SubmissionRejected("The configured model cannot accept reference speech")
        if not health.available or len(self._runs) >= MAX_RUNS:
            raise SubmissionRejected("Resident speech runtime is unavailable")
        reference_data = None
        references = {}
        if reference:
            try:
                async with self.managed_inputs.stage(
                    job_id,
                    {"reference_audio": request.payload.parameters.reference_audio},
                    {"reference_audio": {"audio/wav"}},
                ) as readers:
                    reader = readers["reference_audio"]
                    if reader.metadata["size_bytes"] > MAX_WAV_BYTES:
                        raise ValueError("Reference WAV is too large")
                    data = bytearray()
                    async for chunk in reader.chunks():
                        if len(data) + len(chunk) > MAX_WAV_BYTES:
                            raise ValueError("Reference WAV is too large")
                        data.extend(chunk)
                    reference_data = bytes(data)
                    validate_reference_audio(reference_data)
                    references = {"reference_audio": reader.metadata}
            except (ValueError, OSError, TimeoutError) as exc:
                raise SubmissionRejected("Reference WAV is unavailable or invalid") from exc
        try:
            if (
                self.stage_root.exists()
                and len(list(islice(self.stage_root.iterdir(), MAX_RUNS))) >= MAX_RUNS
            ):
                raise ValueError("Speech staging capacity exhausted")
            execution_id = uuid4().hex
            with AssetFiles(self.stage_root, execution_id):
                pass
        except (OSError, ValueError) as exc:
            raise SubmissionRejected("Speech output storage is unavailable") from exc
        run = _Run()
        self._runs[execution_id] = run
        # JobStore journals before calling us. A single owned task sends one POST;
        # receipt loss never permits a retry or an inferred cancellation.
        run.worker = asyncio.create_task(
            self._execute(execution_id, run, request.payload, reference_data)
        )
        return ProviderJob(execution_id, managed_inputs=references)

    async def _execute(self, execution_id, run, recipe, reference_data=None):
        p = recipe.parameters
        payload = {
            "model": self.model,
            "input": p.text,
            "response_format": "wav",
            "speed": 1.0,
            "irodori": {
                "no_ref": True,
                "caption": p.caption or None,
                "seconds": p.seconds,
                "num_steps": p.steps,
                "seed": p.seed,
                "num_candidates": 1,
                "decode_mode": "sequential",
                "chunking_enabled": False,
                "min_seconds": 0.5,
                "max_seconds": 30,
                "max_text_len": 256,
                "max_caption_len": 256,
            },
        }
        settled = False
        speech_started = False
        voice_id = "flamoris_" + execution_id if reference_data is not None else None
        uploaded = False
        try:
            if voice_id:
                # Persist the private remote identity before the upload effect.
                # Unknown uploads/synthesis retain it for explicit reconciliation.
                with AssetFiles(self.stage_root, execution_id, create=False) as files:
                    files.write(
                        "reference.json",
                        json.dumps({
                            "voice_id": voice_id,
                            "size_bytes": len(reference_data),
                            "sha256": hashlib.sha256(reference_data).hexdigest(),
                        }).encode(),
                        replace=False,
                    )
                await self._upload_reference(voice_id, reference_data)
                uploaded = True
                payload["voice"] = voice_id
                payload["irodori"]["no_ref"] = False
                payload["irodori"]["max_ref_seconds"] = 30
            speech_started = True
            async with (
                asyncio.timeout(self.timeout),
                self.client.stream(
                    "POST",
                    "/v1/audio/speech",
                    json=payload,
                    headers={"Accept": "audio/wav", "Accept-Encoding": "identity"},
                ) as response,
            ):
                # Reviewed upstream rejects these before synthesis. Other errors,
                # redirects and lost/partial responses cannot prove settlement.
                if response.status_code in {400, 401, 404, 422}:
                    settled = True
                    run.snapshot = JobSnapshot(status="failed", error={"code": "speech_rejected"})
                    return
                if response.status_code != 200:
                    raise ValueError("Unconfirmed speech response")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("Encoded speech response")
                length = response.headers.get("content-length")
                if length is not None and (not length.isdecimal() or int(length) > MAX_WAV_BYTES):
                    raise ValueError("Unbounded speech response")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_WAV_BYTES:
                        raise ValueError("Speech response exceeded its bound")
                    data.extend(chunk)
                if length is not None and len(data) != int(length):
                    raise ValueError("Incomplete speech response")
                settled = True
                if response.headers.get("content-type", "").split(";", 1)[0] != "audio/wav":
                    raise ValueError("Unsupported speech media")
            data = bytes(data)
            metadata = validate_wav(data)
            with AssetFiles(self.stage_root, execution_id, create=False) as files:
                files.write("speech.wav", data, replace=False)
            run.digest = hashlib.sha256(data).hexdigest()
            run.snapshot = JobSnapshot(
                status="completed",
                outputs=(
                    ProviderOutput(
                        "speech.wav",
                        "speech.wav",
                        "audio",
                        "audio/wav",
                        OutputRole("audio", "audio"),
                    ),
                ),
                metadata={
                    "audio": metadata,
                    **({"speech": speech_profile(True, True)} if voice_id else {}),
                },
            )
        except asyncio.CancelledError:
            run.snapshot = JobSnapshot(status="unknown", error={"code": "speech_outcome_unknown"})
            raise
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            run.snapshot = JobSnapshot(
                status="failed" if settled or not speech_started else "unknown",
                error={
                    "code": "invalid_speech_output" if settled
                    else "reference_upload_failed" if not speech_started
                    else "speech_outcome_unknown"
                },
            )

        finally:
            # A disconnect does not prove inference stopped using the remote voice.
            if uploaded and (settled or not speech_started):
                if await self._delete_reference(voice_id):
                    try:
                        with AssetFiles(self.stage_root, execution_id, create=False) as files:
                            files.delete("reference.json")
                    except (ValueError, OSError):
                        pass  # Retain the bounded receipt if local cleanup is uncertain.

    async def _upload_reference(self, voice_id, data):
        async with asyncio.timeout(30), self.client.stream(
            "POST", "/v1/audio/voices", data={"voice_id": voice_id},
            files={"file": ("reference.wav", data, "audio/wav")},
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 201:
                raise ValueError("Reference upload was not accepted")
            raw = await self._bounded_json(response)
            if (
                type(raw) is not dict or raw.get("id") != voice_id
                or raw.get("object") != "voice_file"
                or type(raw.get("bytes")) is not int or raw["bytes"] != len(data)
                or raw.get("filename") != voice_id + ".wav"
            ):
                raise ValueError("Invalid reference upload receipt")

    async def _delete_reference(self, voice_id):
        try:
            async with asyncio.timeout(10), self.client.stream(
                "DELETE", "/v1/audio/voices/" + voice_id,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                if response.status_code == 404:
                    return True
                if response.status_code != 200:
                    return False
                raw = await self._bounded_json(response)
                return (
                    type(raw) is dict and raw.get("id") == voice_id
                    and raw.get("deleted") is True
                )
        except (httpx.HTTPError, OSError, ValueError, TimeoutError):
            return False

    @staticmethod
    async def _bounded_json(response):
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError("Encoded reference receipt")
        data = bytearray()
        async for part in response.aiter_bytes():
            if len(data) + len(part) > 65536:
                raise ValueError("Reference receipt too large")
            data.extend(part)
        return json.loads(data)

    async def inspect(self, execution_id):
        run = self._runs.get(execution_id)
        # The API has no queryable durable request identity. A restart never
        # adopts a staged WAV or treats service health as proof of completion.
        return run.snapshot if run else JobSnapshot(status="unknown")

    async def cancel(self, execution_id):
        run = self._runs.get(execution_id)
        if run and run.snapshot.status in {"running", "cancel_requested"}:
            run.snapshot = JobSnapshot(status="cancel_requested")
        # No upstream targeted cancellation exists. Keep observing the original
        # response; never disconnect it and claim that server inference stopped.
        return await self.inspect(execution_id)

    async def materialize(self, execution_id, output_id):
        run = self._runs.get(execution_id)
        if not run or run.snapshot.status != "completed" or output_id != "speech.wav":
            raise ValueError("Speech output is unavailable")
        with AssetFiles(self.stage_root, execution_id, create=False) as files:
            data = files.read(output_id, MAX_WAV_BYTES)
        if data is None or hashlib.sha256(data).hexdigest() != run.digest:
            raise ValueError("Speech output changed")
        return data

    async def stream_output(self, execution_id, output_id):
        from ..transfers import CHUNK_BYTES

        data = await self.materialize(execution_id, output_id)
        for offset in range(0, len(data), CHUNK_BYTES):
            yield data[offset : offset + CHUNK_BYTES]
            await asyncio.sleep(0)

    async def close(self):
        self._closed = True
        workers = [
            run.worker for run in self._runs.values() if run.worker and not run.worker.done()
        ]
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await self.client.aclose()
