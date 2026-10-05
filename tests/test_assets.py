import pytest

from flamoris_generation_controller.jobs import JobStore
from flamoris_generation_controller.workflows import Parameters


async def submit(stores):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image",
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers", seed=42),
    )
    submitted = await jobs.submit(workflow["workflow_id"])
    return submitted["job_id"]


async def test_list_and_get_completed_png_asset(stores, fake):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()

    listing = await jobs.list_assets(job_id)
    assert listing["job_id"] == job_id
    assert listing["assets"] == [
        {
            "asset_id": f"{job_id}:000",
            "job_id": job_id,
            "filename": "000.png",
            "media_kind": "image",
            "mime_type": "image/png",
            "size_bytes": None,
            "materialized": False,
            "output_index": 0,
        }
    ]
    assert fake.download_count == 0

    metadata, data, media_format = await jobs.get_asset(f"{job_id}:000")
    assert metadata == {
        **listing["assets"][0],
        "size_bytes": len(b"image fixture"),
        "materialized": True,
    }
    assert data == b"image fixture"
    assert media_format == "png"
    assert fake.download_count == 1
    assert (await jobs.list_assets(job_id))["assets"] == [metadata]


async def test_multiple_outputs_have_stable_asset_ids(stores, fake):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    fake.history["prompt-1"]["outputs"]["7"]["images"].append(
        {"filename": "second.webp", "subfolder": "flamoris", "type": "output"}
    )

    listing = await jobs.list_assets(job_id)
    assert [asset["asset_id"] for asset in listing["assets"]] == [
        f"{job_id}:000",
        f"{job_id}:001",
    ]
    assert [asset["filename"] for asset in listing["assets"]] == ["000.png", "001.webp"]
    assert [asset["mime_type"] for asset in listing["assets"]] == ["image/png", "image/webp"]
    assert [asset["materialized"] for asset in listing["assets"]] == [False, False]
    assert [asset["size_bytes"] for asset in listing["assets"]] == [None, None]
    assert fake.download_count == 0


async def test_assets_reject_unknown_unfinished_and_unknown_asset(stores, fake):
    _, jobs, _ = stores
    job_id = await submit(stores)

    with pytest.raises(ValueError, match="completed"):
        await jobs.list_assets(job_id)
    with pytest.raises(ValueError, match="Unknown"):
        await jobs.list_assets("0" * 32)

    fake.finish()
    with pytest.raises(ValueError, match="Unknown asset"):
        await jobs.get_asset(f"{job_id}:999")
    with pytest.raises(ValueError, match="assets.list"):
        await jobs.get_asset("../etc/passwd")


async def test_asset_access_rejects_local_symlink_escape(stores, fake, settings, tmp_path):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)

    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    local = settings.output_dir / job_id / "000.png"
    local.unlink()
    local.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        await jobs.list_assets(job_id)
    with pytest.raises(ValueError, match="symlink"):
        await jobs.get_asset(f"{job_id}:000")


async def test_asset_retrieval_size_is_bounded(stores, fake, monkeypatch):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)
    monkeypatch.setattr("flamoris_generation_controller.jobs.MAX_ASSET_BYTES", 4)

    listing = await jobs.list_assets(job_id)
    assert listing["assets"][0]["materialized"] is True
    assert listing["assets"][0]["size_bytes"] > 4
    with pytest.raises(ValueError, match="retrieval limit"):
        await jobs.get_asset(f"{job_id}:000")


async def test_get_asset_does_not_materialize_unrelated_outputs(stores, fake, settings):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    fake.history["prompt-1"]["outputs"]["7"]["images"].append(
        {"filename": "second.png", "subfolder": "flamoris", "type": "output"}
    )
    listing = await jobs.list_assets(job_id)
    assert fake.download_count == 0
    assert all(not asset["materialized"] for asset in listing["assets"])

    metadata, data, media_format = await jobs.get_asset(f"{job_id}:000")

    assert metadata["output_index"] == 0
    assert metadata["materialized"] is True
    assert data == b"image fixture"
    assert media_format == "png"
    assert fake.download_count == 1
    assert not (settings.output_dir / job_id / "001.png").exists()


async def test_delete_one_of_multiple_assets_does_not_rematerialize(stores, fake, settings):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    fake.history["prompt-1"]["outputs"]["7"]["images"].append(
        {"filename": "second.png", "subfolder": "flamoris", "type": "output"}
    )
    await jobs.result(job_id)
    first = settings.output_dir / job_id / "000.png"
    second = settings.output_dir / job_id / "001.png"
    assert first.is_file() and second.is_file()

    deleted = await jobs.delete_asset(f"{job_id}:000")
    assert deleted["materialized_deleted"] is True
    assert not first.exists() and second.exists()
    assert (await jobs.delete_asset(f"{job_id}:000"))["already_deleted"] is True
    assert [item["output_index"] for item in (await jobs.list_assets(job_id))["assets"]] == [1]
    with pytest.raises(ValueError, match="Unknown asset"):
        await jobs.get_asset(f"{job_id}:000")
    await jobs.result(job_id)
    assert not first.exists()
    assert (await jobs.get_asset(f"{job_id}:001"))[1] == b"image fixture"


async def test_delete_unmaterialized_asset_and_reject_invalid_paths(stores, fake, settings):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    assert (await jobs.delete_asset(f"{job_id}:000"))["materialized_deleted"] is False
    assert (settings.output_dir / job_id / ".deleted-assets.json").read_text() == "[0]"
    assert (await jobs.list_assets(job_id))["assets"] == []
    with pytest.raises(ValueError, match="assets.list"):
        await jobs.delete_asset("../../etc/passwd")


async def test_delete_rejects_symlink_escape(stores, fake, settings, tmp_path):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    local = settings.output_dir / job_id / "000.png"
    local.unlink()
    local.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        await jobs.delete_asset(f"{job_id}:000")
    assert outside.read_bytes() == b"private"


async def test_archived_asset_can_be_read_and_deleted_after_restart(stores, fake, settings):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)
    archived = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    asset_id = f"{job_id}:000"
    assert (await archived.get_asset(asset_id))[1] == b"image fixture"
    response = await archived.delete_asset(asset_id)
    assert response["deleted"] is True and response["materialized_deleted"] is True
    assert (await archived.delete_asset(asset_id))["already_deleted"] is True
    with pytest.raises(ValueError, match="Unknown archived asset"):
        await archived.get_asset(asset_id)
    assert not (settings.output_dir / job_id / "000.png").exists()


async def test_archived_assets_reject_unrecorded_and_symlinks(stores, fake, settings, tmp_path):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)
    archived = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    outside = tmp_path / "private.png"
    outside.write_bytes(b"private")
    local = settings.output_dir / job_id / "000.png"
    local.unlink()
    local.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        await archived.get_asset(f"{job_id}:000")
    with pytest.raises(ValueError, match="symlink"):
        await archived.delete_asset(f"{job_id}:000")
    assert outside.read_bytes() == b"private"
    with pytest.raises(ValueError, match="Unknown archived asset"):
        await archived.delete_asset(f"{job_id}:999")


async def test_archived_history_has_no_1024_asset_limit(stores, fake, settings):
    _, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.result(job_id)
    archived = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    first_lock = archived._archived_lock(job_id)
    for index in range(2048):
        archived._archived_lock(f"{index:032x}")
    assert len(archived._archived_locks) == 64
    assert archived._archived_lock(job_id) is first_lock
    assert (await archived.get_asset(f"{job_id}:000"))[1] == b"image fixture"
    assert (await archived.delete_asset(f"{job_id}:000"))["deleted"]


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_directory_swap_during_asset_io_cannot_escape(tmp_path, monkeypatch, operation):
    from flamoris_generation_controller.asset_files import AssetFiles

    root = tmp_path / "outputs"
    job_id = "a" * 32
    original = root / job_id
    original.mkdir(parents=True)
    (original / "000.png").write_bytes(b"managed")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "000.png").write_bytes(b"private")
    real_current = AssetFiles._current
    swapped = False

    def swap_after_validation(files):
        nonlocal swapped
        real_current(files)
        if not swapped:
            swapped = True
            original.rename(root / "detached")
            original.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(AssetFiles, "_current", swap_after_validation)
    with AssetFiles(root, job_id) as files:
        try:
            if operation == "read":
                assert files.read("000.png", 100) == b"managed"
            elif operation == "write":
                files.write("000.png", b"new managed")
            else:
                files.delete("000.png")
        except ValueError as exc:
            assert "directory changed" in str(exc)
    assert swapped
    assert (outside / "000.png").read_bytes() == b"private"


async def test_metadata_catalog_and_selected_get_survive_restart_without_result(
    stores, fake, settings
):
    workflows, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    fake.history["prompt-1"]["outputs"]["7"]["images"].append(
        {"filename": "second.png", "subfolder": "flamoris", "type": "output"}
    )
    listed = await jobs.list_assets(job_id)
    assert fake.download_count == 0
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    assert await restarted.list_assets(job_id) == listed
    with pytest.raises(ValueError, match="Unknown archived"):
        await restarted.get_asset(f"{job_id}:001")
    await jobs.get_asset(f"{job_id}:000")
    assert fake.download_count == 1
    assert (await restarted.get_asset(f"{job_id}:000"))[1] == b"image fixture"
    again = await restarted.list_assets(job_id)
    assert [a["materialized"] for a in again["assets"]] == [True, False]
    await restarted.delete_asset(f"{job_id}:001")
    assert [a["asset_id"] for a in (await restarted.list_assets(job_id))["assets"]] == [
        f"{job_id}:000"
    ]
    assert fake.download_count == 1


async def test_catalog_archive_rejects_malformed_and_symlink(stores, fake, settings, tmp_path):
    import json

    workflows, jobs, _ = stores
    job_id = await submit(stores)
    fake.finish()
    await jobs.list_assets(job_id)
    manifest = settings.output_dir / job_id / "metadata.json"
    record = json.loads(manifest.read_text())
    record["job_id"] = "b" * 32
    manifest.write_text(json.dumps(record))
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    with pytest.raises(ValueError, match="Invalid archived"):
        await restarted.list_assets(job_id)
    manifest.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(record))
    manifest.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        await restarted.list_assets(job_id)
