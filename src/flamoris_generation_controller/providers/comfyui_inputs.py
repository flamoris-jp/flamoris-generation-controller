"""Durable, bounded input copies in an explicitly shared ComfyUI input root.

This is an artifact ledger, not a job authority. Only the adapter's definite
pre-admission rejection or terminal provider history authorizes release.
"""

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..durable import CommitUnknown, Records
from .base import execution_identity
from .comfyui_retention import identity as file_identity

NAMESPACE = "flamoris-inputs"
MAX_RECORD_BYTES = 1024 * 1024
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
TERMINAL = {"completed", "failed", "cancelled"}


def identity(info):
    try:
        return file_identity(info)
    except ValueError:
        raise ValueError("ComfyUI input must be a single-link regular file") from None


@contextmanager
def input_root(root):
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("ComfyUI input root must be an absolute directory")
    fd = os.open("/", FLAGS)
    try:
        for part in root.parts[1:]:
            next_fd = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


class ComfyUIInputs:
    def __init__(self, root: Path, *, state_root: Path, max_files=128, max_bytes=512 * 1024**2):
        if state_root.resolve().is_relative_to(root.resolve()):
            raise ValueError("ComfyUI input authority must be outside the provider input root")
        self.root = root
        self.authority = Records(state_root, "comfyui-input-authority")
        self.max_files = max_files
        self.max_bytes = max_bytes

    def available(self):
        try:
            with self._guard() as (_, fd, records, _lock):
                for name, record in records.items():
                    if record["identity"] is None:
                        return False  # Incomplete copies require reconciliation.
                    if record["state"] == "protected":
                        if (
                            identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                            != record["identity"]
                        ):
                            return False
                return True
        except (OSError, ValueError):
            return False

    @contextmanager
    def _guard(self):
        anchor = self.authority.read("root.json")
        if anchor is not None and (
            set(anchor) != {"schema_version", "root", "namespace", "lock"}
            or type(anchor["schema_version"]) is not int
            or anchor["schema_version"] != 1
            or any(
                not isinstance(anchor[key], list)
                or len(anchor[key]) != 2
                or any(type(v) is not int or v < 0 for v in anchor[key])
                for key in ("root", "namespace", "lock")
            )
        ):
            raise ValueError("Invalid ComfyUI input storage authority")
        with input_root(self.root) as root_fd:
            created = False
            try:
                # A provisioned shared group can read inputs, but only Generation
                # owns namespace writes. The parent may supply the setgid group.
                if anchor is None:
                    os.mkdir(NAMESPACE, 0o2750, dir_fd=root_fd)
                    created = True
                    os.fsync(root_fd)
            except FileExistsError:
                pass
            fd = os.open(NAMESPACE, FLAGS, dir_fd=root_fd)
            lock = None
            try:
                lock = os.open(
                    ".lock",
                    os.O_RDWR
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK
                    | (os.O_CREAT | os.O_EXCL if created else 0),
                    0o600,
                    dir_fd=fd,
                )
                identity(os.fstat(lock))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if identity(os.stat(".lock", dir_fd=fd, follow_symlinks=False)) != identity(
                    os.fstat(lock)
                ):
                    raise ValueError("ComfyUI input lock changed")
                self._current(root_fd, fd, lock)
                records = self._read(fd)
                self._inventory(fd, records)
                if created:
                    os.fsync(fd)  # Persist the stable lock before committing its authority.
                observed = {
                    "schema_version": 1,
                    **{
                        name: [os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino]
                        for name, descriptor in (
                            ("root", root_fd),
                            ("namespace", fd),
                            ("lock", lock),
                        )
                    },
                }
                if anchor is None:
                    self.authority.write("root.json", observed)
                elif anchor != observed:
                    raise ValueError("ComfyUI input storage identity changed; reconcile authority")
                self._current(root_fd, fd, lock)
                yield root_fd, fd, records, lock
            finally:
                if lock is not None:
                    os.close(lock)
                os.close(fd)

    def _current(self, root_fd, fd, lock):
        with input_root(self.root) as current:
            a, b = os.fstat(current), os.fstat(root_fd)
            if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
                raise ValueError("ComfyUI input root changed")
        a = os.stat(NAMESPACE, dir_fd=root_fd, follow_symlinks=False)
        b = os.fstat(fd)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise ValueError("ComfyUI input directory changed")
        if b.st_uid != os.geteuid() or a.st_mode & 0o022:
            raise ValueError("ComfyUI input namespace must be owned by Generation only")
        if identity(os.stat(".lock", dir_fd=fd, follow_symlinks=False)) != identity(os.fstat(lock)):
            raise ValueError("ComfyUI input lock changed")

    def _read(self, fd):
        try:
            record_fd = os.open(
                ".ledger.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
        except FileNotFoundError:
            return {}
        try:
            info = identity(os.fstat(record_fd))
            if info["size"] > MAX_RECORD_BYTES:
                raise ValueError("ComfyUI input ledger exceeds size limit")
            raw = bytearray()
            while len(raw) <= MAX_RECORD_BYTES:
                chunk = os.read(record_fd, min(65536, MAX_RECORD_BYTES + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) != info["size"]:
                raise ValueError("ComfyUI input ledger changed")
            if (
                identity(os.fstat(record_fd)) != info
                or identity(os.stat(".ledger.json", dir_fd=fd, follow_symlinks=False)) != info
            ):
                raise ValueError("ComfyUI input ledger changed")
            data = json.loads(raw)
        finally:
            os.close(record_fd)
        if not isinstance(data, dict) or set(data) != {"schema_version", "files"}:
            raise ValueError("Invalid ComfyUI input ledger")
        records = data["files"]
        if (
            type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or not isinstance(records, dict)
        ):
            raise ValueError("Invalid ComfyUI input ledger")
        if len(records) > self.max_files:
            raise ValueError("ComfyUI input file budget exceeded")
        for name, record in records.items():
            if not re.fullmatch(r"[a-f0-9]{32}-[a-f0-9]{32}\.(png|jpg|webp)", name):
                raise ValueError("Invalid ComfyUI input filename")
            if not isinstance(record, dict) or set(record) != {
                "job_id",
                "size",
                "sha256",
                "identity",
                "state",
                "execution_id",
                "cleanup",
            }:
                raise ValueError("Invalid ComfyUI input receipt")
            if (
                record["job_id"] != name[:32]
                or type(record["size"]) is not int
                or not 1 <= record["size"] <= 64 * 1024**2
                or not isinstance(record["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                or not isinstance(record["state"], str)
                or record["state"] not in {"protected", "released"}
                or not isinstance(record["cleanup"], str)
                or (
                    record["cleanup"]
                    and not re.fullmatch(r"\.cleanup-[a-f0-9]{32}", record["cleanup"])
                )
                or (record["state"] == "protected" and record["cleanup"])
            ):
                raise ValueError("Invalid ComfyUI input receipt")
            execution_identity(record["execution_id"], allow_empty=True)
            expected = record["identity"]
            if expected is not None and (
                not isinstance(expected, dict)
                or set(expected) != {"device", "inode", "size", "mtime_ns"}
                or any(type(v) is not int or v < 0 for v in expected.values())
                or expected["size"] != record["size"]
            ):
                raise ValueError("Invalid ComfyUI input file identity")
        if sum(r["size"] for r in records.values()) > self.max_bytes:
            raise ValueError("ComfyUI input byte budget exceeded")
        return records

    def _inventory(self, fd, records):
        known = {".lock", ".ledger.json", *records}
        known.update(r["cleanup"] for r in records.values() if r["cleanup"])
        candidates = dict(records)
        candidates.update({r["cleanup"]: r for r in records.values() if r["cleanup"]})
        with os.scandir(fd) as entries:
            for index, entry in enumerate(entries):
                if index >= 2 * self.max_files + 2 or entry.name not in known:
                    raise ValueError("Unrecorded ComfyUI input; reconcile before staging")
                info = identity(entry.stat(follow_symlinks=False))
                record = candidates.get(entry.name)
                if record is not None:
                    if info["size"] > record["size"]:
                        raise ValueError("ComfyUI input exceeds reserved bytes")
                    if record["identity"] is not None and info != record["identity"]:
                        raise ValueError("ComfyUI input identity changed; reconcile")

    def _write(self, root_fd, fd, records, lock):
        raw = json.dumps({"schema_version": 1, "files": records}, sort_keys=True).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("ComfyUI input ledger exceeds size limit")
        temp = ".ledger-" + uuid4().hex
        descriptor = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        published = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._current(root_fd, fd, lock)
            os.replace(temp, ".ledger.json", src_dir_fd=fd, dst_dir_fd=fd)
            published = True
            os.fsync(fd)
        except OSError as exc:
            if published:
                raise CommitUnknown("ComfyUI input receipt durability is uncertain") from exc
            raise
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass

    def stage(self, job_id, data, mime_type):
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime_type)
        if (
            not isinstance(job_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", job_id)
            or suffix is None
            or not isinstance(data, bytes)
            or not 1 <= len(data) <= 64 * 1024**2
        ):
            raise ValueError("Invalid ComfyUI input copy")
        with self._guard() as (root_fd, fd, records, lock):
            self._cleanup(root_fd, fd, records, lock)
            if (
                len(records) >= self.max_files
                or sum(r["size"] for r in records.values()) + len(data) > self.max_bytes
            ):
                raise ValueError(
                    "ComfyUI input storage budget exhausted; reconcile protected copies"
                )
            name = job_id + "-" + uuid4().hex + suffix
            records[name] = {
                "job_id": job_id,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "identity": None,
                "state": "protected",
                "execution_id": "",
                "cleanup": "",
            }
            self._write(root_fd, fd, records, lock)  # Reserve before any provider-visible bytes.
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                records[name]["identity"] = identity(os.fstat(stream.fileno()))
            os.fsync(fd)
            self._write(root_fd, fd, records, lock)
            return NAMESPACE + "/" + name

    def before_post(self, job_id):
        with self._guard() as (root_fd, fd, records, lock):
            for name, record in records.items():
                if record["job_id"] != job_id:
                    continue
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    expected = record["identity"]
                    if record["state"] != "protected" or expected != identity(os.fstat(descriptor)):
                        raise ValueError("ComfyUI input changed before submission")
                    digest = hashlib.sha256()
                    remaining = record["size"]
                    while remaining:
                        chunk = os.read(descriptor, min(65536, remaining))
                        if not chunk:
                            raise ValueError("ComfyUI input became incomplete")
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if (
                        digest.hexdigest() != record["sha256"]
                        or identity(os.fstat(descriptor)) != expected
                        or identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != expected
                    ):
                        raise ValueError("ComfyUI input changed before submission")
                finally:
                    os.close(descriptor)
            self._current(root_fd, fd, lock)

    def bind(self, job_id, execution_id):
        execution_identity(execution_id)
        with self._guard() as (root_fd, fd, records, lock):
            for record in records.values():
                if record["job_id"] == job_id:
                    if record["state"] != "protected" or record["execution_id"] not in (
                        "",
                        execution_id,
                    ):
                        raise ValueError("ComfyUI input execution binding changed")
                    record["execution_id"] = execution_id
            self._write(root_fd, fd, records, lock)

    def rejected(self, job_id):
        self._release(job_id=job_id)

    def observe(self, execution_id, status):
        if status in TERMINAL:
            execution_identity(execution_id)
            self._release(execution_id=execution_id)

    def _release(self, *, job_id=None, execution_id=None):
        with self._guard() as (root_fd, fd, records, lock):
            for record in records.values():
                matches = (
                    record["execution_id"] == execution_id
                    if execution_id
                    else record["job_id"] == job_id
                )
                if matches:
                    record["state"] = "released"
            self._write(root_fd, fd, records, lock)  # Persist release before interrupted cleanup.
            self._cleanup(root_fd, fd, records, lock)

    def _cleanup(self, root_fd, fd, records, lock):
        for name, record in list(records.items())[: self.max_files]:
            if record["state"] != "released":
                continue
            quarantine = record["cleanup"]
            candidate = quarantine or name
            try:
                actual = identity(os.stat(candidate, dir_fd=fd, follow_symlinks=False))
            except FileNotFoundError:
                if quarantine:
                    try:
                        actual = identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                    except FileNotFoundError:
                        actual = None
                    candidate = name
                else:
                    actual = None
            if actual is not None:
                if record["identity"] is None or actual != record["identity"]:
                    raise ValueError("Released ComfyUI input identity changed; reconcile")
                if not quarantine:
                    quarantine = ".cleanup-" + uuid4().hex
                    record["cleanup"] = quarantine
                    self._write(root_fd, fd, records, lock)
                self._current(root_fd, fd, lock)
                if candidate == name:
                    os.rename(name, quarantine, src_dir_fd=fd, dst_dir_fd=fd)
                if (
                    identity(os.stat(quarantine, dir_fd=fd, follow_symlinks=False))
                    != record["identity"]
                ):
                    raise ValueError("ComfyUI input changed during cleanup; quarantine retained")
                os.unlink(quarantine, dir_fd=fd)
                os.fsync(fd)
            del records[name]
            self._write(root_fd, fd, records, lock)
