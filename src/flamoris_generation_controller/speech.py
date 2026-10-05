"""Explicit native speech recipe; never a ComfyUI graph or readiness attestation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SPEECH_TEMPLATE = "speech-no-reference"
SPEECH_PROVIDER = "irodori"
SPEECH_CAPABILITY = "speech.generate"
IRODORI_SOURCE_REVISION = "89f9d8fbd4d51ea019867ee1197725ede1df13c5"


def speech_profile():
    return {
        "profile": "irodori-no-reference-v1",
        "source_revision": IRODORI_SOURCE_REVISION,
        "reference_audio": False,
        "candidates": 1,
    }


class SpeechParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    text: str = Field(min_length=1, max_length=512, strict=True)
    caption: str = Field(default="", max_length=512, strict=True)
    seconds: float = Field(default=10, ge=0.5, le=30, allow_inf_nan=False, strict=True)
    steps: int = Field(default=40, ge=1, le=80, strict=True)
    seed: int = Field(default=0, ge=0, le=2**53 - 1, strict=True)

    @field_validator("text", "caption")
    @classmethod
    def bounded_text(cls, value, info):
        if len(value.encode("utf-8")) > 2048 or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Speech text contains unsupported characters or exceeds byte limit")
        if info.field_name == "text" and not value.strip():
            raise ValueError("Speech text must not be empty")
        return value


class SpeechRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[4] = 4
    template: Literal["speech-no-reference"] = SPEECH_TEMPLATE
    parameters: SpeechParameters


def speech_descriptor():
    properties = SpeechParameters.model_json_schema()["properties"]
    parameters = {}
    for name, schema in properties.items():
        spec = {
            key: value
            for key, value in schema.items()
            if key in {"type", "default", "minimum", "maximum"}
        }
        for source, target in (("minLength", "min_length"), ("maxLength", "max_length")):
            if source in schema:
                spec[target] = schema[source]
        if name in {"text", "caption"}:
            spec["max_bytes"] = 2048
        parameters[name] = {**spec, "role": name, "required": "default" not in schema}
    return {
        "id": SPEECH_TEMPLATE,
        "name": "Irodori speech without reference audio",
        "description": "Configured native Japanese speech; no reference audio or voice cloning",
        "kind": "native",
        "metadata_schema_version": 2,
        "schema_version": 4,
        "version": 1,
        "provider_id": SPEECH_PROVIDER,
        "capability_id": SPEECH_CAPABILITY,
        "parameters": parameters,
        "input_roles": [],
        "output_roles": [{"port": "audio", "role": "audio", "media_kind": "audio"}],
        "qualification": "configured-local-resources",
        "speech": speech_profile(),
        "readiness": {"status": "not-attested"},
    }
