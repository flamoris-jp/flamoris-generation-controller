"""Bounded, cursor-free transfers using JobStore's identity and deletion locks."""

import asyncio
import base64
import hashlib
import json
import os
import re
import stat
from contextlib import aclosing, contextmanager
from uuid import uuid4

from .asset_files import AssetFiles

CHUNK_BYTES = 256 * 1024
MAX_SCAN = 65536


def identity(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Asset must be a private regular file")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def open_asset(files, name):
    files._current()
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=files.fd)
    try:
        identity(os.fstat(fd))
        yield fd
    finally:
        os.close(fd)


def disk_usage(root):
    """Bound enumeration and never follow provider/user supplied paths."""
    total = count = 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    with _root_fd(root, flags) as root_fd:
        with os.scandir(root_fd) as jobs:
            for entry in jobs:
                count += 1
                if count > MAX_SCAN:
                    raise ValueError("Asset disk scan limit exceeded")
                if not re.fullmatch(r"[a-f0-9]{32}", entry.name):
                    continue
                fd = os.open(entry.name, flags, dir_fd=root_fd)
                try:
                    with os.scandir(fd) as entries:
                        for item in entries:
                            count += 1
                            if count > MAX_SCAN:
                                raise ValueError("Asset disk scan limit exceeded")
                            info = item.stat(follow_symlinks=False)
                            identity(info)
                            total += info.st_size
                finally:
                    os.close(fd)
    return total


@contextmanager
def _root_fd(root, flags):
    fd = os.open(root, flags)
    try:
        yield fd
    finally:
        os.close(fd)


class AssetTransfers:
    def __init__(self, jobs, *, max_bytes=1024**3, disk_bytes=8 * 1024**3):
        self.jobs = jobs
        self.max_bytes = max_bytes
        self.disk_bytes = disk_bytes
        self.deadline = 300
        self.io_timeout = 30
        self._preparing = asyncio.Lock()

    def lock(self, asset_id):
        job_id, _ = self.jobs._parse_asset_id(asset_id)
        job = self.jobs._jobs.get(job_id)
        return job.lock if job else self.jobs._archived_lock(job_id)

    async def resolve(self, asset_id, files):
        job_id, index = self.jobs._parse_asset_id(asset_id)
        job = self.jobs._jobs.get(job_id)
        if job:
            await self.jobs._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            asset, path = self.jobs._asset_metadata(job_id, job, index)
        else:
            path, deleted = self.jobs._archived_file(job_id, index, files)
            if deleted or path is None:
                raise ValueError("Unknown archived asset ID")
            from .jobs import MEDIA_TYPES

            kind, mime, _ = MEDIA_TYPES[path.suffix]
            asset = dict(
                asset_id=asset_id,
                job_id=job_id,
                filename=path.name,
                media_kind=kind,
                mime_type=mime,
                output_index=index,
                **self.jobs._archived_roles(files, index),
                **self.jobs._archived_provenance(files),
            )
        return asset, path, job

    @staticmethod
    def receipt_name(index):
        return f".integrity-{index:03d}.json"

    async def prepare(self, asset_id, *, max_bytes=None, allowed_types=None):
        # Private limits allow managed-input callers to fail before provider I/O.
        if max_bytes is not None and (
            type(max_bytes) is not int or not 0 < max_bytes <= self.max_bytes
        ):
            raise ValueError("Invalid asset preparation byte limit")
        limit = min(self.max_bytes, max_bytes) if max_bytes is not None else self.max_bytes
        if self._preparing.locked():
            raise ValueError("Asset preparation busy; retry later")
        async with self._preparing, asyncio.timeout(self.deadline), self.lock(asset_id):
            job_id, index = self.jobs._parse_asset_id(asset_id)
            with AssetFiles(
                self.jobs.output_dir, job_id, create=job_id in self.jobs._jobs
            ) as files:
                asset, path, job = await self.resolve(asset_id, files)
                if allowed_types is not None and (
                    asset["mime_type"] not in allowed_types
                    or asset["media_kind"] != asset["mime_type"].split("/")[0]
                ):
                    raise ValueError("Input media type is unsupported")
                if asset.get("size_bytes") is not None and asset["size_bytes"] > limit:
                    raise ValueError("Asset exceeds preparation size limit")
                # Only this helper's names, under the same job lock, are recoverable.
                with os.scandir(files.fd) as entries:
                    leftovers = []
                    for number, item in enumerate(entries):
                        if number >= 256:
                            raise ValueError("Job directory scan limit exceeded")
                        if re.fullmatch(r"\.transfer-[a-f0-9]{32}", item.name):
                            leftovers.append(item.name)
                for name in leftovers:
                    files.delete(name)
                if files.size(path.name) is None:
                    if job is None:
                        raise ValueError("Asset unavailable after restart; provider mapping lost")
                    await self._materialize(files, path.name, job, index, limit)
                with open_asset(files, path.name) as fd:
                    before = identity(os.fstat(fd))
                    if not 0 < before[2] <= limit:
                        raise ValueError("Asset exceeds transfer size limit or is empty")
                    digest = hashlib.sha256()
                    total = 0
                    while chunk := os.read(fd, CHUNK_BYTES):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("Asset exceeds transfer size limit")
                        digest.update(chunk)
                        await asyncio.sleep(0)
                    if identity(os.fstat(fd)) != before or total != before[2]:
                        raise ValueError("Asset changed during preparation")
                receipt = dict(sha256=digest.hexdigest(), identity=before)
                files.write(self.receipt_name(index), json.dumps(receipt).encode())
                return {
                    **asset,
                    "materialized": True,
                    "size_bytes": total,
                    "sha256": receipt["sha256"],
                    "chunk_bytes": CHUNK_BYTES,
                    "transfer_version": 1,
                }

    async def _materialize(self, files, name, job, index, max_bytes):
        provider = self.jobs.providers.get(job.provider_id)
        stream_output = getattr(provider, "stream_output", None)
        if stream_output is None:
            raise ValueError("Provider does not support bounded asset streaming")
        remaining = self.disk_bytes - disk_usage(self.jobs.output_dir)
        limit = min(max_bytes, remaining - 1024 * 1024)
        if limit <= 0:
            raise ValueError("Managed output disk budget exhausted")
        temporary = f".transfer-{uuid4().hex}"
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=files.fd
        )
        try:
            with os.fdopen(fd, "wb") as output:
                total = 0
                iterator = stream_output(
                    job.provider_execution_id, job.snapshot.outputs[index].output_id
                )
                async with aclosing(iterator):
                    while True:
                        try:
                            async with asyncio.timeout(self.io_timeout):
                                chunk = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        if not isinstance(chunk, bytes) or not 0 < len(chunk) <= CHUNK_BYTES:
                            raise ValueError("Provider returned invalid transfer chunk")
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("Asset exceeds transfer size or disk limit")
                        output.write(chunk)
                if not total:
                    raise ValueError("Provider returned empty asset")
                output.flush()
                os.fsync(output.fileno())
            files._current()
            files.size(name)
            os.replace(temporary, name, src_dir_fd=files.fd, dst_dir_fd=files.fd)
            os.fsync(files.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=files.fd)
            except FileNotFoundError:
                pass

    async def read(self, asset_id, sha256, offset, length=CHUNK_BYTES):
        if (
            not isinstance(sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", sha256)
            or type(offset) is not int
            or offset < 0
            or type(length) is not int
            or not 1 <= length <= CHUNK_BYTES
        ):
            raise ValueError("Invalid bounded transfer request")
        async with asyncio.timeout(self.io_timeout), self.lock(asset_id):
            job_id, index = self.jobs._parse_asset_id(asset_id)
            with AssetFiles(self.jobs.output_dir, job_id, create=False) as files:
                _, path, _ = await self.resolve(asset_id, files)
                raw = files.read(self.receipt_name(index), 4096)
                try:
                    receipt = json.loads(raw) if raw else {}
                    if receipt["sha256"] != sha256:
                        raise ValueError("Asset digest changed; prepare again")
                    expected = receipt["identity"]
                except (KeyError, TypeError, json.JSONDecodeError):
                    raise ValueError("Asset must be prepared before reading") from None
                with open_asset(files, path.name) as fd:
                    before = identity(os.fstat(fd))
                    if before != expected or not 0 < before[2] <= self.max_bytes:
                        raise ValueError("Asset identity changed; prepare again")
                    if offset > before[2]:
                        raise ValueError("Offset exceeds asset size")
                    os.lseek(fd, offset, os.SEEK_SET)
                    data = os.read(fd, min(length, before[2] - offset))
                    if identity(os.fstat(fd)) != before:
                        raise ValueError("Asset changed during read")
                    files._current()
                return dict(
                    asset_id=asset_id,
                    sha256=sha256,
                    offset=offset,
                    size_bytes=before[2],
                    data_base64=base64.b64encode(data).decode(),
                    chunk_sha256=hashlib.sha256(data).hexdigest(),
                    next_offset=offset + len(data),
                    eof=offset + len(data) == before[2],
                )
