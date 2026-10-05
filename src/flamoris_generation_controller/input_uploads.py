"""Bounded resumable uploads into the existing immutable input authority."""

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager

from .asset_files import AssetFiles
from .image_decode import decode_image
from .transfers import CHUNK_BYTES, identity, open_asset
from .workflows import checked_id

MAX_UPLOAD_BYTES = 8 * 1024**2
UPLOAD_SECONDS = 600
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


def pending_record(input_id, files, *, expired=False):
    raw = files.read("upload.json", 8192)
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        if (
            record["version"] != 1
            or record["upload_id"] != input_id
            or record["mime_type"] not in IMAGE_TYPES
            or type(record["size_bytes"]) is not int
            or not 0 < record["size_bytes"] <= MAX_UPLOAD_BYTES
            or not isinstance(record["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or type(record["expires_at"]) not in (float, int)
            or type(record["created_at"]) not in (float, int)
            or not 0 < record["expires_at"] - record["created_at"] <= UPLOAD_SECONDS
            or not isinstance(record["identity"], list)
            or len(record["identity"]) != 5
        ):
            raise ValueError("Invalid pending upload")
        if not expired and record["expires_at"] <= time.time():
            raise ValueError("Upload expired; start a new upload")
        return record
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("Invalid pending upload") from None


def sync_write(files, name, data):
    files.write(name, data)
    with open_asset(files, name) as fd:
        os.fsync(fd)
    os.fsync(files.fd)


class InputUploads:
    """Studio supplies a pre-recorded private UUID; IDs never grant user access."""

    def __init__(self, managed):
        self.managed = managed

    @asynccontextmanager
    async def mutation(self):
        if self.managed._creating.locked():
            raise ValueError("Managed input creation busy; retry later")
        async with self.managed._creating, asyncio.timeout(self.managed.deadline):
            yield

    def published(self, key, files):
        if files.read("metadata.json", 8192) is None:
            return None
        result = self.managed.get(key)
        if result.get("source_kind") != "upload":
            raise ValueError("Upload identity conflicts with an existing input")
        return result

    async def begin(self, upload_id, mime_type, size_bytes, sha256):
        checked_id(upload_id)
        if (
            mime_type not in IMAGE_TYPES
            or type(size_bytes) is not int
            or not 0 < size_bytes <= MAX_UPLOAD_BYTES
            or not isinstance(sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise ValueError("Invalid bounded image upload")
        expected = dict(mime_type=mime_type, size_bytes=size_bytes, sha256=sha256)
        async with self.mutation():
            # Inspect an existing identity before quota pruning. An exact replay
            # never overwrites data or extends the original lifetime.
            try:
                with AssetFiles(self.managed.root, upload_id, create=False) as files:
                    record = self.published(upload_id, files) or pending_record(upload_id, files)
                    if record is None or any(record[k] != v for k, v in expected.items()):
                        raise ValueError("Upload identity/content conflict")
                    with open_asset(files, "content") as fd:
                        if identity(os.fstat(fd)) != record.get("identity", identity(os.fstat(fd))):
                            raise ValueError("Upload content changed; abort this upload")
                        offset = os.fstat(fd).st_size
                    return {"upload_id": upload_id, "offset": offset}
            except ValueError as exc:
                if str(exc) != "Unknown archived asset ID":
                    raise
            if self.managed._capacity() < size_bytes:
                raise ValueError("Managed input disk budget exhausted")
            published = False
            try:
                with AssetFiles(self.managed.root, upload_id) as files:
                    fd = os.open(
                        "content",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=files.fd,
                    )
                    try:
                        os.fsync(fd)
                        file_identity = identity(os.fstat(fd))
                    finally:
                        os.close(fd)
                    now = time.time()
                    record = dict(
                        version=1,
                        upload_id=upload_id,
                        **expected,
                        created_at=now,
                        expires_at=now + UPLOAD_SECONDS,
                        identity=file_identity,
                    )
                    sync_write(files, "upload.json", json.dumps(record).encode())
                    published = True
                    return {"upload_id": upload_id, "offset": 0}
            finally:
                if not published:
                    self.managed._remove(upload_id)

    async def write(self, upload_id, offset, data_base64, chunk_sha256):
        checked_id(upload_id)
        if (
            type(offset) is not int
            or not 0 <= offset < MAX_UPLOAD_BYTES
            or not isinstance(data_base64, str)
            or not 0 < len(data_base64) <= 4 * ((CHUNK_BYTES + 2) // 3)
            or not isinstance(chunk_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", chunk_sha256)
        ):
            raise ValueError("Invalid bounded upload chunk")
        try:
            data = base64.b64decode(data_base64, validate=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid bounded upload chunk") from None
        if not 0 < len(data) <= CHUNK_BYTES or hashlib.sha256(data).hexdigest() != chunk_sha256:
            raise ValueError("Upload chunk integrity mismatch")
        async with self.mutation():
            with AssetFiles(self.managed.root, upload_id, create=False) as files:
                if self.published(upload_id, files) is not None:
                    raise ValueError("Upload already published")
                record = pending_record(upload_id, files)
                if record is None or offset + len(data) > record["size_bytes"]:
                    raise ValueError("Upload chunk exceeds declared size")
                with open_asset(files, "content") as fd:
                    if identity(os.fstat(fd)) != record["identity"]:
                        raise ValueError("Upload content changed; abort this upload")
                    size = os.fstat(fd).st_size
                    if offset < size:
                        if offset + len(data) > size or os.pread(fd, len(data), offset) != data:
                            raise ValueError("Upload chunk conflicts with committed content")
                        return {"upload_id": upload_id, "offset": size}
                    if offset != size:
                        raise ValueError("Upload chunk is out of order")
                    info = os.fstat(fd)
                    writer = os.open(
                        "content", os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=files.fd
                    )
                    try:
                        if identity(os.fstat(writer)) != identity(info):
                            raise ValueError("Upload content changed")
                        with os.fdopen(writer, "ab", closefd=False) as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(writer)
                        record["identity"] = identity(os.fstat(writer))
                    finally:
                        os.close(writer)
                sync_write(files, "upload.json", json.dumps(record).encode())
                return {"upload_id": upload_id, "offset": offset + len(data)}

    async def finish(self, upload_id):
        checked_id(upload_id)
        async with self.mutation():
            with AssetFiles(self.managed.root, upload_id, create=False) as files:
                result = self.published(upload_id, files)
                if result is not None:
                    return result
                record = pending_record(upload_id, files)
                if record is None:
                    raise ValueError("Unknown image upload")
                with open_asset(files, "content") as fd:
                    if (
                        identity(os.fstat(fd)) != record["identity"]
                        or os.fstat(fd).st_size != record["size_bytes"]
                    ):
                        raise ValueError("Upload is incomplete or changed")
                    data = os.pread(fd, record["size_bytes"] + 1, 0)
                    if (
                        len(data) != record["size_bytes"]
                        or hashlib.sha256(data).hexdigest() != record["sha256"]
                    ):
                        raise ValueError("Upload integrity mismatch")
                    # Keep the mutation lock until the bounded decoder exits,
                    # including cancellation, so aborted requests cannot pile up workers.
                    task = asyncio.create_task(
                        asyncio.to_thread(decode_image, data, record["mime_type"])
                    )
                    self.managed._used.add(upload_id)
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        while not task.done():
                            try:
                                await asyncio.shield(task)
                            except asyncio.CancelledError:
                                continue
                            except Exception:
                                break
                        if not task.cancelled():
                            task.exception()
                        raise
                    finally:
                        self.managed._used.discard(upload_id)
                    if identity(os.fstat(fd)) != record["identity"]:
                        raise ValueError("Upload content changed during validation")
                now = time.time()
                final = dict(
                    version=2,
                    input_id=upload_id,
                    source_asset_id=None,
                    source_kind="upload",
                    mime_type=record["mime_type"],
                    media_kind="image",
                    size_bytes=record["size_bytes"],
                    sha256=record["sha256"],
                    created_at=now,
                    expires_at=now + self.managed.retention_seconds,
                    identity=record["identity"],
                )
                sync_write(files, "metadata.json", json.dumps(final).encode())
                files.delete("upload.json")
                os.fsync(files.fd)
                return self.managed.get(upload_id)
