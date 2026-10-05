"""The fixed, opt-in YuE2 Music profile, separate from runtime attestation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MUSIC_TEMPLATE = "music-generate"
MUSIC_PROVIDER = "yue2"
MUSIC_CAPABILITY = "music.generate"
YUE2_SOURCE_REVISION = "decfe04c2ae2f8c73855832a56ddda0fce849407"


def music_profile():
    return {
        "profile": "yue2-single-track-v1",
        "source_revision": YUE2_SOURCE_REVISION,
        "cot": "full",
        "tracks": 1,
        "output_format": "wav16",
        "sample_rate": 48000,
        "channels": 2,
    }


class MusicParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    style: str = Field(min_length=1, max_length=1024, strict=True)
    lyrics: str = Field(default="", max_length=4096, strict=True)
    seconds: float = Field(default=30, ge=1, le=120, allow_inf_nan=False, strict=True)
    steps: int = Field(default=32, ge=1, le=64, strict=True)
    seed: int = Field(default=0, ge=0, le=2**53 - 1, strict=True)
    lm_seed: int = Field(default=0, ge=0, le=2**53 - 1, strict=True)

    @field_validator("style", "lyrics")
    @classmethod
    def bounded_text(cls, value, info):
        limit = 4096 if info.field_name == "style" else 16384
        if len(value.encode("utf-8")) > limit or any(
            ord(c) < 32 and c not in "\n\t" for c in value
        ):
            raise ValueError("Music text contains unsupported characters or exceeds byte limit")
        if info.field_name == "style" and not value.strip():
            raise ValueError("Music style must not be empty")
        return value


class MusicRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[5] = 5
    template: Literal["music-generate"] = MUSIC_TEMPLATE
    parameters: MusicParameters


def music_descriptor():
    parameters = {}
    for name, schema in MusicParameters.model_json_schema()["properties"].items():
        spec = {
            key: value
            for key, value in schema.items()
            if key in {"type", "default", "minimum", "maximum"}
        }
        for source, target in (("minLength", "min_length"), ("maxLength", "max_length")):
            if source in schema:
                spec[target] = schema[source]
        if name in {"style", "lyrics"}:
            spec["max_bytes"] = 4096 if name == "style" else 16384
        parameters[name] = {**spec, "role": name, "required": "default" not in schema}
    return {
        "id": MUSIC_TEMPLATE,
        "name": "YuE2 music with symbolic score",
        "description": "Configured single-track music with WAV, ABC and replay metadata",
        "kind": "native",
        "metadata_schema_version": 2,
        "schema_version": 5,
        "version": 1,
        "provider_id": MUSIC_PROVIDER,
        "capability_id": MUSIC_CAPABILITY,
        "parameters": parameters,
        "input_roles": [],
        "output_roles": [
            {"port": "audio", "role": "audio", "media_kind": "audio"},
            {"port": "score", "role": "score", "media_kind": "score"},
            {"port": "metadata", "role": "metadata", "media_kind": "metadata"},
        ],
        "qualification": "configured-http-contract",
        "music": music_profile(),
        "readiness": {"status": "not-attested"},
    }
