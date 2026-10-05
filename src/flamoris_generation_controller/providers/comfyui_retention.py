"""Opt-in local ComfyUI output maintenance, separate from asset deletion/HTTP."""

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def output_directory(root: Path):
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("Provider output root must be an absolute directory")
    fd = os.open("/", FLAGS)
    try:
        for part in (*root.parts[1:], "flamoris"):
            next_fd = os.open(part, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def identity(info) -> dict:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Provider output must be a single-link regular file")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def checked_filename(job_id: str, name: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id) or not re.fullmatch(
        re.escape(job_id) + r"_[0-9]+_\.(png|jpg|jpeg|webp)", name
    ):
        raise ValueError("Output is not in the managed ComfyUI filename namespace")
    return name


class ComfyUIRetention:
    def __init__(self, root: Path, outputs: dict | None = None):
        self.root = root
        self.outputs = outputs if outputs is not None else {}

    def capture(self, execution_id: str, job_id: str) -> list[dict]:
        records = []
        with output_directory(self.root) as fd:
            for index in range(64):
                output = self.outputs.get((execution_id, f"{index:03d}"))
                if output is None:
                    continue
                if output.get("subfolder") != "flamoris" or output.get("type") != "output":
                    raise ValueError("Unsupported provider output location")
                name = checked_filename(job_id, output["filename"])
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                records.append({"index": int(index), "filename": name, "identity": identity(info)})
                if len(records) > 64:
                    raise ValueError("Too many provider outputs")
        return records

    def remove(self, receipt: dict, *, dry_run: bool) -> tuple[str, int]:
        name = checked_filename(receipt["job_id"], receipt["filename"])
        expected = receipt["identity"]
        if not isinstance(expected, dict) or set(expected) != {
            "device",
            "inode",
            "size",
            "mtime_ns",
        }:
            raise ValueError("Invalid output identity")
        if any(type(v) is not int or v < 0 for v in expected.values()):
            raise ValueError("Invalid output identity")
        with output_directory(self.root) as fd:
            try:
                actual = identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
            except FileNotFoundError:
                return "missing", 0
            if actual != expected:
                raise ValueError("Provider output identity changed")
            if dry_run:
                return "eligible", expected["size"]
            # Pin the candidate under a random name before final identity check.
            # A concurrently replaced candidate is never deleted.
            quarantine = ".cleanup-" + uuid4().hex
            os.rename(name, quarantine, src_dir_fd=fd, dst_dir_fd=fd)
            try:
                if identity(os.stat(quarantine, dir_fd=fd, follow_symlinks=False)) != expected:
                    raise ValueError("Provider output changed during cleanup")
                os.unlink(quarantine, dir_fd=fd)
            except BaseException:
                # Restore without overwriting a new original. If it was replaced,
                # leave the quarantined entry for manual recovery, never delete it.
                try:
                    os.link(quarantine, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                    os.unlink(quarantine, dir_fd=fd)
                except OSError:
                    pass
                raise
            return "deleted", expected["size"]
