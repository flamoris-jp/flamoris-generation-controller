from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flamoris_generation_controller.capabilities import Capability, CapabilityRegistry


def test_capability_identity_is_independent_from_provider_and_runtime():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )

    capability = registry.get("image.generate", {"comfyui": True})

    assert capability["id"] == "image.generate"
    assert capability["provider_id"] == "comfyui"
    assert capability["runtime_id"] == "janku"
    assert capability["available"] is True
    assert registry.resolve_workflow("text-to-image").capability_id == "image.generate"


def test_capability_availability_defaults_false_and_unknown_is_rejected():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )

    assert registry.list({})[0]["available"] is False
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.get("music.generate", {})


@pytest.mark.parametrize("capability_id", ["image", "Image.generate", "image/generate"])
def test_invalid_capability_identity_is_rejected(capability_id):
    with pytest.raises(ValueError, match="Capability ID"):
        CapabilityRegistry(
            (
                Capability(
                    capability_id=capability_id,
                    provider_id="comfyui",
                    runtime_id="janku",
                    workflow_templates=("text-to-image",),
                ),
            )
        )


def test_workflow_template_cannot_route_to_multiple_capabilities():
    with pytest.raises(ValueError, match="already assigned"):
        CapabilityRegistry(
            (
                Capability(
                    capability_id="image.generate",
                    provider_id="comfyui",
                    runtime_id="janku",
                    workflow_templates=("text-to-image",),
                ),
                Capability(
                    capability_id="image.alternate",
                    provider_id="alternate",
                    runtime_id="alternate",
                    workflow_templates=("text-to-image",),
                ),
            )
        )


def test_parallel_workflow_assignment_preserves_every_template():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )
    count = 24
    start = Barrier(count)

    def assign(index):
        start.wait()
        template = f"runtime-{index}"
        registry.assign_workflow("image.generate", "comfyui", template)
        return template

    with ThreadPoolExecutor(max_workers=count) as pool:
        templates = set(pool.map(assign, range(count)))

    expected = {"text-to-image", *templates}
    assert set(registry.get("image.generate", {})["workflow_templates"]) == expected
    assert set(registry.list({})[0]["workflow_templates"]) == expected
    for template in templates:
        assert registry.resolve_workflow(template).capability_id == "image.generate"


def implementations():
    return (
        Capability("image.generate", "comfyui", "janku", ("legacy-image",)),
        Capability("image.generate", "alternate", "different", ("alternate-image",)),
    )


def test_two_providers_need_explicit_legacy_route_before_activation():
    with pytest.raises(ValueError, match="explicit legacy route"):
        CapabilityRegistry(implementations())
    with pytest.raises(ValueError, match="before alternatives"):
        CapabilityRegistry(implementations()[::-1], legacy_routes={"image.generate": "comfyui"})
    with pytest.raises(ValueError, match="does not exist"):
        CapabilityRegistry((), legacy_routes={"image.generate": "comfyui"})


def test_legacy_projection_and_explicit_workflow_routes_never_follow_health():
    registry = CapabilityRegistry(implementations(), legacy_routes={"image.generate": "comfyui"})
    availability = {"comfyui": False, "alternate": True}
    legacy = registry.get("image.generate", availability)
    assert legacy["provider_id"] == "comfyui" and not legacy["available"]
    assert legacy["workflow_templates"] == ["legacy-image"]
    assert registry.list(availability) == [legacy]
    assert registry.resolve_workflow("legacy-image").provider_id == "comfyui"
    assert registry.resolve_workflow("alternate-image").provider_id == "alternate"
    projection = registry.implementations("image.generate", availability)
    assert projection["registry_revision"] == 3
    assert [item["available"] for item in projection["implementations"]] == [False, True]
    assert "available" not in projection  # health alone does not certify aggregate readiness


def test_assignments_are_owned_by_an_exact_implementation():
    registry = CapabilityRegistry(implementations(), legacy_routes={"image.generate": "comfyui"})
    registry.assign_workflow("image.generate", "alternate", "second-alternate")
    assert registry.resolve_workflow("second-alternate").provider_id == "alternate"
    assert registry.get("image.generate", {})["workflow_templates"] == ["legacy-image"]
    with pytest.raises(ValueError, match="another provider"):
        registry.assign_workflow("image.generate", "alternate", "legacy-image")
    with pytest.raises(ValueError, match="does not match"):
        registry.assign_workflow("image.generate", "unknown", "new-image")


def test_rejected_registration_preserves_every_route():
    registry = CapabilityRegistry(implementations(), legacy_routes={"image.generate": "comfyui"})
    before = registry.implementations("image.generate", {})
    with pytest.raises(ValueError, match="already assigned"):
        registry.register(Capability("image.generate", "third", "third", ("legacy-image",)))
    assert registry.implementations("image.generate", {}) == before
    with pytest.raises(ValueError, match="Duplicate capability implementation"):
        registry.register(implementations()[0])


def test_parallel_assignments_keep_provider_routes_separate():
    registry = CapabilityRegistry(implementations(), legacy_routes={"image.generate": "comfyui"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda i: registry.assign_workflow(
                    "image.generate", "alternate" if i % 2 else "comfyui", f"workflow-{i}"
                ),
                range(32),
            )
        )
    assert all(
        registry.resolve_workflow(f"workflow-{i}").provider_id
        == ("alternate" if i % 2 else "comfyui")
        for i in range(32)
    )
