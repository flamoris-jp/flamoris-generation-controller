"""Lifetime exclusion for a single generation authority on a local storage root."""

import fcntl
import os
import stat
from pathlib import Path

from .asset_files import AssetFiles
from .contracts import ControllerError


class Authority:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.files = AssetFiles(self.root, "controller-authority")
        self.fd = None
        self.files.__enter__()
        try:
            self.fd = os.open(
                "owner.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=self.files.fd,
            )
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ControllerError("authority_unavailable")
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ControllerError("authority_busy") from None
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.fd is None:
            raise ControllerError("authority_unavailable")
        try:
            current = os.stat(self.root, follow_symlinks=False)
            pinned = os.fstat(self.files.root_fd)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise ControllerError("authority_unavailable")
            self.files._current()
            current = os.stat("owner.lock", dir_fd=self.files.fd, follow_symlinks=False)
            pinned = os.fstat(self.fd)
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise ControllerError("authority_unavailable")
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise ControllerError("authority_unavailable")
        except (ValueError, OSError):
            raise ControllerError("authority_unavailable") from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.files is not None:
            self.files.__exit__()
            self.files = None
