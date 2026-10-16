"""Environment-only provider/storage configuration owned by Controller."""

import json
import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

ModelKind = Literal[
    "checkpoint",
    "lora",
    "vae",
    "controlnet",
    "ipadapter",
    "clip",
    "clip_vision",
    "diffusion_model",
    "text_encoder",
    "unet",
]
MODEL_FOLDERS: dict[str, str] = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "controlnet": "controlnet",
    "ipadapter": "ipadapter",
    "clip": "clip",
    "clip_vision": "clip_vision",
    "diffusion_model": "diffusion_models",
    "text_encoder": "text_encoders",
    "unet": "unet",
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    comfyui_url: HttpUrl = HttpUrl("http://localhost:8188")
    model_root: Path = Path("models")
    model_dirs: dict[ModelKind, list[Path]] = Field(default_factory=dict)
    workflow_dir: Path = Path(".generation/workflows")
    output_dir: Path = Path(".generation/outputs")
    comfyui_output_root: Path | None = None
    comfyui_input_root: Path | None = None
    provider_input_max_files: int = Field(default=128, ge=1, le=128)
    provider_input_max_bytes: int = Field(default=512 * 1024**2, ge=1, le=512 * 1024**2)
    provider_cleanup_enabled: bool = False
    provider_retention_days: int = Field(default=30, ge=0, le=36500)
    request_timeout: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    transfer_max_bytes: int = Field(default=1024**3, ge=1, le=4 * 1024**3)
    transfer_disk_bytes: int = Field(default=8 * 1024**3, ge=1, le=64 * 1024**3)
    targeted_interrupt: bool = False
    irodori_config: Path | None = None
    irodori_url: HttpUrl | None = None
    irodori_model: str = Field(default="irodori-tts", min_length=1, max_length=128)
    irodori_token: str | None = Field(default=None, min_length=1, max_length=512, repr=False)
    irodori_timeout: float = Field(default=900, ge=5, le=1800, allow_inf_nan=False)
    yue2_config: Path | None = None
    yue2_output_root: Path | None = None
    sheetsage2_config: Path | None = None

    env_fields: ClassVar[dict[str, str]] = {
        "COMFYUI_URL": "comfyui_url",
        "MODEL_ROOT": "model_root",
        "WORKFLOW_DIR": "workflow_dir",
        "OUTPUT_DIR": "output_dir",
        "COMFYUI_OUTPUT_ROOT": "comfyui_output_root",
        "COMFYUI_INPUT_ROOT": "comfyui_input_root",
        "PROVIDER_INPUT_MAX_FILES": "provider_input_max_files",
        "PROVIDER_INPUT_MAX_BYTES": "provider_input_max_bytes",
        "PROVIDER_CLEANUP_ENABLED": "provider_cleanup_enabled",
        "PROVIDER_RETENTION_DAYS": "provider_retention_days",
        "REQUEST_TIMEOUT": "request_timeout",
        "TRANSFER_MAX_BYTES": "transfer_max_bytes",
        "TRANSFER_DISK_BYTES": "transfer_disk_bytes",
        "TARGETED_INTERRUPT": "targeted_interrupt",
        "IRODORI_CONFIG": "irodori_config",
        "IRODORI_URL": "irodori_url",
        "IRODORI_MODEL": "irodori_model",
        "IRODORI_TOKEN": "irodori_token",
        "IRODORI_TIMEOUT": "irodori_timeout",
        "YUE2_CONFIG": "yue2_config",
        "YUE2_OUTPUT_ROOT": "yue2_output_root",
        "SHEETSAGE2_CONFIG": "sheetsage2_config",
    }

    @field_validator("irodori_url")
    @classmethod
    def irodori_origin(cls, value):
        if value is not None and (
            value.username is not None
            or value.password is not None
            or value.query is not None
            or value.fragment is not None
            or value.path not in (None, "/")
        ):
            raise ValueError("Irodori URL must be an operator-configured HTTP origin")
        return value

    @field_validator("irodori_url", "irodori_token", mode="before")
    @classmethod
    def optional_irodori_value(cls, value):
        return None if value == "" else value

    @field_validator("irodori_model", "irodori_token")
    @classmethod
    def irodori_printable(cls, value):
        if value is not None and (
            not value.isascii() or not value.isprintable() or value != value.strip()
        ):
            raise ValueError("Invalid Irodori configuration")
        return value

    @model_validator(mode="after")
    def irodori_transport(self):
        if self.irodori_config is not None and self.irodori_url is not None:
            raise ValueError("Select exactly one Irodori transport")
        return self

    @classmethod
    def from_env(cls, **overrides) -> "Settings":

        values = {
            field: os.environ["FLAMORIS_" + suffix]
            for suffix, field in cls.env_fields.items()
            if "FLAMORIS_" + suffix in os.environ
        }
        if "FLAMORIS_MODEL_DIRS" in os.environ:
            values["model_dirs"] = json.loads(os.environ["FLAMORIS_MODEL_DIRS"])
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(values)

    def roots(self, kind: ModelKind) -> list[Path]:
        return self.model_dirs.get(kind, [self.model_root / MODEL_FOLDERS[kind]])
