import json
import os

import pytest

from flamoris_generation_controller.providers.comfyui_retention import ComfyUIRetention
from flamoris_generation_controller.retention import RECEIPT, RetentionStore, main

JOB = "a" * 32


@pytest.fixture
def retention(tmp_path):
    provider_root = tmp_path / "provider"
    (provider_root / "flamoris").mkdir(parents=True)
    output = provider_root / "flamoris" / f"{JOB}_00001_.png"
    output.write_bytes(b"owned output")
    outputs = {
        ("execution", "000"): {"filename": output.name, "subfolder": "flamoris", "type": "output"}
    }
    provider = ComfyUIRetention(provider_root, outputs)
    store = RetentionStore(tmp_path / "managed", {"comfyui": provider})
    store.record("comfyui", "execution", JOB)
    return store, provider, output


def test_preview_age_execute_and_idempotent_retry(retention):
    store, _, output = retention
    assert store.cleanup(age_seconds=86400)["eligible_files"] == 0
    preview = store.cleanup(age_seconds=0)
    assert preview["eligible_files"] == 1 and preview["deleted_files"] == 0
    assert output.exists()
    removed = store.cleanup(age_seconds=0, dry_run=False)
    assert removed["deleted_files"] == 1
    assert removed["reclaimed_bytes"] == len(b"owned output")
    assert not output.exists()
    assert store.cleanup(age_seconds=0, dry_run=False)["missing_files"] == 1


def test_deleted_only_and_unrelated_files(retention):
    store, _, output = retention
    unrelated = output.parent / "manual.png"
    unrelated.write_bytes(b"keep")
    assert store.cleanup(age_seconds=0, deleted_only=True)["eligible_files"] == 0
    (store.root / JOB / ".deleted-assets.json").write_text("[0]")
    assert store.cleanup(age_seconds=0, dry_run=False, deleted_only=True)["deleted_files"] == 1
    assert unrelated.read_bytes() == b"keep"


@pytest.mark.parametrize("change", ["replace", "symlink", "hardlink"])
def test_changed_identity_is_never_deleted(retention, tmp_path, change):
    store, provider, output = retention
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"unrelated")
    if change == "replace":
        replacement = output.with_suffix(".new")
        replacement.write_bytes(b"replacement")
        replacement.replace(output)
    elif change == "symlink":
        output.unlink()
        output.symlink_to(outside)
    else:
        os.link(output, tmp_path / "linked.png")
    store.record("comfyui", "execution", JOB)  # Never recaptures a replacement.
    result = store.cleanup(age_seconds=0, dry_run=False)
    assert result["failures"] == 1 and result["deleted_files"] == 0
    assert output.exists() and outside.read_bytes() == b"unrelated"


def test_traversal_and_wrong_namespace_receipts_fail_closed(retention):
    store, _, output = retention
    path = store.root / JOB / RECEIPT
    record = json.loads(path.read_text())
    record["outputs"][0]["filename"] = "../../other.png"
    path.write_text(json.dumps(record))
    assert store.cleanup(age_seconds=0, dry_run=False)["failures"] == 1
    assert output.exists()


def test_provider_root_symlink_is_rejected(retention, tmp_path):
    store, provider, output = retention
    link = tmp_path / "linked-root"
    link.symlink_to(provider.root, target_is_directory=True)
    provider.root = link
    assert store.cleanup(age_seconds=0, dry_run=False)["failures"] == 1
    assert output.exists()


def test_bounded_scan_and_targeted_selection(retention):
    store, _, output = retention
    for n in range(5):
        (store.root / f"{n:032x}").mkdir()
    result = store.cleanup(age_seconds=0, max_jobs=2)
    assert result["examined_jobs"] <= 2 and result["scan_limit_reached"]
    assert store.cleanup(age_seconds=0, job_ids=[JOB])["eligible_files"] == 1
    assert output.exists()
    with pytest.raises(ValueError):
        store.cleanup(age_seconds=0, job_ids=["../escape"])


def test_concurrent_replacement_is_restored_without_deletion(retention, monkeypatch):
    store, _, output = retention
    real_rename = os.rename

    def race(source, destination, **kwargs):
        if source == output.name:
            output.unlink()
            output.write_bytes(b"new unrelated output")
        return real_rename(source, destination, **kwargs)

    monkeypatch.setattr(os, "rename", race)
    result = store.cleanup(age_seconds=0, dry_run=False)
    assert result["deleted_files"] == 0 and result["failures"] == 1
    assert output.read_bytes() == b"new unrelated output"


def test_cli_requires_explicit_opt_in(retention, monkeypatch, capsys):
    store, provider, output = retention
    monkeypatch.setenv("FLAMORIS_OUTPUT_DIR", str(store.root))
    monkeypatch.setenv("FLAMORIS_COMFYUI_OUTPUT_ROOT", str(provider.root))
    monkeypatch.setenv("FLAMORIS_PROVIDER_RETENTION_DAYS", "0")
    monkeypatch.setenv("FLAMORIS_PROVIDER_CLEANUP_ENABLED", "false")
    main([])
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    with pytest.raises(SystemExit):
        main(["--execute"])
    assert output.exists()
    monkeypatch.setenv("FLAMORIS_PROVIDER_CLEANUP_ENABLED", "true")
    main(["--execute", "--job-id", JOB])
    assert not output.exists()


async def test_completed_job_records_receipt_without_deleting_provider(
    stores, fake, settings, tmp_path
):
    workflows, jobs, client = stores
    root = tmp_path / "provider"
    (root / "flamoris").mkdir(parents=True)
    settings.comfyui_output_root = root
    provider = jobs.providers.get("comfyui")
    jobs.retention = RetentionStore(settings.output_dir, {"comfyui": provider.retention()})
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )
    key = (await jobs.submit(workflow["workflow_id"]))["job_id"]
    fake.finish()
    output = root / "flamoris" / f"{key}_00001_.png"
    output.write_bytes(b"image fixture")
    fake.history["prompt-1"]["outputs"]["7"]["images"][0]["filename"] = output.name
    await jobs.list_assets(key)
    assert (settings.output_dir / key / RECEIPT).is_file()
    assert fake.download_count == 0
    await jobs.delete_asset(f"{key}:000")
    assert output.exists()
    assert jobs.retention.cleanup(age_seconds=0, deleted_only=True)["eligible_files"] == 1
