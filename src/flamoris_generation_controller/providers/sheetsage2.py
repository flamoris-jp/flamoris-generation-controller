"""Opt-in SheetSage2 Python CLI; offline CPU execution and owned cancellation."""

import asyncio
import hashlib
import json
import math
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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..asset_files import AssetFiles
from ..inputs import MAX_INPUT_BYTES
from ..transcription import (
    SHEETSAGE2_ENTRYPOINT_SHA256,
    TRANSCRIPTION_CAPABILITY,
    TRANSCRIPTION_TEMPLATE,
    TranscriptionRecipe,
    transcription_profile,
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
from .irodori import CHILD_ENV_NAMES, _regular_bytes, _regular_path

MAX_RUNS = 128
SPAWN_WAIT_SECONDS = 5
MAX_MIDI_BYTES = 4 * 1024**2
MAX_JSON_BYTES = 8 * 1024**2
MAX_SCORE_BYTES = 2 * 1024**2
SOURCE_LIMIT = 512 * 1024
REQUIRED_SOURCE_FILES = {
    "infer.py",
    "config.json",
    "__init__.py",
    "audio_sheetsage2.py",
    "configuration_mert2.py",
    "configuration_sheetsage2.py",
    "durations_sheetsage2.py",
    "exports_sheetsage2.py",
    "generation_sheetsage2.py",
    "io_sheetsage2.py",
    "labels_sheetsage2.py",
    "midi_sheetsage2.py",
    "modeling_mert2.py",
    "modeling_sheetsage2.py",
    "notation_sheetsage2.py",
    "pipeline_sheetsage2.py",
    "processing_sheetsage2.py",
    "processor_config.json",
    "schema_sheetsage2.py",
    "tensors_sheetsage2.py",
    "tokenization_sheetsage2.py",
}
PART_FILES = ("melody.mid", "melody_vocal.mid", "melody_instrumental.mid", "chords.mid")
LAB_FILES = (
    "key.lab",
    "chord.lab",
    "structure.lab",
    "beat.lab",
    "downbeat.lab",
    "rhythm_events.lab",
    "melody_full.lab",
    "melody_vocal.lab",
    "melody_instrumental.lab",
)
PROMPTS = {
    "timestamp",
    "downbeat_meter",
    "structure",
    "key",
    "chord_majmin",
    "chord_full",
    "melody_vocal",
    "melody_full",
}


class SheetSage2Config(BaseModel):
    """Trusted operator configuration, never caller-supplied model/code paths."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    python: Path
    source_root: Path
    source_revision: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    source_hashes: dict[str, str] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=600, ge=1, le=1800, allow_inf_nan=False)
    log_max_bytes: int = Field(default=65536, ge=1024, le=1048576, strict=True)
    output_max_bytes: int = Field(default=32 * 1024**2, ge=1024, le=64 * 1024**2, strict=True)

    @field_validator("source_hashes")
    @classmethod
    def safe_manifest(cls, value):
        for name, digest in value.items():
            if not re.fullmatch(
                r"(?:[a-z][a-z0-9_]*|__init__)\.(?:py|json)", name
            ) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Source manifest requires bounded local filenames and SHA256")
        return value

    @classmethod
    def read(cls, path):
        return cls.model_validate_json(_regular_bytes(Path(path), 16384))


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate transcription JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Non-finite transcription JSON")

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    budget = [200000]

    def check(value, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 12:
            raise ValueError("Transcription JSON complexity exceeded")
        if isinstance(value, dict):
            for key, child in value.items():
                if len(key) > 128:
                    raise ValueError("Transcription JSON key exceeds limit")
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)
        elif isinstance(value, str):
            if len(value) > 2048 or any(ord(c) < 32 and c not in "\n\t" for c in value):
                raise ValueError("Transcription JSON string exceeds limit")
        elif type(value) in (float, int):
            if not math.isfinite(value) or abs(value) > 2**53 - 1:
                raise ValueError("Transcription JSON number exceeds limit")
        elif value is not None and type(value) is not bool:
            raise ValueError("Invalid transcription JSON value")

    check(result)
    return result


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def validate_midi(data, *, require_notes=False):
    """Bounded SMF parser: track framing, event grammar, running status and EOT."""
    if not 26 <= len(data) <= MAX_MIDI_BYTES or data[:8] != b"MThd\0\0\0\6":
        raise ValueError("Invalid transcription MIDI header")
    fmt, tracks, division = struct.unpack_from(">HHH", data, 8)
    if fmt not in {0, 1} or not 1 <= tracks <= 32 or not 1 <= division < 0x8000:
        raise ValueError("Unsupported transcription MIDI format")
    if fmt == 0 and tracks != 1:
        raise ValueError("Invalid single-track MIDI")
    offset, notes, event_count = 14, 0, 0

    def variable(start, end):
        value = 0
        for _ in range(4):
            if start >= end:
                raise ValueError("Truncated MIDI variable integer")
            byte = data[start]
            start += 1
            value = (value << 7) | (byte & 127)
            if not byte & 128:
                return value, start
        raise ValueError("MIDI variable integer exceeds limit")

    for _ in range(tracks):
        if offset + 8 > len(data) or data[offset : offset + 4] != b"MTrk":
            raise ValueError("Invalid transcription MIDI track")
        end = offset + 8 + struct.unpack_from(">I", data, offset + 4)[0]
        offset += 8
        if end > len(data):
            raise ValueError("Truncated transcription MIDI track")
        running, ended = None, False
        while offset < end:
            _, offset = variable(offset, end)
            if offset >= end:
                raise ValueError("Truncated MIDI event")
            event_count += 1
            if event_count > 250000:
                raise ValueError("MIDI event limit exceeded")
            status = data[offset]
            if status & 128:
                offset += 1
                running = status if status < 0xF0 else None
            elif running is not None:
                status = running
            else:
                raise ValueError("Invalid MIDI running status")
            if 0x80 <= status < 0xF0:
                count = 1 if status & 0xF0 in {0xC0, 0xD0} else 2
                if offset + count > end or any(v > 127 for v in data[offset : offset + count]):
                    raise ValueError("Invalid MIDI channel event")
                if status & 0xF0 == 0x90 and data[offset + 1] > 0:
                    notes += 1
                offset += count
            elif status == 0xFF:
                if offset >= end:
                    raise ValueError("Truncated MIDI meta event")
                meta = data[offset]
                size, offset = variable(offset + 1, end)
                if offset + size > end:
                    raise ValueError("Truncated MIDI meta content")
                expected = {
                    0x00: 2,
                    0x20: 1,
                    0x21: 1,
                    0x2F: 0,
                    0x51: 3,
                    0x54: 5,
                    0x58: 4,
                    0x59: 2,
                }.get(meta)
                if meta > 127 or (expected is not None and size != expected):
                    raise ValueError("Invalid MIDI fixed meta event")
                content = data[offset : offset + size]
                if meta == 0x51 and int.from_bytes(content, "big") == 0:
                    raise ValueError("Invalid MIDI tempo")
                if meta == 0x58 and (not content[0] or content[1] > 7):
                    raise ValueError("Invalid MIDI time signature")
                if meta == 0x59 and (
                    content[0] not in {*range(8), *range(249, 256)} or content[1] not in {0, 1}
                ):
                    raise ValueError("Invalid MIDI key signature")
                if meta == 0x2F:
                    if size != 0 or offset != end:
                        raise ValueError("Invalid MIDI end of track")
                    ended = True
                offset += size
            elif status in {0xF0, 0xF7}:
                size, offset = variable(offset, end)
                if offset + size > end:
                    raise ValueError("Truncated MIDI system exclusive event")
                offset += size
            else:
                raise ValueError("Unsupported MIDI event")
        if not ended:
            raise ValueError("Missing MIDI end of track")
    if offset != len(data) or (require_notes and notes == 0):
        raise ValueError("Incomplete or empty transcription MIDI")
    return {"tracks": tracks, "notes": notes, "ticks_per_beat": division}


def validate_input_wav(data):
    if not 44 <= len(data) <= MAX_INPUT_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Invalid managed transcription WAV")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("Invalid managed WAV length")
    offset, fmt, payload, chunks = 12, None, None, 0
    while offset < len(data):
        if offset + 8 > len(data) or chunks >= 64:
            raise ValueError("Invalid managed WAV chunks")
        name, size = data[offset : offset + 4], struct.unpack_from("<I", data, offset + 4)[0]
        start, end = offset + 8, offset + 8 + size
        if end > len(data):
            raise ValueError("Invalid managed WAV chunk length")
        if name == b"fmt ":
            if fmt is not None or not 16 <= size <= 64:
                raise ValueError("Invalid managed WAV format")
            fmt = data[start:end]
        elif name == b"data":
            if payload is not None:
                raise ValueError("Duplicate managed WAV data")
            payload = memoryview(data)[start:end]
        offset, chunks = end + (size & 1), chunks + 1
    if offset != len(data) or fmt is None or payload is None:
        raise ValueError("Incomplete managed WAV")
    encoding, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if encoding == 0xFFFE:
        if len(fmt) < 40 or fmt[26:40] != bytes.fromhex("000000001000800000aa00389b71"):
            raise ValueError("Unsupported extensible WAV")
        encoding = struct.unpack_from("<H", fmt, 24)[0]
    if (
        encoding not in {1, 3}
        or not 1 <= channels <= 8
        or not 8000 <= rate <= 192000
        or bits not in {8, 16, 24, 32}
        or (encoding == 3 and bits != 32)
        or align != channels * bits // 8
        or byte_rate != rate * align
        or not payload
        or len(payload) % align
    ):
        raise ValueError("Unsupported managed WAV domain")
    seconds = len(payload) / align / rate
    if not 0.1 <= seconds <= 600:
        raise ValueError("Managed WAV duration exceeds transcription limit")
    if encoding == 3 and any(not math.isfinite(v[0]) for v in struct.iter_unpack("<f", payload)):
        raise ValueError("Non-finite managed WAV sample")
    return seconds


def _read(files, name, limit):
    info = files.size(name)
    if info is None:
        return None
    if os.stat(name, dir_fd=files.fd, follow_symlinks=False).st_nlink != 1:
        raise ValueError("Transcription output must not be a hard link")
    return files.read(name, limit)


def _score(data):
    text = data.decode("utf-8")
    if (
        not re.search(r"(?m)^X:\s*\d+\s*$", text)
        or not re.search(r"(?m)^K:\s*\S+", text)
        or any(ord(c) < 32 and c not in "\n\r\t" for c in text)
        or re.search(r"(?im)^\s*%%(?:include|abc-include|write|exec)\b", text)
    ):
        raise ValueError("Invalid transcription ABC")


def _events(events, duration):
    """Keep the reviewed symbolic event namespace; reject extra diagnostic fields."""
    fields = {"timestamp", "rhythm", "structure", "key", "chord", "melody"}
    event_keys = {
        "subbeat",
        "tokens_by_field",
        "values",
        "time",
        "window_index",
        "window_start",
        "source_subbeat",
        "global_subbeat",
    }
    note_keys = {"pitch", "track", "duration_bin", "duration_steps", "end_time"}

    def number(value, maximum):
        if type(value) not in (int, float) or not 0 <= value <= maximum:
            raise ValueError("Invalid transcription musical value")

    def label(value):
        if (
            type(value) is not str
            or not re.fullmatch(r"[A-Za-z0-9#b:()/+_. -]{0,128}", value)
            or value.startswith(("/", "."))
            or ".." in value
        ):
            raise ValueError("Invalid transcription musical label")

    for event in events:
        if not isinstance(event, dict) or not event.keys() <= event_keys:
            raise ValueError("Undeclared transcription event field")
        number(event.get("time"), duration + 0.1)
        for name, value in event.items():
            if name not in {"time", "tokens_by_field", "values"}:
                number(value, 1000000)
        values = event.get("values")
        if not isinstance(values, dict) or not values.keys() <= fields:
            raise ValueError("Undeclared transcription musical field")
        for name, value in values.items():
            if name in {"structure", "key", "chord"}:
                label(value)
            elif name == "timestamp":
                number(value, duration + 0.1)
            elif name == "rhythm":
                if not isinstance(value, dict) or not value.keys() <= {"meter", "eighth_position"}:
                    raise ValueError("Invalid transcription rhythm")
                if "meter" in value:
                    meter = value["meter"]
                    if (
                        not isinstance(meter, list)
                        or len(meter) != 2
                        or any(type(v) is not int or not 1 <= v <= 64 for v in meter)
                    ):
                        raise ValueError("Invalid transcription meter")
                if "eighth_position" in value:
                    number(value["eighth_position"], 128)
            elif name == "melody":
                if not isinstance(value, list) or len(value) > 128:
                    raise ValueError("Invalid transcription melody")
                for note in value:
                    if not isinstance(note, dict) or not note.keys() <= note_keys:
                        raise ValueError("Invalid transcription note")
                    for key, item in note.items():
                        number(item, duration + 0.1 if key == "end_time" else 100000)
                    if type(note.get("pitch")) is not int or not 0 <= note["pitch"] <= 127:
                        raise ValueError("Invalid transcription pitch")
                    if type(note.get("track")) is not int or note["track"] not in {0, 1}:
                        raise ValueError("Invalid transcription melody track")
        tokens = event.get("tokens_by_field", {})
        if not isinstance(tokens, dict) or not tokens.keys() <= fields:
            raise ValueError("Invalid transcription event tokens")
        for values in tokens.values():
            if not isinstance(values, list) or len(values) > 512:
                raise ValueError("Invalid transcription token collection")
            if any(type(v) is not int or not 0 <= v < 100000 for v in values):
                raise ValueError("Invalid transcription token identity")


@dataclass
class _Run:
    execution_id: str
    recipe: TranscriptionRecipe
    deadline: float
    process: object | None = None
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="unknown"))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    overflow: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False
    log_bytes: int = 0
    worker: asyncio.Task | None = None
    spawn: asyncio.Task | None = None
    digests: dict[str, str] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)


class SheetSage2Provider:
    provider_id = "sheetsage2"

    def __init__(self, config: SheetSage2Config, stage_root: Path, managed_inputs=None):
        self.config, self.stage_root = config, stage_root.absolute()
        self.managed_inputs = managed_inputs
        self._runs = {}
        self._closed = False

    def _check(self):
        if os.name != "posix":
            raise ValueError("Unsupported transcription platform")
        _regular_path(self.config.python, executable=True)
        root = self.config.source_root
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid transcription source root")
        manifest = self.config.source_hashes
        if (
            not REQUIRED_SOURCE_FILES <= manifest.keys()
            or manifest.get("infer.py") != SHEETSAGE2_ENTRYPOINT_SHA256
        ):
            raise ValueError("Incomplete or incompatible transcription source manifest")
        for count, entry in enumerate(islice(root.iterdir(), 129)):
            if count >= 128:
                raise ValueError("Transcription source directory limit exceeded")
            if entry.name.endswith((".py", ".json")) and entry.name not in manifest:
                raise ValueError("Unpinned transcription source/configuration")
        for name, expected in manifest.items():
            if hashlib.sha256(_regular_bytes(root / name, SOURCE_LIMIT)).hexdigest() != expected:
                raise ValueError("Transcription source contract changed")
        _regular_path(root / "model.safetensors")

    async def health(self):
        try:
            self._check()
            available = not self._closed and self.managed_inputs is not None
        except (OSError, ValueError):
            available = False
        return ProviderHealth(
            available, {"qualification": "configured-local-resources", "runtime_verified": False}
        )

    def _argv(self, run):
        directory = self.stage_root / run.execution_id
        argv = [
            str(self.config.python),
            "-B",
            str(self.config.source_root / "infer.py"),
            str(directory / "input.wav"),
            "--output",
            str(directory / "outputs"),
            "--model",
            str(self.config.source_root),
            "--device",
            "cpu",
            "--dtype",
            "fp32",
            "--preset",
            "default",
            "--max-seconds",
            str(run.recipe.parameters.max_seconds),
            "--local-files-only",
        ]
        if run.recipe.parameters.melody_only:
            argv.append("--melody-only")
        return argv

    async def submit(self, request: GenerationRequest, job_id):
        if (
            not isinstance(request.payload, TranscriptionRecipe)
            or request.operation != TRANSCRIPTION_CAPABILITY
            or request.payload.template != TRANSCRIPTION_TEMPLATE
            or type(job_id) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", job_id)
        ):
            raise SubmissionRejected("Invalid native transcription request")
        try:
            self._check()
            if self._closed or self.managed_inputs is None or len(self._runs) >= MAX_RUNS:
                raise ValueError("Transcription provider unavailable")
            if (
                self.stage_root.exists()
                and len(list(islice(self.stage_root.iterdir(), MAX_RUNS))) >= MAX_RUNS
            ):
                raise ValueError("Transcription staging capacity exhausted")
            execution_id = uuid4().hex
            with AssetFiles(self.stage_root, execution_id) as files:
                async with self.managed_inputs.stage(
                    job_id, {"audio": request.payload.parameters.audio}, {"audio": {"audio/wav"}}
                ) as readers:
                    reader = readers["audio"]
                    content = bytearray()
                    async for chunk in reader.chunks():
                        if len(content) + len(chunk) > MAX_INPUT_BYTES:
                            raise ValueError("Transcription input size limit exceeded")
                        content.extend(chunk)
                    validate_input_wav(content)
                    files.write("input.wav", content)
                    metadata = {
                        key: reader.metadata[key]
                        for key in (
                            "input_id",
                            "source_asset_id",
                            "sha256",
                            "mime_type",
                            "size_bytes",
                        )
                    }
                os.mkdir("outputs", mode=0o700, dir_fd=files.fd)
        except (OSError, ValueError) as exc:
            raise SubmissionRejected(
                "Native transcription local resources/input unavailable"
            ) from exc
        run = _Run(execution_id, request.payload, time.monotonic() + self.config.timeout_seconds)
        self._runs[execution_id] = run
        # CPU-only execution does not inherit runtime-switching or credential variables.
        env = {
            key: value
            for key, value in os.environ.items()
            if (key in CHILD_ENV_NAMES or key.startswith(("LC_", "OMP_", "MKL_", "OPENBLAS_")))
            and not any(
                v in key.upper() for v in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
            )
        }
        env.update(
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_DATASETS_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            CUDA_VISIBLE_DEVICES="",
            HIP_VISIBLE_DEVICES="",
            ROCR_VISIBLE_DEVICES="",
            OMP_NUM_THREADS="4",
            MKL_NUM_THREADS="4",
            OPENBLAS_NUM_THREADS="4",
        )
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *self._argv(run),
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
            # TimeoutError subclasses OSError. Lost acknowledgement must retain
            # its unknown reservation and late owned subprocess for cleanup.
            if isinstance(exc, OSError) and not isinstance(exc, TimeoutError):
                self._runs.pop(execution_id, None)
                raise SubmissionRejected("Native transcription process could not start") from None

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
            raise SubmissionUnknown("Native transcription spawn outcome unknown") from None
        run.snapshot = JobSnapshot(status="running")
        run.worker = asyncio.create_task(self._watch(run))
        return ProviderJob(execution_id, {"audio": metadata})

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

    def _disk_usage(self, run):
        total, entries = 0, 0

        def visit(fd, depth):
            nonlocal total, entries
            with os.scandir(fd) as listing:
                for entry in listing:
                    entries += 1
                    info = entry.stat(follow_symlinks=False)
                    if entries > 128 or depth > 3:
                        raise ValueError("Transcription output count exceeded")
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(
                            entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            visit(child, depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        total += info.st_size
                    else:
                        raise ValueError("Unsafe transcription output")
                    if total > self.config.output_max_bytes:
                        raise ValueError("Transcription output byte limit exceeded")

        with AssetFiles(self.stage_root / run.execution_id, "outputs", create=False) as files:
            visit(files.fd, 0)

    async def _output_budget(self, run):
        while True:
            self._disk_usage(run)
            await asyncio.sleep(0.05)

    def _collect(self, run):
        self._disk_usage(run)
        outputs, warnings, total = [], [], 0
        with AssetFiles(self.stage_root / run.execution_id, "outputs", create=False) as files:

            def add(output_id, name, kind, mime, role, limit, data=None, index=0):
                nonlocal total
                data = _read(files, name, limit) if data is None else data
                if data is None:
                    return False
                total += len(data)
                if total > self.config.output_max_bytes:
                    raise ValueError("Transcription publication byte limit exceeded")
                run.digests[output_id] = hashlib.sha256(data).hexdigest()
                run.limits[output_id] = limit
                outputs.append(
                    ProviderOutput(output_id, name, kind, mime, OutputRole(role, role, index))
                )
                return True

            midi = _read(files, "transcription.mid", MAX_MIDI_BYTES)
            midi_info = validate_midi(midi or b"", require_notes=True)
            add("midi", "transcription.mid", "midi", "audio/midi", "midi", MAX_MIDI_BYTES, midi)
            result = _json(_read(files, "result.json", MAX_JSON_BYTES) or b"{}")
            if not isinstance(result, dict):
                raise ValueError("Invalid transcription result")
            seconds = result.get("duration_seconds")
            if (
                type(seconds) not in (int, float)
                or not 0 < seconds <= run.recipe.parameters.max_seconds + 1
            ):
                raise ValueError("Invalid transcription duration")
            counts = {}
            for name in (
                "melody_notes",
                "vocal_notes",
                "instrumental_notes",
                "events",
                "abc_measures",
            ):
                value = result.get(name)
                if type(value) is not int or not 0 <= value <= 100000:
                    raise ValueError("Invalid transcription summary count")
                counts[name] = value
            if (
                counts["events"] == 0
                or counts["vocal_notes"] + counts["instrumental_notes"] != counts["melody_notes"]
            ):
                raise ValueError("Inconsistent transcription summary counts")
            events = _json(_read(files, "events.json", MAX_JSON_BYTES) or b"{}")
            if (
                not isinstance(events, dict)
                or events.get("schema_version") != "v1"
                or not isinstance(events.get("events"), list)
                or len(events["events"]) != counts["events"]
                or not isinstance(events.get("prompts"), list)
                or not set(events["prompts"]) <= PROMPTS
                or events.get("has_eos") is not True
            ):
                raise ValueError("Invalid transcription event receipt")
            _events(events["events"], seconds)
            # Publish music events only; root metadata such as arbitrary audio locators is omitted.
            event_payload = {
                name: events[name] for name in ("schema_version", "prompts", "events", "has_eos")
            }
            files.write("events.json", _json_bytes(event_payload))
            add("events", "events.json", "metadata", "application/json", "events", MAX_JSON_BYTES)
            abc = _read(files, "score.abc", MAX_SCORE_BYTES)
            if abc and not result.get("abc_error"):
                _score(abc)
                add("score", "score.abc", "score", "text/vnd.abc", "score", MAX_SCORE_BYTES, abc)
            else:
                warnings.append("abc_unavailable")
            if run.recipe.parameters.melody_only and warnings:
                raise ValueError("Requested melody-only ABC is unavailable")
            if result.get("warnings") or result.get("diagnostics"):
                warnings.append("provider_diagnostics_available")
            summary = {
                **transcription_profile(),
                "source_revision": self.config.source_revision,
                "duration_seconds": seconds,
                "max_seconds": run.recipe.parameters.max_seconds,
                "melody_only": run.recipe.parameters.melody_only,
                **counts,
                "warnings": warnings,
                "midi": midi_info,
            }
            files.write("result.json", _json_bytes(summary))
            add("summary", "result.json", "metadata", "application/json", "summary", MAX_JSON_BYTES)
            for index, name in enumerate(PART_FILES):
                data = _read(files, name, MAX_MIDI_BYTES)
                if data is not None:
                    validate_midi(data)
                    add(
                        f"part-{index}",
                        name,
                        "midi",
                        "audio/midi",
                        "parts",
                        MAX_MIDI_BYTES,
                        data,
                        index,
                    )
            labs = {}
            for name in LAB_FILES:
                data = _read(files, name, 1024 * 1024)
                if data is not None:
                    text = data.decode("utf-8")
                    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                        raise ValueError("Invalid transcription annotation")
                    labs[name[:-4]] = text
            if labs:
                files.write("annotations.json", _json_bytes({"schema_version": 1, "labs": labs}))
                add(
                    "annotations",
                    "annotations.json",
                    "metadata",
                    "application/json",
                    "annotations",
                    MAX_JSON_BYTES,
                )
        self._disk_usage(run)
        return JobSnapshot(
            status="completed", outputs=tuple(outputs), metadata={"transcription": summary}
        )

    async def _watch(self, run):
        readers = [
            asyncio.create_task(self._drain(run, reader))
            for reader in (
                run.process.stdout,
                run.process.stderr,
            )
        ]
        waiter = asyncio.create_task(run.process.wait())
        overflow = asyncio.create_task(run.overflow.wait())
        budget = asyncio.create_task(self._output_budget(run))
        try:
            done, _ = await asyncio.wait(
                (waiter, overflow, budget),
                timeout=0 if run.cancel_requested else max(0, run.deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            code = (
                "log_limit"
                if run.overflow.is_set()
                else "output_limit"
                if budget in done
                else "execution_timeout"
            )
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
                    status="failed", error={"code": "transcription_provider_failed"}
                )
            else:
                await asyncio.gather(*readers)
                if run.cancel_requested:
                    run.snapshot = JobSnapshot(status="cancelled")
                    return
                if run.overflow.is_set() or budget in done:
                    raise ValueError("Transcription resource limit exceeded")
                run.snapshot = self._collect(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            if run.snapshot.status != "cancelled":
                run.snapshot = JobSnapshot(
                    status="failed" if self._settled(run) else "unknown",
                    error={
                        "code": "log_limit"
                        if run.overflow.is_set()
                        else "transcription_output_invalid"
                    },
                )
        finally:
            for task in (*readers, waiter, overflow, budget):
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, waiter, overflow, budget, return_exceptions=True)

    def _get(self, execution_id):
        if type(execution_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", execution_id):
            raise ProviderError("Invalid native transcription execution ID")
        return self._runs.get(execution_id)

    async def inspect(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "transcription_execution_unowned"})
        if run.snapshot.status == "unknown" and run.cancel_requested and self._settled(run):
            run.snapshot = JobSnapshot(status="cancelled")
        return run.snapshot

    async def cancel(self, execution_id):
        run = self._get(execution_id)
        if run is None:
            return JobSnapshot(status="unknown", error={"code": "transcription_execution_unowned"})
        if run.snapshot.status in {"completed", "failed", "cancelled"}:
            return run.snapshot
        run.cancel_requested = True
        settled = await self._terminate(run)
        run.snapshot = JobSnapshot(status="cancelled" if settled else "unknown")
        return run.snapshot

    async def materialize(self, execution_id, output_id):
        run = self._get(execution_id)
        if run is None or run.snapshot.status != "completed" or output_id not in run.digests:
            raise ProviderError("Native transcription output unavailable")
        output = next(item for item in run.snapshot.outputs if item.output_id == output_id)
        try:
            with AssetFiles(self.stage_root / execution_id, "outputs", create=False) as files:
                data = _read(files, output.filename, run.limits[output_id])
            if data is None or hashlib.sha256(data).hexdigest() != run.digests[output_id]:
                raise ValueError("Transcription output changed")
            return data
        except (OSError, ValueError) as exc:
            raise ProviderError("Native transcription output unavailable") from exc

    async def stream_output(self, execution_id, output_id):
        from ..transfers import CHUNK_BYTES

        data = await self.materialize(execution_id, output_id)
        for offset in range(0, len(data), CHUNK_BYTES):
            yield data[offset : offset + CHUNK_BYTES]
            await asyncio.sleep(0)

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
