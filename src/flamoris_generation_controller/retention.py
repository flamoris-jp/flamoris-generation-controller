"""Explicit bounded maintenance for provider outputs with recorded identities."""

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Protocol

from .asset_files import AssetFiles
from .config import Settings

LOG = logging.getLogger(__name__)
RECEIPT = ".provider-retention.json"
MAX_RECORD_BYTES = 64 * 1024


class OutputRetention(Protocol):
    def capture(self, execution_id: str, job_id: str) -> list[dict]: ...

    def remove(self, receipt: dict, *, dry_run: bool) -> tuple[str, int]: ...


class RetentionStore:
    def __init__(self, root: Path, providers: dict[str, OutputRetention]):
        self.root = root
        self.providers = providers

    def record(self, provider_id: str, execution_id: str, job_id: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return
        with AssetFiles(self.root, job_id) as files:
            if files.read(RECEIPT, MAX_RECORD_BYTES) is not None:
                return  # Never recapture a changed file identity on later polls.
            outputs = provider.capture(execution_id, job_id)
            if not outputs:
                return
            record = {
                "version": 1,
                "provider": provider_id,
                "job_id": job_id,
                "captured_at": time.time(),
                "outputs": outputs,
            }
            payload = json.dumps(record).encode()
            if len(payload) > MAX_RECORD_BYTES:
                raise ValueError("Provider retention receipt exceeds limit")
            files.write(RECEIPT, payload)

    def cleanup(
        self,
        *,
        age_seconds: float,
        dry_run: bool = True,
        deleted_only: bool = False,
        max_jobs: int = 100,
        job_ids: list[str] | None = None,
    ) -> dict:
        if not 0 <= age_seconds <= 36500 * 86400 or not 1 <= max_jobs <= 1000:
            raise ValueError("Invalid retention age or scan limit")
        if job_ids is not None and (
            len(job_ids) > max_jobs
            or any(not re.fullmatch(r"[a-f0-9]{32}", key) for key in job_ids)
        ):
            raise ValueError("Invalid bounded job selection")
        summary = {
            "dry_run": dry_run,
            "examined_jobs": 0,
            "scan_limit_reached": False,
            "eligible_files": 0,
            "deleted_files": 0,
            "reclaimed_bytes": 0,
            "missing_files": 0,
            "failures": 0,
        }
        if not self.root.exists():
            return summary
        # Bound directory enumeration itself, not just the number of deletions.
        if job_ids is None:
            selected = []
            with os.scandir(self.root) as entries:
                for count, entry in enumerate(entries):
                    if count >= max_jobs:
                        summary["scan_limit_reached"] = True
                        break
                    if re.fullmatch(r"[a-f0-9]{32}", entry.name):
                        selected.append(entry.name)
        else:
            selected = list(dict.fromkeys(job_ids))
        for job_id in selected:
            summary["examined_jobs"] += 1
            try:
                with AssetFiles(self.root, job_id, create=False) as files:
                    raw = files.read(RECEIPT, MAX_RECORD_BYTES)
                    if raw is None:
                        continue
                    record = json.loads(raw)
                    if (
                        record.get("version") != 1
                        or record.get("job_id") != job_id
                        or type(record.get("captured_at")) not in (int, float)
                        or not 0 <= record["captured_at"] <= time.time()
                        or not isinstance(record.get("outputs"), list)
                        or not 1 <= len(record["outputs"]) <= 64
                    ):
                        raise ValueError("Invalid retention receipt")
                    if time.time() - record["captured_at"] < age_seconds:
                        continue
                    provider = self.providers.get(record.get("provider"))
                    if provider is None:
                        raise ValueError("Unsupported retention provider")
                    raw_deleted = files.read(".deleted-assets.json", 4096)
                    deleted = json.loads(raw_deleted) if raw_deleted else []
                    if not isinstance(deleted, list) or any(type(x) is not int for x in deleted):
                        raise ValueError("Invalid deletion metadata")
                    for item in record["outputs"]:
                        try:
                            if (
                                not isinstance(item, dict)
                                or type(item.get("index")) is not int
                                or not 0 <= item["index"] < 64
                            ):
                                raise ValueError("Invalid receipt output")
                            if deleted_only and item["index"] not in deleted:
                                continue
                            # Provider receives job identity from the enclosing trusted record.
                            receipt = {**item, "job_id": job_id}
                            outcome, size = provider.remove(receipt, dry_run=dry_run)
                            if outcome == "missing":
                                summary["missing_files"] += 1
                            else:
                                summary["eligible_files"] += 1
                                if outcome == "deleted":
                                    summary["deleted_files"] += 1
                                    summary["reclaimed_bytes"] += size
                            LOG.info(
                                "provider cleanup job=%s output=%s outcome=%s",
                                job_id,
                                item["index"],
                                outcome,
                            )
                        except (ValueError, OSError, TypeError, KeyError):
                            summary["failures"] += 1
                            LOG.warning("provider cleanup refused job=%s output", job_id)
            except (ValueError, OSError, TypeError, KeyError, AttributeError):
                summary["failures"] += 1
                LOG.warning("provider cleanup refused job=%s", job_id)
        return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Preview or execute proven provider-output cleanup"
    )
    parser.add_argument("--execute", action="store_true", help="Delete eligible originals (opt-in)")
    parser.add_argument("--deleted-only", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=100)
    parser.add_argument("--job-id", action="append", dest="job_ids")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.execute and not settings.provider_cleanup_enabled:
        parser.error("Execution requires FLAMORIS_PROVIDER_CLEANUP_ENABLED=true")
    if settings.comfyui_output_root is None:
        parser.error("FLAMORIS_COMFYUI_OUTPUT_ROOT must identify the provider output root")
    from .providers.comfyui_retention import ComfyUIRetention

    maintenance = RetentionStore(
        settings.output_dir,
        {
            "comfyui": ComfyUIRetention(settings.comfyui_output_root),
        },
    )
    logging.basicConfig(level=logging.INFO)
    try:
        result = maintenance.cleanup(
            age_seconds=settings.provider_retention_days * 86400,
            dry_run=not args.execute,
            deleted_only=args.deleted_only,
            max_jobs=args.max_jobs,
            job_ids=args.job_ids,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
