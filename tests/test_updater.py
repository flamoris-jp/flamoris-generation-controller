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
    records = Records(resources["outputs"].root, "job-authority")
    if record is not None:
        records.write("active.json", record)
    before = resources["outputs"].inventory()
    state = inspect_domain(None, resources)
    assert state.active_work is blocked
    assert state.unknown_work is blocked
    assert resources["outputs"].inventory() == before
    assert records.read("active.json") == record


def test_owner_requires_all_owned_resources(tmp_path):
    with pytest.raises(UpdateError):
        inspect_domain(None, {"configuration": tree(tmp_path, "configuration")})
