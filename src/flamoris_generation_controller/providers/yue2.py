"""Pinned, opt-in YuE2 HTTP generation; never owns GPU runtime activation."""

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..asset_files import AssetFiles
from ..music import (
    MUSIC_CAPABILITY,
    MUSIC_TEMPLATE,
    YUE2_SOURCE_REVISION,
    MusicRecipe,
    music_profile,
)
from .base import (
    GenerationRequest,
    JobSnapshot,
    OutputRole,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    SubmissionRejected,
    SubmissionUnknown,
)

MAX_RUNS = 64
MAX_RESULT_BYTES = 25 * 1024 * 1024
MAX_REPLAY_BYTES = 256 * 1024
MAX_SCORE_BYTES = 128 * 1024
MULTIPART_MIME = "multipart/mixed; boundary=yue2-batch-boundary"
JSON_HEAD = b"--yue2-batch-boundary\r\nContent-Type: application/json\r\n\r\n"
AUDIO_HEAD = b"\r\n--yue2-batch-boundary\r\nContent-Type: audio/wav\r\n\r\n"
END = b"\r\n--yue2-batch-boundary--\r\n"


class Yue2Config(BaseModel):
    """Operator-owned fixed server/model identity; never accepted from a tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    url: str = Field(max_length=2048, strict=True)
    source_revision: str
    model_revision: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    timeout_seconds: float = Field(default=30, ge=1, le=300, allow_inf_nan=False)
    execution_timeout_seconds: float = Field(default=1800, ge=1, le=3600, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def server_url(cls, value):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("Invalid YuE2 server URL") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or "\\" in value
            or "%" in parsed.netloc
        ):
            raise ValueError("Invalid YuE2 server URL")
        return value.rstrip("/")

    @field_validator("source_revision")
    @classmethod
    def pinned_revision(cls, value):
        if value != YUE2_SOURCE_REVISION:
            raise ValueError("Unsupported YuE2 source contract")
        return value

    @classmethod
    def read(cls, path):
        descriptor = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                raise ValueError("Invalid YuE2 configuration file")
            raw = os.read(descriptor, 16385)
            if len(raw) != info.st_size:
                raise ValueError("YuE2 configuration changed")
            return cls.model_validate_json(raw)
        finally:
            os.close(descriptor)


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate YuE2 JSON field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Invalid YuE2 JSON number")

    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


def _float32(value):
    # The deployed request parser stores duration as C++ float, and its JSON
    # writer emits the shortest decimal that rounds back to that same float.
    return struct.unpack("<f", struct.pack("<f", value))[0]


def validate_music_wav(data, seconds):
    """Only the reviewed single-track stereo/48kHz PCM16 domain is published."""
    if (
        not 44 <= len(data) <= MAX_RESULT_BYTES
        or data[:4] != b"RIFF"
        or data[8:12] != b"WAVE"
        or struct.unpack_from("<I", data, 4)[0] + 8 != len(data)
    ):
        raise ValueError("Invalid music WAV")
    offset, fmt, audio, chunks = 12, None, None, 0
    while offset < len(data):
        if offset + 8 > len(data) or chunks >= 16:
            raise ValueError("Invalid music WAV chunks")
        kind = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        if end > len(data):
            raise ValueError("Invalid music WAV chunk length")
        if kind == b"fmt ":
            if fmt is not None or size != 16:
                raise ValueError("Invalid music WAV format")
            fmt = struct.unpack_from("<HHIIHH", data, start)
        elif kind == b"data":
            if audio is not None:
                raise ValueError("Duplicate music WAV audio")
            audio = size
        else:
            raise ValueError("Unsupported music WAV chunk")
        chunks += 1
        offset = end + (size & 1)
    if offset != len(data) or fmt != (1, 2, 48000, 192000, 4, 16):
        raise ValueError("Unsupported music WAV domain")
    if audio is None or audio % 4 or not 1 <= audio // 4 <= math.ceil((seconds + 1) * 48000):
        raise ValueError("Invalid music WAV duration")
    return {"sample_rate": 48000, "channels": 2, "frames": audio // 4}


def _result(data, recipe):
    """Parse exactly one literal multipart pair emitted by the pinned server."""
    if not data.startswith(JSON_HEAD) or not data.endswith(END):
        raise ValueError("Invalid YuE2 multipart result")
    split = data.find(AUDIO_HEAD, len(JSON_HEAD))
    if split < 0 or split - len(JSON_HEAD) > MAX_REPLAY_BYTES:
        raise ValueError("Invalid YuE2 replay part")
    replay = _json(data[len(JSON_HEAD) : split])
    if type(replay) is not dict or set(replay) - {
        "style",
        "lyrics",
        "abc",
        "cot",
        "duration",
        "lm_seed",
        "seed",
        "steps",
        "lm_batch_size",
        "synth_batch_size",
        "semantic_tokens",
        "output_format",
    }:
        raise ValueError("Unsupported YuE2 replay fields")
    p = recipe.parameters
    expected = {
        "style": p.style,
        "lyrics": p.lyrics,
        "cot": "full",
        "duration": _float32(p.seconds),
        "lm_seed": p.lm_seed,
        "seed": p.seed,
        "steps": p.steps,
        "lm_batch_size": 1,
        "synth_batch_size": 1,
        "output_format": "wav16",
    }
    defaults = {
        "lyrics": "",
        "cot": "full",
        "duration": 360,
        "steps": 32,
        "lm_batch_size": 1,
        "synth_batch_size": 1,
    }
    for key, value in expected.items():
        actual = replay.get(key, defaults.get(key))
        if key == "duration":
            if type(actual) not in (int, float) or _float32(actual) != value:
                raise ValueError("YuE2 replay does not match submitted recipe")
            continue
        if (
            type(actual) is bool
            or not isinstance(actual, (str, int, float))
            or actual != value
            or (type(value) is int and type(actual) is not int)
        ):
            raise ValueError("YuE2 replay does not match submitted recipe")
    score = replay.get("abc")
    if (
        type(score) is not str
        or not score.strip()
        or len(score.encode("utf-8")) > MAX_SCORE_BYTES
        or not re.search(r"(?m)^K:[^\r\n]+", score)
        or any(ord(c) < 32 and c not in "\n\r\t" for c in score)
        or re.search(r"(?im)^\s*%%(?:include|abc-include|write|exec)\b", score)
    ):
        raise ValueError("Invalid YuE2 ABC score")
    tokens = replay.get("semantic_tokens")
    if (
        type(tokens) is not str
        or len(tokens) > 128 * 1024
        or not re.fullmatch(r"[0-9]{1,5}(?:,[0-9]{1,5})*", tokens)
        or tokens.count(",") + 1 > math.ceil(p.seconds * 25) + 1
        or any(int(token) >= 32768 for token in tokens.split(","))
    ):
        raise ValueError("Invalid YuE2 semantic stream")
    wav = data[split + len(AUDIO_HEAD) : -len(END)]
    audio_metadata = validate_music_wav(wav, p.seconds)
    # Output JSON is a validated canonical projection, never provider paths/URLs.
    canonical = {**expected, "abc": score, "semantic_tokens": tokens}
    return (
        wav,
        score.encode("utf-8"),
        json.dumps(canonical, ensure_ascii=False).encode(),
        audio_metadata,
    )


@dataclass
class _Run:
    job_id: str
    recipe: MusicRecipe
    deadline: float
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="running"))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cancel_requested: bool = False
    timed_out: bool = False
    digests: dict[str, str] = field(default_factory=dict)
    deadline_worker: asyncio.Task | None = None


class Yue2Provider:
    provider_id = "yue2"

    def __init__(self, config: Yue2Config, stage_root: Path, *, client=None):
        self.config, self.stage_root = config, stage_root.absolute()
        self.client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={"Accept-Encoding": "identity"},
        )
        self._runs: dict[str, _Run] = {}
        self._closed = False

    async def _request(self, method, route, *, limit=16384, **kwargs):
        async with asyncio.timeout(self.config.timeout_seconds):
            async with self.client.stream(method, self.config.url + route, **kwargs) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Encoded YuE2 response is unsupported")
                length = response.headers.get("content-length")
                if length is not None and (
                    len(length) > 10 or not length.isdigit() or int(length) > limit
                ):
                    raise ValueError("YuE2 response exceeds retrieval limit")
                content = bytearray()
                async for chunk in response.aiter_raw():
                    if len(content) + len(chunk) > limit:
                        raise ValueError("YuE2 response exceeds retrieval limit")
                    content.extend(chunk)
                return (
                    response.status_code,
                    response.headers.get("content-type", ""),
                    bytes(content),
                )

    async def health(self):
        available = False
        if not self._closed:
            try:
                status, mime, content = await self._request("GET", "/health")
                available = (
                    status == 200
                    and mime == "application/json"
                    and _json(content) == {"status": "ok"}
                )
            except Exception:
                pass
        return ProviderHealth(
            available,
            {
                "qualification": "configured-http-contract",
                "runtime_verified": False,
                "music": music_profile(),
                "model_revision": self.config.model_revision,
            },
        )

    async def submit(self, request: GenerationRequest, job_id):
        if (
            request.operation != MUSIC_CAPABILITY
            or not isinstance(request.payload, MusicRecipe)
            or request.payload.template != MUSIC_TEMPLATE
            or type(job_id) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", job_id)
        ):
            raise SubmissionRejected("Invalid YuE2 Music request")
        try:
            if self._closed or len(self._runs) >= MAX_RUNS:
                raise ValueError("YuE2 session is unavailable")
            if self.stage_root.is_symlink():
                raise ValueError("YuE2 staging root must not be a symlink")
            if self.stage_root.exists():
                with os.scandir(self.stage_root) as entries:
                    if (
                        sum(1 for _, _entry in zip(range(MAX_RUNS), entries, strict=False))
                        >= MAX_RUNS
                    ):
                        raise ValueError("YuE2 staging capacity exhausted")
        except (ValueError, OSError) as exc:
            raise SubmissionRejected("YuE2 local staging unavailable") from exc
        # Optional downtime is a definite rejection while no generation POST has
        # been attempted. This read-only check neither loads nor activates models.
        if not (await self.health()).available:
            raise SubmissionRejected("YuE2 HTTP provider is unavailable")
        try:
            with AssetFiles(self.stage_root, job_id):
                pass
        except (ValueError, OSError) as exc:
            raise SubmissionRejected("YuE2 local staging unavailable") from exc
        p = request.payload.parameters
        body = {
            "style": p.style,
            "lyrics": p.lyrics,
            "duration": _float32(p.seconds),
            "steps": p.steps,
            "seed": p.seed,
            "lm_seed": p.lm_seed,
            "cot": "full",
            "lm_batch_size": 1,
            "synth_batch_size": 1,
            "output_format": "wav16",
        }
        try:
            status, mime, raw = await self._request("POST", "/synth", json=body)
            if status == 400:
                raise SubmissionRejected("YuE2 rejected generation parameters")
            if status != 200 or mime != "application/json":
                raise ValueError("Unknown YuE2 submission response")
            data = _json(raw)
            execution_id = data.get("id") if type(data) is dict else None
            if type(execution_id) is not str or not re.fullmatch(r"[0-9a-f]{16}", execution_id):
                raise ValueError("Invalid YuE2 execution ID")
            if execution_id in self._runs:
                raise ValueError("Duplicate YuE2 execution ID")
        except SubmissionRejected:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            # A POST may already have been accepted. No replay or false release.
            raise SubmissionUnknown("YuE2 submission outcome unknown") from None
        self._runs[execution_id] = _Run(
            job_id, request.payload, time.monotonic() + self.config.execution_timeout_seconds
        )
        self._runs[execution_id].deadline_worker = asyncio.create_task(
            self._expire(execution_id, self._runs[execution_id])
        )
        return ProviderJob(execution_id)

    def _remember(self, run, snapshot):
        run.snapshot = snapshot
        worker = run.deadline_worker
        if (
            snapshot.status in {"completed", "failed", "cancelled"}
            and worker is not None
            and worker is not asyncio.current_task()
        ):
            worker.cancel()
        return snapshot

    async def _expire(self, execution_id, run):
        # A disappearing client cannot disable the configured execution deadline.
        # One scoped cancellation asks the remote worker to stop. Its running or
        # missing-history response never proves settlement or releases capacity.
        await asyncio.sleep(max(0, run.deadline - time.monotonic()))
        async with run.lock:
            if run.snapshot.status in {"completed", "failed", "cancelled"}:
                return
            if not run.cancel_requested:
                run.cancel_requested = run.timed_out = True
                self._remember(run, await self._state(run, execution_id, cancel=True))

    def _get(self, execution_id):
        if type(execution_id) is not str or not re.fullmatch(r"[0-9a-f]{16}", execution_id):
            raise ProviderError("Invalid YuE2 execution ID")
        return self._runs.get(execution_id)

    async def _state(self, run, execution_id, *, cancel=False):
        try:
            params = {"id": execution_id, **({"cancel": "1"} if cancel else {})}
            status, mime, data = await self._request(
                "POST" if cancel else "GET", "/job", params=params
            )
            raw = _json(data)
            state = raw.get("status") if type(raw) is dict else None
            if (
                status != 200
                or mime != "application/json"
                or state not in {"running", "done", "failed", "cancelled"}
            ):
                raise ValueError("Invalid YuE2 job response")
        except Exception:
            return JobSnapshot(status="unknown", error={"code": "music_execution_unconfirmed"})
        if state == "running":
            return JobSnapshot(status="cancel_requested" if run.cancel_requested else "running")
        if state == "cancelled":
            return JobSnapshot(
                status="failed" if run.timed_out else "cancelled",
                error={"code": "execution_timeout"} if run.timed_out else None,
            )
        if state == "failed":
            return JobSnapshot(status="failed", error={"code": "music_provider_failed"})
        if run.timed_out:
            return JobSnapshot(status="failed", error={"code": "execution_timeout"})
        try:
            status, mime, body = await self._request(
                "GET", "/job", params={"id": execution_id, "result": "1"}, limit=MAX_RESULT_BYTES
            )
            if status != 200 or mime != MULTIPART_MIME:
                raise ValueError("Invalid YuE2 result response")
            audio, score, replay, audio_metadata = _result(body, run.recipe)
            assets = {"audio.wav": audio, "score.abc": score, "replay.json": replay}
            with AssetFiles(self.stage_root, run.job_id, create=False) as files:
                for name, content in assets.items():
                    files.write(name, content)
                    run.digests[name] = hashlib.sha256(content).hexdigest()
            return JobSnapshot(
                status="completed",
                outputs=(
                    ProviderOutput(
                        "audio", "audio.wav", "audio", "audio/wav", OutputRole("audio", "audio")
                    ),
                    ProviderOutput(
                        "score", "score.abc", "score", "text/vnd.abc", OutputRole("score", "score")
                    ),
                    ProviderOutput(
                        "metadata",
                        "replay.json",
                        "metadata",
                        "application/json",
                        OutputRole("metadata", "metadata"),
                    ),
                ),
                metadata={
                    "audio": audio_metadata,
                    "music": {
                        **music_profile(),
                        "model_revision": self.config.model_revision,
                        "seed": run.recipe.parameters.seed,
                        "lm_seed": run.recipe.parameters.lm_seed,
                    },
                },
            )
        except Exception:
            # The accepted execution is terminal even when its artifacts are invalid.
            return JobSnapshot(status="failed", error={"code": "music_output_invalid"})

    async def inspect(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "music_execution_unowned"})
        async with run.lock:
            if run.snapshot.status in {"completed", "failed", "cancelled"}:
                return run.snapshot
            expired = time.monotonic() > run.deadline and not run.cancel_requested
            if expired:
                run.cancel_requested = run.timed_out = True
            return self._remember(run, await self._state(run, execution_id, cancel=expired))

    async def cancel(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "music_execution_unowned"})
        async with run.lock:
            if run.snapshot.status in {"completed", "failed", "cancelled"}:
                return run.snapshot
            run.cancel_requested = True
            return self._remember(run, await self._state(run, execution_id, cancel=True))

    async def materialize(self, execution_id, output_id):
        run = self._get(execution_id)
        names = {"audio": "audio.wav", "score": "score.abc", "metadata": "replay.json"}
        if run is None or run.snapshot.status != "completed" or output_id not in names:
            raise ProviderError("YuE2 output is unavailable")
        name = names[output_id]
        try:
            with AssetFiles(self.stage_root, run.job_id, create=False) as files:
                data = files.read(
                    name, MAX_RESULT_BYTES if output_id == "audio" else MAX_REPLAY_BYTES
                )
            if data is None or hashlib.sha256(data).hexdigest() != run.digests[name]:
                raise ValueError("YuE2 output changed")
            return data
        except (OSError, ValueError):
            raise ProviderError("YuE2 output is unavailable") from None

    async def stream_output(self, execution_id, output_id):
        from ..transfers import CHUNK_BYTES

        data = await self.materialize(execution_id, output_id)
        for offset in range(0, len(data), CHUNK_BYTES):
            yield data[offset : offset + CHUNK_BYTES]
            await asyncio.sleep(0)

    async def close(self):
        self._closed = True
        workers = [run.deadline_worker for run in self._runs.values() if run.deadline_worker]
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        for execution_id, run in self._runs.items():
            if run.snapshot.status not in {"completed", "failed", "cancelled"}:
                await self.cancel(execution_id)
        await self.client.aclose()
