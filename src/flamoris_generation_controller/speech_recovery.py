"""Offline operator reconciliation of a settled resident speech request.

This command never queries health, submits speech, adopts audio or declares the
provider successful. The operator must separately establish runtime settlement.
"""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from flamoris_generation_controller.asset_files import AssetFiles
from flamoris_generation_controller.authority import Authority
from flamoris_generation_controller.durable import Records
from flamoris_generation_controller.speech import ReferenceSpeechRecipe, SpeechRecipe


def reconcile(
    root: Path,
    job_id: str,
    execution_id: str,
    evidence_note: str,
    *,
    confirmed_runtime_settled: bool = False,
    apply: bool = False,
) -> dict:
    if any(not re.fullmatch(r"[a-f0-9]{32}", key) for key in (job_id, execution_id)):
        raise ValueError("Expected exact job and provider execution IDs")
    if (
        not isinstance(evidence_note, str)
        or not evidence_note.strip()
        or len(evidence_note.encode("utf-8")) > 2048
        or any(ord(c) < 32 for c in evidence_note)
    ):
        raise ValueError("A bounded, non-secret operator evidence note is required")
    if apply and confirmed_runtime_settled is not True:
        raise ValueError("Confirm actual runtime settlement before applying")
    # The same lifetime lock excludes a live Controller. No second runtime or
    # provider is constructed, and no lock or journal is deleted.
    owner = Authority(root)
    try:
        records = Records(owner.root, "job-authority")
        receipt_name = "reconciled-" + job_id + ".json"
        receipt = records.read(receipt_name)
        record = records.read("active.json")
        identity = {"job_id": job_id, "provider_execution_id": execution_id}
        if receipt is not None:
            original = receipt.get("original_reservation")
            original_active = original.get("active") if isinstance(original, dict) else None
            if (
                receipt.get("version") != 1
                or receipt.get("decision") != "runtime_settled_output_unavailable"
                or receipt.get("runtime_settled_confirmed") is not True
                or any(receipt.get(k) != v for k, v in identity.items())
                or not isinstance(original_active, dict)
                or (original_active.get("job_id"), original_active.get("execution_id"))
                != (job_id, execution_id)
            ):
                raise ValueError("Invalid reconciliation receipt")
        if record == {"active": None} and receipt is not None:
            return {**identity, "released": True, "already_reconciled": True}
        raw = record.get("active") if isinstance(record, dict) else None
        if not isinstance(raw, dict) or (
            raw.get("job_id"),
            raw.get("execution_id"),
            raw.get("provider_id"),
            raw.get("operation"),
        ) != (job_id, execution_id, "irodori", "speech.generate"):
            raise ValueError("Active reservation does not match the settled speech request")
        if any(k in raw for k in ("runtime_delegation", "definition", "verification")):
            raise ValueError("Retired execution cannot be reconciled by this command")
        recipe = raw.get("recipe")
        if not isinstance(recipe, dict):
            raise ValueError("Invalid resident speech recipe")
        if recipe.get("template") == "speech-reference":
            ReferenceSpeechRecipe.model_validate(recipe)
        else:
            SpeechRecipe.model_validate(recipe)
        # Distinguish the resident HTTP adapter from CLI speech/other providers.
        with AssetFiles(owner.root / "irodori-http-staging", execution_id, create=False):
            pass
        if receipt is not None and (
            any(receipt.get(k) != v for k, v in identity.items())
            or receipt.get("original_reservation") != record
            or receipt.get("decision") != "runtime_settled_output_unavailable"
        ):
            raise ValueError("Existing reconciliation receipt differs")
        result = {**identity, "released": False, "dry_run": not apply}
        if not apply:
            return result
        owner.check()
        if receipt is None:
            records.write(
                receipt_name,
                {
                    "version": 1,
                    **identity,
                    "decision": "runtime_settled_output_unavailable",
                    "runtime_settled_confirmed": True,
                    "evidence_note": evidence_note,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "original_reservation": record,
                },
            )
        # Audit must be durable before releasing capacity. If either commit is
        # uncertain, stop; a later explicit invocation validates the receipt.
        owner.check()
        records.write("active.json", {"active": None})
        return {**result, "released": True}
    finally:
        owner.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--evidence-note", required=True)
    parser.add_argument("--confirmed-runtime-settled", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = reconcile(
            args.output_root,
            args.job_id,
            args.execution_id,
            args.evidence_note,
            confirmed_runtime_settled=args.confirmed_runtime_settled,
            apply=args.apply,
        )
    except (ValueError, OSError, RuntimeError):
        # Private paths, original recipes and provider details stay in storage.
        parser.exit(1, "Reconciliation refused; verify stopped owner, exact IDs and evidence.\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
