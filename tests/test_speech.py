import pytest
from pydantic import ValidationError

from flamoris_generation_controller.speech import SpeechParameters, SpeechRecipe, speech_descriptor


@pytest.mark.parametrize(
    "params",
    [
        {"text": " "},
        {"text": "hello\0"},
        {"text": "a" * 513},
        {"text": "a", "seed": True},
        {"text": "a", "seed": 2**53},
        {"text": "a", "seconds": float("nan")},
        {"text": "a", "seconds": 31},
        {"text": "a", "steps": 81},
        {"text": "a", "ref_wav": "/tmp/voice.wav"},
    ],
)
def test_bounded_native_parameters(params):
    with pytest.raises(ValidationError):
        SpeechParameters.model_validate(params)


def test_native_recipe_has_no_graph_or_reference_authority():
    recipe = SpeechRecipe(parameters={"text": "こんにちは😊", "caption": "穏やか"})
    assert recipe.schema_version == 4
    assert recipe.parameters.seed == 0
    with pytest.raises(ValidationError):
        SpeechRecipe.model_validate({**recipe.model_dump(), "require_ready": True})
    descriptor = speech_descriptor()
    assert descriptor["provider_id"] == "irodori"
    assert descriptor["capability_id"] == "speech.generate"
    assert descriptor["readiness"]["status"] == "not-attested"
    assert descriptor["input_roles"] == []
    assert "graph" not in descriptor


def test_workflow_opt_in_and_attestation_preconditions(tmp_path):
    from flamoris_generation_controller.config import Settings
    from flamoris_generation_controller.models import ModelCatalog
    from flamoris_generation_controller.workflows import WorkflowStore

    store = WorkflowStore(ModelCatalog(Settings(model_root=tmp_path)), tmp_path / "recipes")
    with pytest.raises(ValueError, match="disabled"):
        store.build("speech-no-reference", {"text": "hello"})
    store.speech_enabled = True
    for preconditions in (
        {"require_ready": True},
        {"definition_version": 1},
        {"definition_version": 1, "definition_digest": "sha256:" + "a" * 64},
    ):
        with pytest.raises(ValueError, match="attestation"):
            store.build("speech-no-reference", {"text": "hello"}, **preconditions)
    built = store.build("speech-no-reference", {"text": "hello"})
    store.save(built["workflow_id"])
    store._recipes.clear()
    store.speech_enabled = False
    with pytest.raises(ValueError, match="disabled"):
        store.get(built["workflow_id"])


@pytest.mark.parametrize(
    "params", [{"text": "こんにちは"}, {"text": "こんにちは", "seconds": None}]
)
def test_automatic_duration_survives_recipe_serialization(params):
    recipe = SpeechRecipe(parameters=params)
    assert recipe.parameters.seconds is None
    assert SpeechRecipe.model_validate_json(recipe.model_dump_json()).parameters.seconds is None
    spec = speech_descriptor(True)["parameters"]["seconds"]
    assert spec["type"] == "number" and spec["nullable"] is True
    assert spec["default"] is None and spec["required"] is False
    assert (spec["minimum"], spec["maximum"]) == (0.5, 30)


def test_retained_fixed_duration_is_not_reinterpreted_as_automatic():
    retained = SpeechRecipe(parameters={"text": "こんにちは", "seconds": 10.0})
    assert SpeechRecipe.model_validate_json(retained.model_dump_json()).parameters.seconds == 10.0


@pytest.mark.parametrize("seconds", [None, 3.5])
def test_cli_omits_manual_duration_option_only_in_automatic_mode(seconds):
    from pathlib import Path
    from types import SimpleNamespace

    from flamoris_generation_controller.providers.irodori import IrodoriProvider

    provider = object.__new__(IrodoriProvider)
    provider.config = SimpleNamespace(
        python=Path("/python"),
        source_root=Path("/source"),
        checkpoint=Path("/model"),
        codec=Path("/codec"),
        model_device="cpu",
        codec_device="cpu",
    )
    args = provider._argv(
        SpeechRecipe(parameters={"text": "こんにちは", "seconds": seconds}), Path("/output.wav")
    )
    assert ("--seconds" in args) == (seconds is not None)
    if seconds is not None:
        assert args[args.index("--seconds") + 1] == "3.5"
