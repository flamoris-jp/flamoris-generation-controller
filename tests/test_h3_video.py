"""Silent H3 contracts; synthetic graphs/weights do not qualify GPU inference."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_video import fixture as wan_fixture

from flamoris_generation_controller.declared_comfy import validate
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.workflows import WorkflowStore


def fixture(mode="i2v"):
    data = json.loads((Path(__file__).parent / "fixtures/minimax-h3-contract.json").read_text())
    body, info = data["registration"], data["object_info"]
    if mode == "flf2v":
        body["graph"]["14"] = {"class_type": "LoadImage", "inputs": {"image": "$end_image"}}
        body["graph"]["5"]["inputs"]["last_frame"] = ["14", 0]
        body["declaration"]["video"]["mode"] = mode
        body["declaration"]["parameters"]["end_image"] = {
            "type": "string",
            "role": "managed_input",
            "label": "End",
            "required": True,
            "targets": [{"node": "14", "input": "image"}],
        }
    return body, info


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
def test_h3_registration_build_restart_and_exact_profile(settings, mode):
    body, info = fixture(mode)
    for folder in ("diffusion_models", "text_encoders", "vae"):
        path = settings.model_root / folder / "fixture.safetensors"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic placeholder, never model weights")
    original = deepcopy(info)
    catalog = ModelCatalog(settings)
    store = WorkflowStore(catalog, settings.workflow_dir, declared_comfy_nodes=list(info))
    definition = store.definitions.register(**body, node_specs=info)
    assert definition["video"] == {
        "profile": "minimax-h3-video-v1",
        "mode": mode,
        "conditioning_node": "5",
    }
    images = {"start_image": "a" * 32}
    if mode == "flf2v":
        images["end_image"] = "b" * 32
    built = store.build(definition["id"], {**images, "frames": 124})
    assert built["schema_version"] == 11
    assert built["prompt"]["5"]["inputs"]["length"] == 124
    assert built["prompt"]["12"]["inputs"]["fps"] == 24
    assert "audio" not in built["prompt"]["12"]["inputs"]
    assert store.definitions.get(definition["id"])[2].version == 3
    store.save(built["workflow_id"])
    fresh = WorkflowStore(catalog, settings.workflow_dir, declared_comfy_nodes=list(info))
    assert fresh.prompt(fresh.get(built["workflow_id"])) == built["prompt"]
    assert info == original
    with pytest.raises(ValueError):
        store.build(definition["id"], {**images, "frames": 81})


@pytest.mark.parametrize(
    "bad",
    [
        "wan_version",
        "wan_frames",
        "width",
        "fps",
        "unbounded_steps",
        "tiled_decode",
        "last_frame_in_i2v",
        "conditioning_bypass",
        "audio",
        "extra_end_role",
    ],
)
def test_h3_invalid_semantics_fail_before_registration(bad):
    body, info = fixture()
    graph, decl = body["graph"], body["declaration"]
    if bad == "wan_version":
        decl["version"] = 2
    elif bad in {"wan_frames", "width", "fps", "unbounded_steps"}:
        name, value = {
            "wan_frames": ("frames", 81),
            "width": ("width", 80),
            "fps": ("fps", 16),
            "unbounded_steps": ("steps", 51),
        }[bad]
        spec = decl["parameters"][name]
        target = spec["targets"][0]
        spec["default"] = value
        graph[target["node"]]["inputs"][target["input"]] = value
        if bad == "fps":
            spec.update(minimum=1, maximum=32)
        if bad == "unbounded_steps":
            spec["maximum"] = 10000
    elif bad == "tiled_decode":
        graph["11"]["class_type"] = "VAEDecodeTiled"
        info["VAEDecodeTiled"] = deepcopy(info["VAEDecode"])
    elif bad == "last_frame_in_i2v":
        graph["5"]["inputs"]["last_frame"] = ["4", 0]
    elif bad == "conditioning_bypass":
        info["SyntheticCondition"] = {"input": {}, "output": ["CONDITIONING"]}
        graph["14"] = {"class_type": "SyntheticCondition", "inputs": {}}
        graph["7"]["inputs"]["conditioning"] = ["14", 0]
    elif bad == "audio":
        info["SyntheticAudio"] = {"input": {}, "output": ["AUDIO"]}
        graph["14"] = {"class_type": "SyntheticAudio", "inputs": {}}
        graph["12"]["inputs"]["audio"] = ["14", 0]
    elif bad == "extra_end_role":
        decl["parameters"]["end_image"] = deepcopy(decl["parameters"]["start_image"])
    with pytest.raises(ValueError):
        validate(graph, decl, info, set(info))


def test_wan_graph_cannot_be_relabeled_as_h3():
    body, info = wan_fixture()
    body["declaration"]["version"] = 3
    with pytest.raises(ValueError):
        validate(body["graph"], body["declaration"], info, set(info))
