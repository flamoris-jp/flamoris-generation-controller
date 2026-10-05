"""Provider-independent capability discovery for the Generation Hub."""

import re
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    provider_id: str
    runtime_id: str
    workflow_templates: tuple[str, ...]

    def as_dict(self, *, available: bool) -> dict[str, object]:
        return {
            "id": self.capability_id,
            "provider_id": self.provider_id,
            "runtime_id": self.runtime_id,
            "workflow_templates": list(self.workflow_templates),
            "available": available,
        }


class CapabilityRegistry:
    def __init__(
        self,
        capabilities: tuple[Capability, ...] = (),
        *,
        legacy_routes: dict[str, str] | None = None,
    ):
        self._lock = threading.RLock()
        self._capabilities: dict[str, Capability] = {}
        self._implementations: dict[tuple[str, str], Capability] = {}
        self._legacy_routes = dict(legacy_routes or {})
        self._workflow_capabilities: dict[str, Capability] = {}
        for capability in capabilities:
            self.register(capability)
        if set(self._legacy_routes) - set(self._capabilities):
            raise ValueError("Configured legacy capability route does not exist")

    def register(self, capability: Capability) -> None:
        with self._lock:
            self._register_unlocked(capability)

    def _register_unlocked(self, capability: Capability) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+", capability.capability_id):
            raise ValueError("Capability ID must be a stable dotted lowercase identifier")
        key = (capability.capability_id, capability.provider_id)
        if key in self._implementations:
            raise ValueError(f"Duplicate capability implementation: {capability.capability_id}")
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", capability.provider_id):
            raise ValueError("Invalid capability provider identity")
        if len(capability.workflow_templates) != len(set(capability.workflow_templates)):
            raise ValueError("Duplicate workflow template in implementation")
        current = self._capabilities.get(capability.capability_id)
        legacy_provider = self._legacy_routes.get(capability.capability_id)
        if current is not None and legacy_provider is None:
            raise ValueError("Multiple implementations require an explicit legacy route")
        if (
            current is None
            and legacy_provider is not None
            and (legacy_provider != capability.provider_id)
        ):
            raise ValueError("Configured legacy route must be registered before alternatives")
        if len(self._implementations) >= 128:
            raise ValueError("Capability implementation limit exceeded")
        if len(self._workflow_capabilities) + len(capability.workflow_templates) > 128:
            raise ValueError("Capability workflow limit exceeded")
        duplicate_workflows = [
            template
            for template in capability.workflow_templates
            if template in self._workflow_capabilities
        ]
        if duplicate_workflows:
            raise ValueError(
                "Workflow template is already assigned to a capability: "
                + ", ".join(duplicate_workflows)
            )
        self._implementations[key] = capability
        if current is None or capability.provider_id == legacy_provider:
            self._capabilities[capability.capability_id] = capability
        for template in capability.workflow_templates:
            self._workflow_capabilities[template] = capability

    def assign_workflow(self, capability_id: str, provider_id: str, template: str) -> None:
        with self._lock:
            self._validate_workflow_unlocked(capability_id, provider_id, template)
            capability = self._implementations[(capability_id, provider_id)]
            if template in self._workflow_capabilities:
                return
            if len(self._workflow_capabilities) >= 128:
                raise ValueError("Capability workflow limit exceeded")
            updated = Capability(
                capability_id=capability.capability_id,
                provider_id=capability.provider_id,
                runtime_id=capability.runtime_id,
                workflow_templates=(*capability.workflow_templates, template),
            )
            self._implementations[(capability_id, provider_id)] = updated
            if self._capabilities[capability_id].provider_id == provider_id:
                self._capabilities[capability_id] = updated
            for workflow in updated.workflow_templates:
                self._workflow_capabilities[workflow] = updated

    def validate_workflow(self, capability_id: str, provider_id: str, template: str) -> None:
        with self._lock:
            self._validate_workflow_unlocked(capability_id, provider_id, template)

    def _validate_workflow_unlocked(
        self, capability_id: str, provider_id: str, template: str
    ) -> None:
        try:
            self._capabilities[capability_id]
        except KeyError:
            raise ValueError(f"Unknown capability ID: {capability_id}") from None
        if (capability_id, provider_id) not in self._implementations:
            raise ValueError("Workflow provider does not match capability provider")
        existing = self._workflow_capabilities.get(template)
        if existing is not None:
            if existing.capability_id != capability_id:
                raise ValueError("Workflow template is already assigned to another capability")
            if existing.provider_id != provider_id:
                raise ValueError("Workflow template is already assigned to another provider")

    def implementations(
        self, capability_id: str, availability: dict[str, bool]
    ) -> dict[str, object]:
        """Internal graph-free projection. Health is not exact Workflow readiness.

        Public catalog negotiation and composed readiness are separate deliveries.
        The existing list/get projection continues to describe the explicit legacy route.
        """
        with self._lock:
            if capability_id not in self._capabilities:
                raise ValueError(f"Unknown capability ID: {capability_id}")
            return {
                "registry_revision": 3,
                "id": capability_id,
                "legacy_provider_id": self._capabilities[capability_id].provider_id,
                "implementations": [
                    capability.as_dict(available=availability.get(capability.provider_id, False))
                    for (operation, _), capability in self._implementations.items()
                    if operation == capability_id
                ],
            }

    def list(self, availability: dict[str, bool]) -> list[dict[str, object]]:
        with self._lock:
            return [
                capability.as_dict(available=availability.get(capability.provider_id, False))
                for capability in self._capabilities.values()
            ]

    def get(self, capability_id: str, availability: dict[str, bool]) -> dict[str, object]:
        with self._lock:
            try:
                capability = self._capabilities[capability_id]
            except KeyError:
                raise ValueError(f"Unknown capability ID: {capability_id}") from None
            return capability.as_dict(available=availability.get(capability.provider_id, False))

    def resolve_workflow(self, workflow_template: str) -> Capability:
        with self._lock:
            try:
                return self._workflow_capabilities[workflow_template]
            except KeyError:
                raise ValueError(
                    f"No capability is registered for workflow template: {workflow_template}"
                ) from None
