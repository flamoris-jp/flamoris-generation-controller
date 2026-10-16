"""Explicit native speech recipe; never a ComfyUI graph or readiness attestation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SPEECH_TEMPLATE = "speech-no-reference"
SPEECH_PROVIDER = "irodori"
SPEECH_CAPABILITY = "speech.generate"
IRODORI_SOURCE_REVISION = "89f9d8fbd4d51ea019867ee1197725ede1df13c5"
IRODORI_SERVER_CONTRACT_REVISION = "61012c760f22f7b4a6c21c5c5f8f9e148120b6f9"


SPEECH_REFERENCE_TEMPLATE = "speech-reference"


def speech_profile(http=False, reference=False):
    if reference:
        if not http:
            raise ValueError("Reference speech requires the resident HTTP adapter")
        return {
            **speech_profile(True),
            "profile": "irodori-server-reference-v1",
            "reference_audio": True,
        }
    if http:
        return {
            "profile": "irodori-server-no-reference-v1",
            "server_contract_revision": IRODORI_SERVER_CONTRACT_REVISION,
            "reference_audio": False,
            "candidates": 1,
        }
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
    seconds: float | None = Field(default=None, ge=0.5, le=30, allow_inf_nan=False, strict=True)
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


class ReferenceSpeechParameters(SpeechParameters):
    reference_audio: str = Field(strict=True, pattern=r"^[0-9a-f]{32}$")


class ReferenceSpeechRecipe(SpeechRecipe):
    schema_version: Literal[9] = 9
    template: Literal["speech-reference"] = SPEECH_REFERENCE_TEMPLATE
    parameters: ReferenceSpeechParameters


def reference_speech_descriptor():
    descriptor = speech_descriptor(True)
    descriptor.update(
        id=SPEECH_REFERENCE_TEMPLATE,
        schema_version=9,
        name="Irodori speech with reference audio",
        description="Speech conditioned on one managed reference WAV",
        speech=speech_profile(True, True),
    )
    descriptor["parameters"]["reference_audio"] = {
        "type": "managed_input",
        "role": "reference_audio",
        "required": True,
        "media_types": ["audio/wav"],
        "max_bytes": 8 * 1024**2,
        "max_seconds": 30,
    }
    descriptor["input_roles"] = [
        {"parameter": "reference_audio", "role": "reference_audio", "media_kind": "audio"}
    ]
    return descriptor


def speech_descriptor(http=False):
    properties = SpeechParameters.model_json_schema()["properties"]
    parameters = {}
    for name, schema in properties.items():
        if name == "seconds":
            # Keep numeric controls discoverable while explicitly advertising
            # omission/null as automatic duration, rather than another default.
            schema = {
                **next(s for s in schema["anyOf"] if s.get("type") == "number"),
                "default": None,
            }
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
        if name == "seconds":
            parameters[name].update(
                nullable=True,
                description="Omit or use null for auto duration; set a number for fixed length.",
            )
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
        "qualification": "configured-http-contract" if http else "configured-local-resources",
        "speech": speech_profile(http),
        "readiness": {"status": "not-attested"},
    }
