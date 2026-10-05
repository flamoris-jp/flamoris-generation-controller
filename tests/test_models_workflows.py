import json

import pytest
from pydantic import ValidationError

from flamoris_generation_controller.config import MODEL_FOLDERS, Settings
from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.workflows import Lora, Parameters, WorkflowStore


def test_scan_all_kinds_and_details(settings):
    for folder in MODEL_FOLDERS.values():
        directory = settings.model_root / folder
        directory.mkdir(exist_ok=True)
        (directory / "extra.GGUF").write_bytes(b"123")
        (directory / "ignore.txt").write_text("not a model")
    catalog = ModelCatalog(settings)
    models = catalog.list()
    assert {m["kind"] for m in models} == set(MODEL_FOLDERS)
    assert len(models) == 12
    assert len(catalog.list("lora")) == 3
    assert catalog.get("lora:nested/detail.safetensors")["format"] == "safetensors"
    assert catalog.get("vae:extra.GGUF")["size_bytes"] == 3


def test_configured_roots_missing_duplicate_and_symlink(settings, tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "outside.pt").write_bytes(b"model")
    (settings.model_root / "loras" / "escape.pt").symlink_to(custom / "outside.pt")
    catalog = ModelCatalog(settings)
    assert len(catalog.list("lora")) == 2
    assert catalog.list("controlnet") == []
    configured = settings.model_copy(update={"model_dirs": {"lora": [custom]}})
    assert ModelCatalog(configured).list("lora")[0]["name"] == "outside.pt"
    (custom / "style.safetensors").write_bytes(b"duplicate")
    configured.model_dirs["lora"].append(settings.model_root / "loras")
    with pytest.raises(ValueError, match="Ambiguous"):
        ModelCatalog(configured).list("lora")


@pytest.mark.parametrize(
    "identity",
    [
        "other:x.pt",
        "checkpoint:missing.pt",
        "lora:../x.pt",
        "lora:/x.pt",
        "lora:C:/x.pt",
        "lora:folder\\x.pt",
    ],
)
def test_invalid_or_missing_model(settings, identity):
    with pytest.raises(ValueError):
        ModelCatalog(settings).get(identity)


def test_plain_and_multiple_lora_graph(settings):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    params = Parameters(
        checkpoint="base.safetensors",
        positive_prompt="flowers",
        negative_prompt="blurry",
        width=768,
        height=1024,
        seed=123,
        steps=31,
        cfg=6.5,
        sampler="dpmpp_2m",
        scheduler="karras",
        denoise=0.9,
    )
    plain = store.build("text-to-image", params)["prompt"]
    assert plain["5"]["inputs"] == {
        "model": ["1", 0],
        "positive": ["2", 0],
        "negative": ["3", 0],
        "latent_image": ["4", 0],
        "seed": 123,
        "steps": 31,
        "cfg": 6.5,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
        "denoise": 0.9,
    }
    assert plain["4"]["inputs"] == {"width": 768, "height": 1024, "batch_size": 1}
    assert plain["2"]["inputs"]["text"] == "flowers"
    assert plain["3"]["inputs"]["text"] == "blurry"
    params = params.model_copy(
        update={
            "loras": (
                Lora(name="style.safetensors", strength_model=0.8, strength_clip=0.4),
                Lora(name="nested/detail.safetensors", strength_model=-0.2, strength_clip=0),
            )
        }
    )
    graph = store.build("text-to-image-lora", params)["prompt"]
    assert graph["10"]["inputs"]["model"] == ["1", 0]
    assert graph["11"]["inputs"]["model"] == ["10", 0]
    assert graph["11"]["inputs"]["clip"] == ["10", 1]
    assert graph["10"]["inputs"]["strength_clip"] == 0.4
    assert graph["11"]["inputs"]["strength_model"] == -0.2
    assert graph["5"]["inputs"]["model"] == ["11", 0]
    assert graph["2"]["inputs"]["clip"] == graph["3"]["inputs"]["clip"] == ["11", 1]
    assert graph["6"]["inputs"]["vae"] == ["1", 2]


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 513},
        {"width": True},
        {"seed": -1},
        {"steps": 0},
        {"cfg": float("nan")},
        {"denoise": 2},
        {"checkpoint": "../bad.pt"},
        {"unknown": 1},
        {"loras": [{"name": "a.pt", "strength_clip": float("inf")}]},
        {"loras": [{"name": "a.pt"}] * 17},
    ],
)
def test_argument_validation(changes):
    with pytest.raises(ValidationError):
        Parameters.model_validate(
            {"checkpoint": "base.safetensors", "positive_prompt": "x", **changes}
        )


def test_template_semantics_and_saved_reload(settings):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    p = Parameters(checkpoint="base.safetensors", positive_prompt="x")
    with pytest.raises(ValueError, match="at least one"):
        store.build("text-to-image-lora", p)
    loras = p.model_copy(update={"loras": (Lora(name="missing.pt"),)})
    with pytest.raises(ValueError, match="Use text-to-image-lora"):
        store.build("text-to-image", loras)
    with pytest.raises(ValueError, match="Model not found"):
        store.build("text-to-image-lora", loras)
    built = store.build("text-to-image", p)
    key = built["workflow_id"]
    store.save(key)
    saved = settings.workflow_dir / f"{key}.json"
    assert "prompt" not in json.loads(saved.read_text())
    new_store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    assert new_store.get(key) == store.get(key)
    assert new_store.list()["saved_workflows"] == [key]
    with pytest.raises(ValueError):
        new_store.get("../escape")
    saved.write_text('{"schema_version": 999}')
    with pytest.raises(ValidationError):
        new_store.get(key)


def test_environment_config(monkeypatch):
    monkeypatch.setenv("FLAMORIS_MODEL_ROOT", "assets/models")
    monkeypatch.setenv("FLAMORIS_MODEL_DIRS", '{"lora":["assets/styles"]}')
    monkeypatch.setenv("FLAMORIS_TARGETED_INTERRUPT", "true")
    settings = Settings.from_env()
    assert settings.roots("lora")[0].as_posix() == "assets/styles"
    assert settings.roots("checkpoint")[0].as_posix() == "assets/models/checkpoints"
    assert settings.targeted_interrupt
    monkeypatch.setenv("FLAMORIS_MODEL_DIRS", '{"typo":[]}')
    with pytest.raises(ValidationError):
        Settings.from_env()


def test_unicode_recipe_round_trip_and_no_raw_node_import(settings):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    p = Parameters(
        checkpoint="base.safetensors", positive_prompt="🌸" * 20000, negative_prompt="🌱" * 20000
    )
    built = store.build("text-to-image", p)
    key = built["workflow_id"]
    # Mutating an exported graph cannot change what is subsequently submitted.
    built["prompt"]["1"]["inputs"]["ckpt_name"] = "injected.pt"
    assert store.get(key).parameters.checkpoint == "base.safetensors"
    store.save(key)
    fresh = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    assert fresh.get(key).parameters == p
    path = settings.workflow_dir / f"{key}.json"
    data = json.loads(path.read_text())
    data["prompt"] = {"evil": {"class_type": "arbitrary"}}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        fresh.get(key)
