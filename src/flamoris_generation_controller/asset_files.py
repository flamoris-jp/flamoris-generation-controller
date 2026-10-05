"""Directory-fd based access to Hub-managed job outputs.

Every operation is relative to an opened directory descriptor. A concurrent
rename or symlink replacement of a pathname cannot redirect an operation.
"""

import errno
import os
import stat
import uuid
from pathlib import Path


class AssetFiles:
    def __init__(self, root: Path, job_id: str, *, create: bool = True):
        self.root = root
        self.job_id = job_id
        self.create = create

    def __enter__(self):
        if self.create:
            self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            self.root_fd = os.open(self.root, flags)
        except FileNotFoundError as exc:
            if not self.create:
                raise ValueError("Unknown archived asset ID") from exc
            raise
        try:
            if self.create:
                try:
                    os.mkdir(self.job_id, mode=0o700, dir_fd=self.root_fd)
                except FileExistsError:
                    pass
            try:
                self.fd = os.open(self.job_id, flags, dir_fd=self.root_fd)
            except OSError as exc:
                if exc.errno == errno.ENOENT and not self.create:
                    raise ValueError("Unknown archived asset ID") from exc
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError("Output job directory must not be a symlink") from exc
                raise
        except BaseException:
            os.close(self.root_fd)
            raise
        return self

    def __exit__(self, *_):
        os.close(self.fd)
        os.close(self.root_fd)

    def _current(self):
        """Do not publish into a job directory detached during provider I/O."""
        try:
            current = os.stat(self.job_id, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ValueError("Output job directory changed") from exc
        pinned = os.fstat(self.fd)
        if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise ValueError("Output job directory changed")

    def size(self, name: str) -> int | None:
        self._current()
        try:
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Output file must not be a symlink") from exc
            raise
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Output file must be a regular file, not a symlink")
        return info.st_size

    def read(self, name: str, limit: int) -> bytes | None:
        self._current()
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Output file must not be a symlink") from exc
            raise
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Output file must be a regular file, not a symlink")
            if info.st_size > limit:
                raise ValueError("Asset exceeds retrieval limit")
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) != info.st_size or len(data) > limit:
                raise ValueError("Generated asset changed while being read")
            return bytes(data)
        finally:
            os.close(descriptor)

    def write(self, name: str, data: bytes) -> None:
        self._current()
        self.size(name)  # Reject an existing symlink or special file.
        temporary = f".tmp-{uuid.uuid4().hex}"
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            self._current()
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def delete(self, name: str) -> bool:
        self._current()
        existed = self.size(name) is not None
        try:
            os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            pass
        return existed
