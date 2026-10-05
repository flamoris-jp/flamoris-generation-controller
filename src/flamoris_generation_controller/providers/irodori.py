"""Opt-in, local-only Irodori CLI adapter with owned process-group settlement."""

import asyncio
import hashlib
import os
import re
import signal
import stat
import struct
import time
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..asset_files import AssetFiles
from ..speech import (
    IRODORI_SOURCE_REVISION,
    SPEECH_CAPABILITY,
    SPEECH_TEMPLATE,
    SpeechRecipe,
    speech_profile,
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

SOURCE_REVISION = IRODORI_SOURCE_REVISION
SOURCE_BLOBS = {
    "infer.py": "c24c64241f648ab9717da4f9caf7f985c2efeef8",
    "irodori_tts/__init__.py": "f1c108f90cbefd944c87356c8b72aed1fa6c680b",
    "irodori_tts/attention.py": "8ba55831a3bd9c243336dd949e2132f0909c43bf",
    "irodori_tts/codec.py": "f1d40676548d7d67ae9c902622e705aa2d9c33ce",
    "irodori_tts/config.py": "2faa973c6107689bd4aa432c8d62f8bcf34b7690",
    "irodori_tts/dataset.py": "0ca3f9d62bb86e056b78185f4d56bca3a2174da0",
    "irodori_tts/duration.py": "28f578983dcefac609a357e70705d05d36c38b4b",
    "irodori_tts/gradio_emoji_palette.py": "33fce37be1e042b05fefe8e2b9ab5ac110b91fda",
    "irodori_tts/inference_runtime.py": "7463e5bb46c5adb840da1e788d2729400ba97840",
    "irodori_tts/lora.py": "181e986f9b1efc4e6aab904ab89fbec5eed80220",
    "irodori_tts/meanflow.py": "fa22a6bcfe975aeb214afe69fb2db1aecf2feac4",
    "irodori_tts/model.py": "2ad8e78d5853001a47b65d29366afc752f25640d",
    "irodori_tts/optim.py": "2f67969e3a851dbbaae82c4225f04bd244ebdb02",
    "irodori_tts/progress.py": "85596a62175a8ef6838f682a8c869dadf7cc18a3",
    "irodori_tts/quantization.py": "901bdd576de494156847e7ccfb1209aa2c215c3d",
    "irodori_tts/rf.py": "7cd27599e7dd572f9da4059a1a3262b5c9da85f6",
    "irodori_tts/speaker_inversion.py": "77ee69d5632738ef2e9f2dae4359f9d567fcfd0c",
    "irodori_tts/text_normalization.py": "bbfc0f175d13315a821e340b56d7dba752a8aa65",
    "irodori_tts/tokenizer.py": "861aa3a51096c19c1931a4ab44e7b8b025a1c5c7",
    "irodori_tts/watermark.py": "d84932b7480ebcb3256261affde273b8368bba87",
}
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_RUNS = 128
SPAWN_WAIT_SECONDS = 5
CHILD_ENV_NAMES = {
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "LD_LIBRARY_PATH",
    "LIBRARY_PATH",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "XDG_CACHE_HOME",
    "VIRTUAL_ENV",
}
CHILD_ENV_PREFIXES = ("LC_", "CUDA_", "HIP_", "HSA_", "ROCM_", "OMP_", "MKL_", "OPENBLAS_", "NCCL_")


class IrodoriConfig(BaseModel):
    """Operator-owned configuration. No field is accepted through an MCP tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    python: Path
    source_root: Path
    checkpoint: Path
    codec: Path
    resource_root: Path | None = None
    source_revision: str
    model_revision: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    model_device: str = Field(default="cuda", pattern=r"^(?:cpu|mps|cuda(?::[0-9]{1,2})?)$")
    codec_device: str = Field(default="cuda", pattern=r"^(?:cpu|mps|cuda(?::[0-9]{1,2})?)$")
    timeout_seconds: float = Field(default=300, ge=1, le=600, allow_inf_nan=False)
    log_max_bytes: int = Field(default=65536, ge=1024, le=1048576, strict=True)

    @classmethod
    def read(cls, path):
        raw = _regular_bytes(Path(path), 16384)
        return cls.model_validate_json(raw)


def _regular_bytes(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid configured local resource")
        chunks = bytearray()
        while len(chunks) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) != info.st_size or len(chunks) > limit:
            raise ValueError("Configured local resource changed")
        return bytes(chunks)
    finally:
        os.close(fd)


def _regular_path(path, *, executable=False):
    if not path.is_absolute() or (path.is_symlink() and not executable):
        raise ValueError("Configured resources require absolute regular paths")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise ValueError("Configured local resource is unavailable")
    if executable and not os.access(path, os.X_OK):
        raise ValueError("Configured Python is not executable")


def _model_resource(path, root):
    """Follow operator-owned cache links only inside an explicitly trusted root."""
    if root is None:
        _regular_path(path)
        return path
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Invalid model resource root")
    resolved_root = root.resolve(strict=True)
    if not path.is_absolute() or not path.is_relative_to(root):
        raise ValueError("Model resource is outside its configured root")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("Model resource link escapes its configured root")
    _regular_path(resolved)
    return resolved


def validate_wav(data):
    """Validate bounded RIFF PCM/float samples, including extensible WAV headers."""
    if not 44 <= len(data) <= MAX_WAV_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Invalid speech WAV")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("Invalid speech WAV length")
    offset, fmt, payload, chunks = 12, None, None, 0
    while offset < len(data):
        if offset + 8 > len(data) or chunks >= 32:
            raise ValueError("Invalid speech WAV chunks")
        name, size = data[offset : offset + 4], struct.unpack_from("<I", data, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        if end > len(data):
            raise ValueError("Invalid speech WAV chunk size")
        if name == b"fmt ":
            if fmt is not None or not 16 <= size <= 64:
                raise ValueError("Invalid speech WAV format")
            fmt = data[start:end]
        elif name == b"data":
            if payload is not None:
                raise ValueError("Duplicate speech WAV data")
            payload = data[start:end]
        offset, chunks = end + (size % 2), chunks + 1
    if offset != len(data) or fmt is None or not payload:
        raise ValueError("Incomplete speech WAV")
    encoding, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if encoding == 0xFFFE:
        if len(fmt) < 40 or struct.unpack_from("<H", fmt, 16)[0] != 22:
            raise ValueError("Invalid extensible speech WAV")
        if fmt[26:40] != b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71":
            raise ValueError("Unsupported speech WAV encoding")
        encoding = struct.unpack_from("<H", fmt, 24)[0]
    if (
        channels != 1
        or not 8000 <= rate <= 96000
        or encoding not in (1, 3)
        or bits not in ((8, 16, 24, 32) if encoding == 1 else (32,))
        or align != channels * bits // 8
        or byte_rate != rate * align
        or len(payload) % align
        or not 1 <= len(payload) // align <= rate * 31
    ):
        raise ValueError("Unsupported speech WAV domain")
    if encoding == 3:
        import math

        if any(not math.isfinite(value[0]) for value in struct.iter_unpack("<f", payload)):
            raise ValueError("Non-finite speech WAV sample")
    return {"sample_rate": rate, "channels": channels, "frames": len(payload) // align}


@dataclass
class _Run:
    execution_id: str
    process: object | None = None
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="unknown"))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    log_bytes: int = 0
    worker: asyncio.Task | None = None
    digest: str | None = None
    spawn: asyncio.Task | None = None
    deadline: float = 0


class IrodoriProvider:
    provider_id = "irodori"

    def __init__(self, config: IrodoriConfig, stage_root: Path):
        self.config, self.stage_root = config, stage_root.absolute()
        self._runs = {}
        self._closed = False

    def _check(self):
        if os.name != "posix" or self.config.source_revision != SOURCE_REVISION:
            raise ValueError("Unsupported Irodori source/platform")
        _regular_path(self.config.python, executable=True)
        for path in (self.config.checkpoint, self.config.codec):
            _model_resource(path, self.config.resource_root)
        root = self.config.source_root
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid Irodori source root")
        for relative, expected in SOURCE_BLOBS.items():
            data = _regular_bytes(root / relative, 256 * 1024)
            blob = b"blob " + str(len(data)).encode() + b"\0" + data
            if hashlib.sha1(blob).hexdigest() != expected:
                raise ValueError("Irodori source contract changed")
        tokenizer_config = _model_resource(
            self.config.checkpoint.parent / "tokenizer" / "tokenizer_config.json",
            self.config.resource_root,
        )
        _regular_bytes(tokenizer_config, 65536)
        # AutoTokenizer may read tokenizer.json, vocabulary and additional local
        # resources. Apply the same boundary to every adjacent tokenizer file.
        tokenizer_root = self.config.checkpoint.parent / "tokenizer"
        entries = list(islice(tokenizer_root.iterdir(), 33))
        if len(entries) > 32:
            raise ValueError("Too many configured tokenizer resources")
        total = 0
        for entry in entries:
            resource = _model_resource(entry, self.config.resource_root)
            total += resource.stat().st_size
            if total > 32 * 1024 * 1024:
                raise ValueError("Configured tokenizer resources exceed their bound")

    async def health(self):
        try:
            self._check()
            available = not self._closed
        except (OSError, ValueError):
            available = False
        return ProviderHealth(
            available,
            {"qualification": "configured-local-resources", "runtime_verified": False},
        )

    def _argv(self, recipe, output):
        p = recipe.parameters
        argv = [
            str(self.config.python),
            "-B",
            str(self.config.source_root / "infer.py"),
            "--checkpoint",
            str(self.config.checkpoint),
            "--codec-repo",
            str(self.config.codec),
            "--text=" + p.text,
            "--no-ref",
            "--output-wav",
            str(output),
            "--seconds",
            str(p.seconds),
            "--num-steps",
            str(p.steps),
            "--seed",
            str(p.seed),
            "--num-candidates",
            "1",
            "--decode-mode",
            "sequential",
            "--max-text-len",
            "256",
            "--max-caption-len",
            "256",
            "--model-device",
            self.config.model_device,
            "--codec-device",
            self.config.codec_device,
            "--no-show-timings",
        ]
        if p.caption:
            argv.append("--caption=" + p.caption)
        return argv

    async def submit(self, request: GenerationRequest, job_id):
        if (
            not isinstance(request.payload, SpeechRecipe)
            or request.operation != SPEECH_CAPABILITY
            or request.payload.template != SPEECH_TEMPLATE
            or not re.fullmatch(r"[0-9a-f]{32}", job_id)
        ):
            raise SubmissionRejected("Invalid native speech request")
        try:
            self._check()
            if self._closed or len(self._runs) >= MAX_RUNS:
                raise ValueError("Native speech session is unavailable")
            if (
                self.stage_root.exists()
                and len(list(islice(self.stage_root.iterdir(), MAX_RUNS))) >= MAX_RUNS
            ):
                raise ValueError("Native speech staging capacity exhausted")
            execution_id = uuid4().hex
            with AssetFiles(self.stage_root, execution_id):
                pass
        except (OSError, ValueError) as exc:
            raise SubmissionRejected("Native speech local resources unavailable") from exc
        run = _Run(execution_id, deadline=time.monotonic() + self.config.timeout_seconds)
        self._runs[execution_id] = run
        env = {
            key: value
            for key, value in os.environ.items()
            if (key in CHILD_ENV_NAMES or key.startswith(CHILD_ENV_PREFIXES))
            and not any(
                word in key.upper()
                for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
            )
        }
        env.update(
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_DATASETS_OFFLINE="1",
            PYTHONNOUSERSITE="1",
            PYTHONPYCACHEPREFIX=str(self.stage_root / execution_id / "unused-pycache"),
        )
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *self._argv(request.payload, self.stage_root / execution_id / "speech.wav"),
                cwd=self.config.source_root,
                env=env,
                start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=8192,
            )
        )
        run.spawn = spawn
        try:
            async with asyncio.timeout(min(SPAWN_WAIT_SECONDS, self.config.timeout_seconds)):
                run.process = await asyncio.shield(spawn)
        except BaseException as exc:
            # Cancellation can lose spawn acknowledgement. Recover only this owned
            # task for bounded cleanup; the shared JobStore keeps its unknown fence.
            def finish_spawn(task):
                try:
                    run.process = task.result()
                    run.cancel_requested = True
                    run.worker = asyncio.create_task(self._watch(run))
                except BaseException:
                    pass

            spawn.add_done_callback(finish_spawn)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise SubmissionUnknown("Native speech spawn outcome unknown") from None
        run.snapshot = JobSnapshot(status="running")
        run.worker = asyncio.create_task(self._watch(run))
        return ProviderJob(execution_id)

    async def _drain(self, run, reader):
        while chunk := await reader.read(4096):
            run.log_bytes += len(chunk)
            if run.log_bytes > self.config.log_max_bytes:
                run.overflow.set()

    def _settled(self, run):
        if run.process is None or run.process.returncode is None:
            return False
        try:
            os.killpg(run.process.pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    async def _wait_settled(self, run, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._settled(run):
                return True
            await asyncio.sleep(0.02)
        return self._settled(run)

    async def _terminate(self, run):
        async with run.lock:
            if self._settled(run):
                return True
            if run.process is None:
                return False
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(run.process.pid, sig)
                except ProcessLookupError:
                    pass
                except OSError:
                    return False
                if await self._wait_settled(run, 2):
                    return True
            return False

    async def _watch(self, run):
        readers = [
            asyncio.create_task(self._drain(run, reader))
            for reader in (run.process.stdout, run.process.stderr)
        ]
        waiter = asyncio.create_task(run.process.wait())
        overflow = asyncio.create_task(run.overflow.wait())
        try:
            done, _ = await asyncio.wait(
                (waiter, overflow),
                timeout=0 if run.cancel_requested else max(0, run.deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            code = "log_limit" if run.overflow.is_set() else "execution_timeout"
            if waiter not in done or not self._settled(run):
                settled = await self._terminate(run)
                run.snapshot = JobSnapshot(
                    status="cancelled"
                    if settled and run.cancel_requested
                    else "failed"
                    if settled
                    else "unknown",
                    error={"code": "cancelled" if run.cancel_requested else code},
                )
            elif run.cancel_requested:
                run.snapshot = JobSnapshot(status="cancelled")
            elif run.process.returncode != 0:
                run.snapshot = JobSnapshot(
                    status="failed", error={"code": "speech_provider_failed"}
                )
            else:
                # Await both bounded drains so late overflow cannot become success.
                await asyncio.gather(*readers)
                if run.cancel_requested:
                    run.snapshot = JobSnapshot(status="cancelled")
                    return
                if run.overflow.is_set():
                    raise ValueError("Speech provider log limit exceeded")
                with AssetFiles(self.stage_root, run.execution_id, create=False) as files:
                    data = files.read("speech.wav", MAX_WAV_BYTES)
                metadata = validate_wav(data or b"")
                run.digest = hashlib.sha256(data).hexdigest()
                run.snapshot = JobSnapshot(
                    status="completed",
                    outputs=(
                        ProviderOutput(
                            "audio",
                            "speech.wav",
                            "audio",
                            "audio/wav",
                            OutputRole("audio", "audio"),
                        ),
                    ),
                    metadata={
                        "audio": metadata,
                        "speech": {
                            **speech_profile(),
                            "model_revision": self.config.model_revision,
                            "output_sha256": run.digest,
                        },
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if run.snapshot.status == "cancelled":
                return
            run.snapshot = JobSnapshot(
                status="failed" if self._settled(run) else "unknown",
                error={"code": "log_limit" if run.overflow.is_set() else "speech_output_invalid"},
            )
        finally:
            for task in (*readers, waiter, overflow):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, waiter, overflow, return_exceptions=True)

    def _get(self, execution_id):
        if type(execution_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
            raise ProviderError("Invalid native speech execution ID")
        return self._runs.get(execution_id)

    async def inspect(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "speech_execution_unowned"})
        if run.snapshot.status == "unknown" and run.cancel_requested and self._settled(run):
            run.snapshot = JobSnapshot(status="cancelled")
        return run.snapshot

    async def cancel(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "speech_execution_unowned"})
        if run.snapshot.status in {"completed", "failed", "cancelled"}:
            return run.snapshot
        run.cancel_requested = True
        settled = await self._terminate(run)
        run.snapshot = JobSnapshot(status="cancelled" if settled else "unknown")
        return run.snapshot

    async def materialize(self, execution_id, output_id):
        run = self._get(execution_id)
        if run is None or run.snapshot.status != "completed" or output_id != "audio":
            raise ProviderError("Native speech output is unavailable")
        try:
            with AssetFiles(self.stage_root, execution_id, create=False) as files:
                data = files.read("speech.wav", MAX_WAV_BYTES)
            validate_wav(data or b"")
            if hashlib.sha256(data).hexdigest() != run.digest:
                raise ValueError("Speech output changed")
            return data
        except (OSError, ValueError) as exc:
            raise ProviderError("Native speech output is unavailable") from exc

    async def close(self):
        self._closed = True
        for run in self._runs.values():
            if run.snapshot.status not in {"completed", "failed", "cancelled"}:
                await self.cancel(run.execution_id)
            if run.worker is not None and not run.worker.done():
                try:
                    async with asyncio.timeout(5):
                        await asyncio.shield(run.worker)
                except TimeoutError:
                    run.worker.cancel()

    async def stream_output(self, execution_id, output_id):
        from ..transfers import CHUNK_BYTES

        # Native output is independently bounded/validated before this immutable
        # snapshot is split into the existing provider-neutral transfer chunks.
        data = await self.materialize(execution_id, output_id)
        for offset in range(0, len(data), CHUNK_BYTES):
            yield data[offset : offset + CHUNK_BYTES]
            await asyncio.sleep(0)
