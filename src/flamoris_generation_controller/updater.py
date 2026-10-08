"""Read retained reservations without constructing another runtime authority."""

from flamoris_update_core.domain import check_resources, configuration_revision
from flamoris_update_core.owner import ApplicationOwner, DomainState
from flamoris_update_core.owner_cli import serve

from .durable import Records

SCHEMAS = {
    "configuration": "controller-config-1",
    "definitions": "comfy-definitions-retained-1",
    "inputs": "provider-inputs-1",
    "recipes": "generation-recipes-1",
    # Generated assets, managed input snapshots, upload state, reservations,
    # journals and the authority lock all live below the Controller output root.
    "outputs": "generation-state-2",
}


def inspect_domain(config, resources):
    check_resources(resources, SCHEMAS)
    root = resources["outputs"].root
    active = Records(root, "job-authority").read("active.json")
    uncertain = active is not None and active != {"active": None}
    return DomainState(
        schemas=SCHEMAS,
        active_work=uncertain,
        unknown_work=uncertain,
        configuration_digest=configuration_revision(resources),
    )


def factory(config):
    return ApplicationOwner(config, "flamoris-generation-controller", "1.0.0", inspect_domain)


def main():
    serve(factory)
