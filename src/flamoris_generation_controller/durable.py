"""Bounded private records with definite/uncertain atomic commit outcomes."""

import json
import os
from uuid import uuid4

from .asset_files import AssetFiles


class CommitUnknown(OSError):
    """Publication occurred but directory durability was not confirmed."""


class Records:
    def __init__(self, root, namespace, limit=1024 * 1024):
        self.root = root
        self.namespace = namespace
        self.limit = limit

    def read(self, name):
        try:
            with AssetFiles(self.root, self.namespace, create=False) as files:
                raw = files.read(name, self.limit)
        except ValueError as exc:
            if str(exc) == "Unknown archived asset ID":
                return None
            raise
        if raw is None:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid durable record")
        return data

    def write(self, name, data):
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > self.limit:
            raise ValueError("Durable record exceeds size limit")
        with AssetFiles(self.root, self.namespace) as files:
            files.size(name)
            temp = ".tmp-" + uuid4().hex
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=files.fd
            )
            published = False
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                files._current()
                os.replace(temp, name, src_dir_fd=files.fd, dst_dir_fd=files.fd)
                published = True
                os.fsync(files.fd)
                os.fsync(files.root_fd)
            except OSError as exc:
                if published:
                    raise CommitUnknown("Durable record commit outcome is uncertain") from exc
                raise
            finally:
                try:
                    os.unlink(temp, dir_fd=files.fd)
                except FileNotFoundError:
                    pass
