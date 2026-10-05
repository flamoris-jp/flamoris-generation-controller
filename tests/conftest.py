import json

import httpx
import pytest

from flamoris_generation_controller.capabilities import Capability, CapabilityRegistry
from flamoris_generation_controller.comfyui import ComfyUIClient
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.jobs import JobStore
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.providers import ProviderRegistry
from flamoris_generation_controller.providers.comfyui import ComfyUIProvider
from flamoris_generation_controller.workflows import WorkflowStore


def validation_rejection():
    """The provider's validation response, emitted before queue admission."""
    return httpx.Response(
        400,
        json={
            "error": {"type": "prompt_outputs_failed_validation", "message": "Invalid workflow"},
            "node_errors": {},
        },
    )


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "models"
    for kind, name in [
        ("checkpoints", "base.safetensors"),
        ("loras", "style.safetensors"),
        ("loras", "nested/detail.safetensors"),
    ]:
        path = root / kind / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test model placeholder; not weights")
    return Settings(
        model_root=root, workflow_dir=tmp_path / "workflows", output_dir=tmp_path / "outputs"
    )


class FakeComfy:
    """HTTP-only test double; exercise actual adapter serialization and parsing."""

    def __init__(self):
        self.running = []
        self.pending = []
        self.history = {}
        self.calls = []
        self.prompts = []
        self.download_count = 0
        self.race_to_running = False
        self.output_error = False

    def handle(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/queue" and request.method == "GET":
            return httpx.Response(
                200, json={"queue_running": self.running, "queue_pending": self.pending}
            )
        if path == "/prompt":
            self.prompts.append(body)
            prompt_id = f"prompt-{len(self.prompts)}"
            self.pending.append([len(self.prompts), prompt_id, body["prompt"], {}, ["7"]])
            return httpx.Response(200, json={"prompt_id": prompt_id, "node_errors": {}})
        if path.startswith("/history/"):
            key = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={key: self.history[key]} if key in self.history else {})
        if path == "/queue":
            assert set(body) == {"delete"}
            if self.race_to_running:
                self.running += self.pending
            self.pending = [entry for entry in self.pending if entry[1] not in body["delete"]]
            return httpx.Response(200)
        if path == "/interrupt":
            assert set(body) == {"prompt_id"}
            return httpx.Response(200)
        if path == "/view":
            self.download_count += 1
            assert request.url.params["type"] == "output"
            if self.output_error:
                return httpx.Response(503)
            return httpx.Response(
                200, content=b"image fixture", headers={"content-type": "image/png"}
            )
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    def finish(self, prompt_id="prompt-1", state="success", messages=None):
        self.pending = [entry for entry in self.pending if entry[1] != prompt_id]
        self.running = [entry for entry in self.running if entry[1] != prompt_id]
        self.history[prompt_id] = {
            "status": {
                "status_str": state,
                "completed": state == "success",
                "messages": messages or [],
            },
            "outputs": {
                "7": {
                    "images": [
                        {"filename": "result.png", "subfolder": "flamoris", "type": "output"}
                    ]
                }
            },
        }


@pytest.fixture
def fake():
    return FakeComfy()


@pytest.fixture
async def stores(settings, fake):
    client = ComfyUIClient(settings, httpx.MockTransport(fake.handle))
    catalog = ModelCatalog(settings)
    workflows = WorkflowStore(catalog, settings.workflow_dir)
    providers = ProviderRegistry((ComfyUIProvider(client, catalog),))
    capabilities = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image", "text-to-image-lora"),
            ),
        )
    )
    yield workflows, JobStore(workflows, providers, capabilities, settings.output_dir), client
    await client.close()
