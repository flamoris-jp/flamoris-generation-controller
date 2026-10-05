import json

import pytest
from test_provider_jobs import make_store

from flamoris_generation_controller.jobs import JobStore
from flamoris_generation_controller.providers import JobSnapshot, OutputRole, ProviderOutput
from flamoris_generation_controller.providers.base import output_roles
from flamoris_generation_controller.transfers import AssetTransfers


def output(role=None):
    return ProviderOutput("image-0", "provider-name.png", "image", "image/png", role)


@pytest.mark.parametrize(
    "args",
    [
        ("../path", "preview", 0),
        ("image", "Primary", 0),
        ("image", "preview", True),
        ("image", "preview", -1),
        ("image", "preview", 128),
        ("x" * 65, "preview", 0),
    ],
)
def test_output_role_rejects_unsafe_identity(args):
    with pytest.raises(ValueError, match="output"):
        OutputRole(*args)


def test_legacy_output_remains_unclassified_and_collection_indices_are_explicit():
    assert output().as_dict() == {
        "output_id": "image-0",
        "filename": "provider-name.png",
        "media_kind": "image",
        "mime_type": "image/png",
    }
    roles = [output(OutputRole("frames", "frame", i)).as_dict() for i in (0, 1)]
    assert output_roles(roles) == [
        {"port": "frames", "role": "frame", "role_index": i} for i in (0, 1)
    ]
    assert output_roles([output().as_dict()]) == [{}]


@pytest.mark.parametrize(
    "roles",
    [
        [{"role": "preview"}],
        [OutputRole("image", "preview").as_dict()] * 2,
        [OutputRole("image", "preview").as_dict(), OutputRole("image", "primary").as_dict()],
        [OutputRole("image", "preview").as_dict(), OutputRole("other", "preview", 1).as_dict()],
        [OutputRole("image", "preview").as_dict()] * 65,
    ],
)
def test_incomplete_duplicate_conflicting_or_oversized_roles_reject(roles):
    with pytest.raises(ValueError, match="[Oo]utput"):
        output_roles(roles)


async def completed(settings, role):
    workflows, provider, _, jobs, workflow_id = make_store(settings)

    async def stream_output(execution_id, output_id):
        yield await provider.materialize(execution_id, output_id)

    provider.stream_output = stream_output
    job_id = (await jobs.submit(workflow_id))["job_id"]
    provider.snapshots["execution-1"] = JobSnapshot(status="completed", outputs=(output(role),))
    return workflows, provider, jobs, job_id


async def test_roles_survive_lazy_catalog_get_transfer_restart_and_tombstones(settings):
    workflows, provider, jobs, job_id = await completed(settings, OutputRole("image", "preview"))
    listed = (await jobs.list_assets(job_id))["assets"][0]
    role = {"port": "image", "role": "preview", "role_index": 0}
    assert {k: listed[k] for k in role} == role
    assert not listed["materialized"]
    assert (await jobs.status(job_id))["outputs"][0]["role"] == "preview"
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    assert (await restarted.list_assets(job_id))["assets"] == [listed]
    asset_id = listed["asset_id"]
    prepared = await AssetTransfers(jobs).prepare(asset_id)
    assert {k: prepared[k] for k in role} == role
    archive_prepared = await AssetTransfers(restarted).prepare(asset_id)
    assert {k: archive_prepared[k] for k in role} == role
    live_metadata, data, _ = await jobs.get_asset(asset_id)
    archive_metadata, archive_data, _ = await restarted.get_asset(asset_id)
    assert live_metadata == archive_metadata
    assert data == archive_data == b"provider-neutral image"
    await restarted.delete_asset(asset_id)
    assert (await restarted.list_assets(job_id))["assets"] == []
    with pytest.raises(ValueError, match="Unknown"):
        await AssetTransfers(restarted).prepare(asset_id)


async def test_invalid_provider_role_set_is_not_published_or_materialized(settings):
    _, provider, jobs, job_id = await completed(settings, OutputRole("image", "preview"))
    provider.snapshots["execution-1"] = JobSnapshot(
        status="completed", outputs=(output(OutputRole("image", "preview")),) * 2
    )
    with pytest.raises(ValueError, match="Duplicate"):
        await jobs.list_assets(job_id)
    assert not (settings.output_dir / job_id / "metadata.json").exists()
    assert not (settings.output_dir / job_id / "000.png").exists()


async def test_invalid_archived_role_fails_catalog_get_and_transfer(settings):
    workflows, _, jobs, job_id = await completed(settings, OutputRole("image", "preview"))
    asset_id = f"{job_id}:000"
    await jobs.get_asset(asset_id)
    path = settings.output_dir / job_id / "metadata.json"
    record = json.loads(path.read_text())
    record["outputs"][0]["role_index"] = True
    path.write_text(json.dumps(record))
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    for operation in (
        restarted.list_assets(job_id),
        restarted.get_asset(asset_id),
        AssetTransfers(restarted).prepare(asset_id),
    ):
        with pytest.raises(ValueError, match="role manifest"):
            await operation


async def test_old_archived_outputs_are_not_given_fabricated_roles(settings):
    workflows, _, jobs, job_id = await completed(settings, None)
    asset_id = f"{job_id}:000"
    live, _, _ = await jobs.get_asset(asset_id)
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    archive, _, _ = await restarted.get_asset(asset_id)
    assert live == archive
    assert not {"port", "role", "role_index"} & archive.keys()
