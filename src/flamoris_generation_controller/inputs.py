"""Immutable input snapshots and bounded readers for trusted provider adapters."""

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .asset_files import AssetFiles
from .input_uploads import MAX_UPLOAD_BYTES, pending_record
from .transfers import CHUNK_BYTES, identity, open_asset
from .workflows import checked_id

MAX_INPUT_BYTES = 64 * 1024**2
MAX_INPUTS = 128
MAX_DISK_BYTES = 512 * 1024**2
MAX_JOB_INPUTS = 4
MAX_JOB_BYTES = 128 * 1024**2
RETENTION_SECONDS = 24 * 3600
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "audio/wav"}


def media_type(prefix):
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WAVE":
        return "audio/wav"
    raise ValueError("Unsupported input media signature")


@dataclass(frozen=True)
class InputReader:
    """Private fd, never a public path; valid only inside the staging context."""

    _fd: int
    _identity: list
    metadata: dict
    _active: list

    async def chunks(self):
        if not self._active[0]:
            raise ValueError("Input lease is closed")
        digest = hashlib.sha256()
        offset = 0
        size = self.metadata["size_bytes"]
        while offset < size:
            if not self._active[0] or identity(os.fstat(self._fd)) != self._identity:
                raise ValueError("Input changed during staging")
            chunk = os.pread(self._fd, min(CHUNK_BYTES, size - offset), offset)
            if not chunk:
                raise ValueError("Input was truncated")
            offset += len(chunk)
            digest.update(chunk)
            yield chunk
            await asyncio.sleep(0)
        if digest.hexdigest() != self.metadata["sha256"]:
            raise ValueError("Input integrity check failed")
        if identity(os.fstat(self._fd)) != self._identity:
            raise ValueError("Input changed during staging")


class ManagedInputs:
    def __init__(self, transfers, root: Path):
        self.transfers = transfers
        self.root = root
        self._creating = asyncio.Lock()
        self._used: set[str] = set()
        self._staging = False
        self.max_bytes = MAX_INPUT_BYTES
        self.disk_bytes = MAX_DISK_BYTES
        self.max_inputs = MAX_INPUTS
        self.retention_seconds = RETENTION_SECONDS
        self.deadline = 300

    def _record(self, input_id, files, *, expired=False):
        raw = files.read("metadata.json", 8192)
        try:
            record = json.loads(raw) if raw else {}
            if (
                record["version"] not in (1, 2)
                or record["input_id"] != input_id
                or record["mime_type"] not in ALLOWED_TYPES
                or record["media_kind"] != record["mime_type"].split("/")[0]
                or type(record["size_bytes"]) is not int
                or not 0 < record["size_bytes"] <= self.max_bytes
                or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                or type(record["created_at"]) not in (float, int)
                or type(record["expires_at"]) not in (float, int)
                or not 0 <= record["created_at"] < record["expires_at"]
                or record["expires_at"] - record["created_at"] > RETENTION_SECONDS
                or not isinstance(record["identity"], list)
                or len(record["identity"]) != 5
            ):
                raise ValueError("Invalid managed input record")
            if record["version"] == 2:
                if (
                    record.get("source_kind") != "upload"
                    or record["source_asset_id"] is not None
                    or record["media_kind"] != "image"
                    or record["size_bytes"] > MAX_UPLOAD_BYTES
                ):
                    raise ValueError("Invalid uploaded input provenance")
            else:
                self.transfers.jobs._parse_asset_id(record["source_asset_id"])
            if not expired and record["expires_at"] <= time.time():
                raise ValueError("Managed input expired")
            return record
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid or unpublished managed input") from None

    @staticmethod
    def _public(record):
        result = {
            key: record[key]
            for key in (
                "input_id",
                "source_asset_id",
                "sha256",
                "mime_type",
                "media_kind",
                "size_bytes",
                "created_at",
                "expires_at",
            )
        }
        if record.get("source_kind") == "upload":
            result["source_kind"] = "upload"
        return result

    def get(self, input_id):
        checked_id(input_id)
        with AssetFiles(self.root, input_id, create=False) as files:
            record = self._record(input_id, files)
            with open_asset(files, "content") as fd:
                if identity(os.fstat(fd)) != record["identity"]:
                    raise ValueError("Managed input identity changed")
            return self._public(record)

    def _remove(self, input_id):
        with AssetFiles(self.root, input_id, create=False) as files:
            # Bounded and closed namespace; never recursively remove user files.
            with os.scandir(files.fd) as entries:
                names = []
                for count, entry in enumerate(entries):
                    if count >= 8:
                        raise ValueError("Managed input directory contains unexpected files")
                    if entry.name not in {
                        "content",
                        "metadata.json",
                        "upload.json",
                    } and not re.fullmatch(r"\.tmp-[a-f0-9]{32}", entry.name):
                        raise ValueError("Managed input directory contains unexpected files")
                    files.size(entry.name)  # Normalize symlink rejection before opening.
                    with open_asset(files, entry.name):
                        pass
                    names.append(entry.name)
            for name in names:
                files.delete(name)
            files._current()
            os.rmdir(input_id, dir_fd=files.root_fd)

    def delete(self, input_id):
        checked_id(input_id)
        if input_id in self._used:
            raise ValueError("Managed input is in use")
        try:
            self._remove(input_id)
        except ValueError as exc:
            if str(exc) != "Unknown archived asset ID":
                raise
        return {"input_id": input_id, "deleted": True}

    def _capacity(self):
        """Clean expired/aborted publications and charge all remaining bytes."""
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
        try:
            keys = []
            with os.scandir(fd) as entries:
                for count, entry in enumerate(entries):
                    if count >= self.max_inputs + 1:
                        raise ValueError("Managed input count limit exceeded")
                    checked_id(entry.name)
                    keys.append(entry.name)
            total = count = 0
            for key in keys:
                with AssetFiles(self.root, key, create=False) as files:
                    raw = files.read("metadata.json", 8192)
                    record = self._record(key, files, expired=True) if raw else None
                    pending = pending_record(key, files, expired=True) if record is None else None
                    retained = record or pending
                    expired = retained is None or retained["expires_at"] <= time.time()
                    if not expired or key in self._used:
                        stored = 0
                        with os.scandir(files.fd) as entries:
                            for number, entry in enumerate(entries):
                                if number >= 8:
                                    raise ValueError("Managed input directory limit exceeded")
                                stored += identity(entry.stat(follow_symlinks=False))[2]
                        # Reserve the whole declared upload before accepting chunks,
                        # including after restart; other creates cannot consume it.
                        total += max(stored, pending["size_bytes"] + 8192) if pending else stored
                        count += 1
                if expired and key not in self._used:
                    self._remove(key)
            if count >= self.max_inputs:
                raise ValueError("Managed input count limit exceeded")
            return self.disk_bytes - total - 8192
        finally:
            os.close(fd)

    async def create(self, asset_id):
        self.transfers.jobs._parse_asset_id(asset_id)
        if self._creating.locked():
            raise ValueError("Managed input creation busy; retry later")
        async with self._creating, asyncio.timeout(self.deadline):
            remaining = self._capacity()
            if remaining <= 0:
                raise ValueError("Managed input disk budget exhausted")
            source = await self.transfers.prepare(
                asset_id,
                max_bytes=min(self.max_bytes, remaining),
                allowed_types=ALLOWED_TYPES,
            )
            if (
                source["mime_type"] not in ALLOWED_TYPES
                or source["media_kind"] != source["mime_type"].split("/")[0]
                or not 0 < source["size_bytes"] <= min(self.max_bytes, remaining)
            ):
                raise ValueError("Input media type or byte limit exceeded")
            input_id = uuid4().hex
            published = False
            try:
                with AssetFiles(self.root, input_id) as files:
                    fd = os.open(
                        "content",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=files.fd,
                    )
                    digest = hashlib.sha256()
                    total = 0
                    prefix = b""
                    with os.fdopen(fd, "wb") as stream:
                        while total < source["size_bytes"]:
                            part = await self.transfers.read(asset_id, source["sha256"], total)
                            chunk = base64.b64decode(part["data_base64"], validate=True)
                            if (
                                not chunk
                                or len(chunk) > CHUNK_BYTES
                                or total + len(chunk) > source["size_bytes"]
                                or hashlib.sha256(chunk).hexdigest() != part["chunk_sha256"]
                            ):
                                raise ValueError("Invalid input source chunk")
                            prefix = (prefix + chunk[:12])[:12]
                            stream.write(chunk)
                            digest.update(chunk)
                            total += len(chunk)
                            await asyncio.sleep(0)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if digest.hexdigest() != source["sha256"]:
                        raise ValueError("Input source digest mismatch")
                    if media_type(prefix) != source["mime_type"]:
                        raise ValueError("Input MIME does not match media signature")
                    now = time.time()
                    with open_asset(files, "content") as fd:
                        file_identity = identity(os.fstat(fd))
                    record = dict(
                        version=1,
                        input_id=input_id,
                        source_asset_id=asset_id,
                        sha256=digest.hexdigest(),
                        mime_type=source["mime_type"],
                        media_kind=source["media_kind"],
                        size_bytes=total,
                        created_at=now,
                        expires_at=now + self.retention_seconds,
                        identity=file_identity,
                    )
                    files.write("metadata.json", json.dumps(record).encode())
                    os.fsync(files.fd)
                    published = True
                    return self._public(record)
            finally:
                if not published:
                    self._remove(input_id)

    @asynccontextmanager
    async def stage(self, job_id, references, allowed_types):
        """Adapters call this only inside the shared JobStore reservation.

        The adapter receives readers, not a caller-selectable staging path.
        Keep the context alive until its runtime no longer needs input bytes.
        """
        checked_id(job_id)
        if self.transfers.jobs._active_job_id != job_id:
            raise ValueError("Input staging requires the active job reservation")
        if (
            self._staging
            or not isinstance(references, dict)
            or not 1 <= len(references) <= MAX_JOB_INPUTS
            or set(references) != set(allowed_types)
        ):
            raise ValueError("Invalid, undeclared or busy managed input bindings")
        readers = {}
        fds = []
        active = [True]
        self._staging = True
        try:
            total = 0
            for name, key in references.items():
                checked_id(key)
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                    raise ValueError("Invalid input parameter name")
                with AssetFiles(self.root, key, create=False) as files:
                    record = self._record(key, files)
                    total += record["size_bytes"]
                    if record["mime_type"] not in allowed_types[name] or total > MAX_JOB_BYTES:
                        raise ValueError("Input binding media type or aggregate limit exceeded")
                    with open_asset(files, "content") as fd:
                        current = identity(os.fstat(fd))
                        if current != record["identity"]:
                            raise ValueError("Managed input identity changed")
                        pinned = os.dup(fd)
                        fds.append(pinned)
                    readers[name] = InputReader(pinned, current, self._public(record), active)
                    self._used.add(key)
            async with asyncio.timeout(self.deadline):
                yield readers
        finally:
            active[0] = False
            for fd in fds:
                os.close(fd)
            for reader in readers.values():
                self._used.discard(reader.metadata["input_id"])
            self._staging = False
