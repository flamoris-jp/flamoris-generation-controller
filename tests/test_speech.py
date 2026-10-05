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
