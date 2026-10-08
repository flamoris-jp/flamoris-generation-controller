"""Read retained reservations without constructing another runtime authority."""

from flamoris_update_core.domain import check_resources, configuration_revision
from flamoris_update_core.owner import ApplicationOwner, DomainState
from flamoris_update_core.owner_cli import serve

from .durable import Records

SCHEMAS = {
    "configuration": "controller-config-1",
    "recipes": "native-recipes-1",
    "outputs": "generation-assets-1",
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
