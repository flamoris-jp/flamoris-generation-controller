import pytest
from flamoris_update_core.errors import UpdateError
from flamoris_update_core.resources import TreeBinding, TreeResource

from flamoris_generation_controller.durable import Records
from flamoris_generation_controller.updater import SCHEMAS, inspect_domain


def tree(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return TreeResource(TreeBinding(id=name, path=str(path), max_files=20, max_bytes=4096))


@pytest.mark.parametrize(
    "record,blocked",
    [
        (None, False),
        ({"active": None}, False),
        ({"active": {"id": "retained-job", "state": "unknown"}}, True),
    ],
)
def test_owner_preserves_retained_reservation(tmp_path, record, blocked):
    resources = {name: tree(tmp_path, name) for name in SCHEMAS}
    (resources["definitions"].root / "retained-definition.json").write_text("{}")
    (resources["inputs"].root / "reference.png").write_bytes(b"retained-reference")
    (resources["recipes"].root / "retained-recipe.json").write_text("{}")
    records = Records(resources["outputs"].root, "job-authority")
    if record is not None:
        records.write("active.json", record)
    before = {name: resource.inventory() for name, resource in resources.items()}
    state = inspect_domain(None, resources)
    assert state.active_work is blocked
    assert state.unknown_work is blocked
    assert {name: resource.inventory() for name, resource in resources.items()} == before
    assert records.read("active.json") == record


def test_owner_requires_all_owned_resources(tmp_path):
    with pytest.raises(UpdateError):
        inspect_domain(None, {"configuration": tree(tmp_path, "configuration")})


def test_shared_models_are_external_not_an_owned_resource(tmp_path):
    resources = {name: tree(tmp_path, name) for name in SCHEMAS}
    resources["models"] = tree(tmp_path, "models")
    with pytest.raises(UpdateError) as error:
        inspect_domain(None, resources)
    assert error.value.code == "invalid_profile"
