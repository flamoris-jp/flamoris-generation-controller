"""Fixed native transcription recipe using an immutable managed WAV reference."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TRANSCRIPTION_PROVIDER = "sheetsage2"
TRANSCRIPTION_CAPABILITY = "music.transcribe"
TRANSCRIPTION_TEMPLATE = "music-transcribe"
SHEETSAGE2_ENTRYPOINT_SHA256 = "20e6b23c910bfdecf012a8ee8d45efcb31cb6cb21351da5921d95fb877e70de6"


def transcription_profile():
    return {
        "profile": "sheetsage2-python-cpu-v1",
        "entrypoint_sha256": SHEETSAGE2_ENTRYPOINT_SHA256,
        "device": "cpu",
        "dtype": "fp32",
        "offline": True,
    }


class TranscriptionParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    audio: str = Field(strict=True, pattern=r"^[0-9a-f]{32}$")
    max_seconds: float = Field(default=30.0, ge=1, le=120, strict=True, allow_inf_nan=False)
    melody_only: bool = Field(default=False, strict=True)


class TranscriptionRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[6] = 6
    template: Literal["music-transcribe"] = TRANSCRIPTION_TEMPLATE
    parameters: TranscriptionParameters


def transcription_descriptor():
    return {
        "id": TRANSCRIPTION_TEMPLATE,
        "name": "SheetSage2 music transcription",
        "description": "Transcribe a bounded segment of managed WAV audio to MIDI and score",
        "kind": "native",
        "metadata_schema_version": 2,
        "schema_version": 6,
        "version": 1,
        "provider_id": TRANSCRIPTION_PROVIDER,
        "capability_id": TRANSCRIPTION_CAPABILITY,
        "parameters": {
            "audio": {
                "type": "managed_input",
                "role": "audio",
                "required": True,
                "media_types": ["audio/wav"],
            },
            "max_seconds": {
                "type": "number",
                "role": "max_seconds",
                "required": False,
                "default": 30.0,
                "minimum": 1,
                "maximum": 120,
            },
            "melody_only": {
                "type": "boolean",
                "role": "melody_only",
                "required": False,
                "default": False,
            },
        },
        "input_roles": [{"parameter": "audio", "role": "audio", "media_kind": "audio"}],
        "output_roles": [
            {"port": "midi", "role": "midi", "media_kind": "midi"},
            {"port": "score", "role": "score", "media_kind": "score", "optional": True},
            {"port": "events", "role": "events", "media_kind": "metadata"},
            {"port": "summary", "role": "summary", "media_kind": "metadata"},
            {
                "port": "annotations",
                "role": "annotations",
                "media_kind": "metadata",
                "optional": True,
            },
            {
                "port": "parts",
                "role": "parts",
                "media_kind": "midi",
                "collection": True,
                "optional": True,
            },
        ],
        "qualification": "configured-local-resources",
        "transcription": transcription_profile(),
        "readiness": {"status": "not-attested"},
    }
