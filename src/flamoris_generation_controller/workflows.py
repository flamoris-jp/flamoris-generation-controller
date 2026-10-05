"""Trusted template builders and saved parameter recipes, not a raw node editing API."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ModelCatalog, model_name
from .music import (
    MUSIC_CAPABILITY,
    MUSIC_PROVIDER,
    MUSIC_TEMPLATE,
    MusicRecipe,
    music_descriptor,
    music_profile,
)
from .speech import (
    SPEECH_CAPABILITY,
    SPEECH_PROVIDER,
    SPEECH_TEMPLATE,
    SpeechRecipe,
    speech_descriptor,
    speech_profile,
)
from .transcription import (
    TRANSCRIPTION_CAPABILITY,
    TRANSCRIPTION_PROVIDER,
    TRANSCRIPTION_TEMPLATE,
    TranscriptionRecipe,
    transcription_descriptor,
    transcription_profile,
)

Template = Literal["text-to-image", "text-to-image-lora"]
MAX_RECIPE_BYTES = 256 * 1024
MAX_DISCOVERY_BYTES = 256 * 1024
TEMPLATES = [
    {"id": "text-to-image", "description": "Checkpoint-based text-to-image; no LoRAs"},
    {"id": "text-to-image-lora", "description": "Text-to-image with an ordered LoRA chain"},
]


class Lora(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    strength_model: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)
    strength_clip: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)

    _name = field_validator("name")(model_name)


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    checkpoint: str
    positive_prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=20000)
    width: int = Field(default=512, ge=64, le=4096, multiple_of=8, strict=True)
    height: int = Field(default=512, ge=64, le=4096, multiple_of=8, strict=True)
    seed: int = Field(default=0, ge=0, le=2**64 - 1, strict=True)
    steps: int = Field(default=20, ge=1, le=150, strict=True)
    cfg: float = Field(default=7, ge=0, le=100, allow_inf_nan=False)
    sampler: str = Field(default="euler", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    scheduler: str = Field(default="normal", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    denoise: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    loras: Annotated[tuple[Lora, ...], Field(max_length=16)] = ()

    _checkpoint = field_validator("checkpoint")(model_name)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    template: Template
    parameters: Parameters


AnyRecipe = Recipe | SpeechRecipe | MusicRecipe | TranscriptionRecipe
NATIVE_RECIPES = (SpeechRecipe, MusicRecipe, TranscriptionRecipe)


def builtin_descriptors() -> list[dict]:
    """Graph-free descriptions of the existing builtin contracts."""
    properties = Parameters.model_json_schema()["properties"]
    parameters = {}
    for name, schema in properties.items():
        if name == "loras":
            continue
        spec = {
            k: v
            for k, v in schema.items()
            if k in {"type", "default", "minimum", "maximum", "minLength", "maxLength", "pattern"}
        }
        for source, target in (("minLength", "min_length"), ("maxLength", "max_length")):
            if source in spec:
                spec[target] = spec.pop(source)
        if "multipleOf" in schema:
            spec["multiple_of"] = int(schema["multipleOf"])
        parameters[name] = {
            **spec,
            "role": name,
            "required": name in properties and "default" not in schema,
        }
    parameters["checkpoint"]["model_kind"] = "checkpoint"
    loras = {
        "type": "ordered_loras",
        "role": "loras",
        "max_items": 16,
        "model_kind": "lora",
        "items": {
            "name": {"type": "string", "model_kind": "lora"},
            "strength_model": {"type": "number", "minimum": -20, "maximum": 20, "default": 1},
            "strength_clip": {"type": "number", "minimum": -20, "maximum": 20, "default": 1},
        },
    }
    return [
        {
            **template,
            "kind": "builtin",
            "metadata_schema_version": 2,
            "version": 1,
            "name": template["id"],
            "provider_id": "comfyui",
            "capability_id": "image.generate",
            "image": {
                "profile": "image-v1",
                "mode": "txt2img",
                "dimensions": {"mode": "parameters"},
            },
            "readiness": {
                "state": "ready",
                "basis": "builtin_compatibility",
                "descriptor_revision": 1,
            },
            "parameters": {
                **parameters,
                "loras": {
                    **loras,
                    "min_items": 1 if template["id"].endswith("-lora") else 0,
                    "max_items": 16 if template["id"].endswith("-lora") else 0,
                    "required": template["id"].endswith("-lora"),
                },
            },
        }
        for template in TEMPLATES
    ]


def build_prompt(recipe: Recipe, catalog: ModelCatalog) -> dict:
    p = recipe.parameters
    catalog.require("checkpoint", p.checkpoint)
    if recipe.template == "text-to-image" and p.loras:
        raise ValueError("Use text-to-image-lora for LoRAs")
    if recipe.template == "text-to-image-lora" and not p.loras:
        raise ValueError("text-to-image-lora requires at least one LoRA")
    nodes = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": p.checkpoint}}}
    model, clip = ["1", 0], ["1", 1]
    for index, lora in enumerate(p.loras, start=10):
        catalog.require("lora", lora.name)
        key = str(index)
        nodes[key] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": model,
                "clip": clip,
                "lora_name": lora.name,
                "strength_model": lora.strength_model,
                "strength_clip": lora.strength_clip,
            },
        }
        model, clip = [key, 0], [key, 1]
    nodes.update(
        {
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": p.positive_prompt, "clip": clip},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": p.negative_prompt, "clip": clip},
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": p.width, "height": p.height, "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": model,
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                    "seed": p.seed,
                    "steps": p.steps,
                    "cfg": p.cfg,
                    "sampler_name": p.sampler,
                    "scheduler": p.scheduler,
                    "denoise": p.denoise,
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {
                "class_type": "SaveImage",
                "inputs": {"images": ["6", 0], "filename_prefix": "flamoris"},
            },
        }
    )
    return nodes


def checked_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Invalid workflow or job ID")
    return value


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def decode_recipe(content: bytes) -> dict:
    """Retain strict JSON parsing independently of retired definition machinery."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate recipe key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("Nonfinite recipe value")

    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


class RetiredRecipeError(ValueError):
    """A fixed public rejection; old recipes never fall back to builtin execution."""


class WorkflowStore:
    """Original builtin/native parameter recipes; custom graph definitions are retired."""

    def __init__(
        self,
        catalog: ModelCatalog,
        directory: Path,
        *,
        speech_enabled=False,
        music_enabled=False,
        transcription_enabled=False,
    ):
        self.catalog = catalog
        self.directory = directory
        self._recipes: dict[str, AnyRecipe] = {}
        self.speech_enabled = speech_enabled
        self.music_enabled = music_enabled
        self.transcription_enabled = transcription_enabled

    def build(
        self,
        template: str,
        parameters: Parameters | dict[str, Any],
        definition_version: int | None = None,
        definition_digest: str | None = None,
        require_ready: bool = False,
    ) -> dict:
        # Keep old optional wire arguments so callers receive a clear retirement
        # error rather than losing readiness gates through silent fallback.
        if type(require_ready) is not bool or (
            definition_version is not None and type(definition_version) is not int
        ):
            raise ValueError("Invalid build preconditions")
        if definition_version is not None or definition_digest is not None or require_ready:
            raise RetiredRecipeError(
                "Custom definitions and attestation are retired; use builtin recipes"
            )
        native = {
            SPEECH_TEMPLATE: (self.speech_enabled, SpeechRecipe),
            MUSIC_TEMPLATE: (self.music_enabled, MusicRecipe),
            TRANSCRIPTION_TEMPLATE: (self.transcription_enabled, TranscriptionRecipe),
        }
        if template in native:
            enabled, recipe_type = native[template]
            if not enabled:
                raise ValueError("Native recipe is disabled")
            recipe = recipe_type(parameters=parameters)
            prompt = None
        elif template in ("text-to-image", "text-to-image-lora"):
            recipe = Recipe(template=template, parameters=Parameters.model_validate(parameters))
            prompt = build_prompt(recipe, self.catalog)
        else:
            raise RetiredRecipeError(
                "Custom ComfyWorkFlow definitions are retired; unknown builtin recipe"
            )
        if len(self._recipes) >= 1024:
            raise ValueError("Recipe session is full; save needed recipes and restart")
        workflow_id = uuid4().hex
        self._recipes[workflow_id] = recipe
        return {
            "workflow_id": workflow_id,
            **recipe.model_dump(mode="json"),
            "prompt": prompt,
            **({"speech": speech_profile()} if isinstance(recipe, SpeechRecipe) else {}),
            **({"music": music_profile()} if isinstance(recipe, MusicRecipe) else {}),
            **(
                {"transcription": transcription_profile()}
                if isinstance(recipe, TranscriptionRecipe)
                else {}
            ),
        }

    def get(self, workflow_id: str) -> AnyRecipe:
        checked_id(workflow_id)
        if workflow_id in self._recipes:
            return self._recipes[workflow_id]
        path = self.directory / f"{workflow_id}.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown workflow ID")
        if path.stat().st_size > MAX_RECIPE_BYTES:
            raise ValueError("Saved recipe is too large")
        try:
            data = decode_recipe(path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid saved recipe") from exc
        return self.parse(data)

    def parse(self, data: dict) -> AnyRecipe:
        if not isinstance(data, dict):
            raise ValueError("Invalid saved recipe")
        schema = data.get("schema_version", 1)
        if type(schema) is not int:
            raise ValueError("Invalid recipe schema")
        if schema in (2, 3):
            raise RetiredRecipeError("Custom ComfyWorkFlow recipe execution is retired")
        native = {
            4: (self.speech_enabled, SpeechRecipe),
            5: (self.music_enabled, MusicRecipe),
            6: (self.transcription_enabled, TranscriptionRecipe),
        }
        if schema in native:
            enabled, recipe_type = native[schema]
            if not enabled:
                raise ValueError("Native recipe is disabled")
            return recipe_type.model_validate(data)
        return Recipe.model_validate(data)

    def _require_supported_recipe(self, recipe: AnyRecipe) -> None:
        """Recheck enabled native routes without mutable provider graph authority."""
        if isinstance(recipe, NATIVE_RECIPES):
            enabled = {
                SpeechRecipe: self.speech_enabled,
                MusicRecipe: self.music_enabled,
                TranscriptionRecipe: self.transcription_enabled,
            }[type(recipe)]
            if not enabled:
                raise ValueError("Native recipe is disabled")
        elif not isinstance(recipe, Recipe):
            raise RetiredRecipeError("Custom ComfyWorkFlow recipe execution is retired")

    def prompt(self, recipe: AnyRecipe, job_id: str | None = None) -> dict:
        if isinstance(recipe, NATIVE_RECIPES):
            raise ValueError("Native recipe has no ComfyUI prompt")
        self._require_supported_recipe(recipe)
        prompt = build_prompt(recipe, self.catalog)
        if job_id is not None:
            prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        return prompt

    def routing(self, recipe: AnyRecipe) -> tuple[str, str]:
        self._require_supported_recipe(recipe)
        if isinstance(recipe, NATIVE_RECIPES):
            return {
                SpeechRecipe: (SPEECH_PROVIDER, SPEECH_CAPABILITY),
                MusicRecipe: (MUSIC_PROVIDER, MUSIC_CAPABILITY),
                TranscriptionRecipe: (TRANSCRIPTION_PROVIDER, TRANSCRIPTION_CAPABILITY),
            }[type(recipe)]
        return "comfyui", "image.generate"

    def save(self, workflow_id: str) -> dict:
        recipe = self.get(workflow_id)
        path = self.directory / f"{workflow_id}.json"
        content = recipe.model_dump_json(indent=2).encode()
        if len(content) > MAX_RECIPE_BYTES:
            raise ValueError("Saved recipe is too large")
        atomic_write(path, content)
        return {"workflow_id": workflow_id, "file": str(path), "saved": True}

    def list(self) -> dict:
        descriptors = builtin_descriptors()
        if self.speech_enabled:
            descriptors.append(speech_descriptor())
        if self.music_enabled:
            descriptors.append(music_descriptor())
        if self.transcription_enabled:
            descriptors.append(transcription_descriptor())
        saved = []
        for path in sorted(self.directory.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{32}", path.stem) and not path.is_symlink():
                saved.append(path.stem)
        # Saved IDs remain discoverable, including retired recipes. Listing never
        # executes/reinterprets/deletes them; get/build report explicit retirement.
        result = {
            "templates": TEMPLATES,
            "descriptors": descriptors,
            "definitions": [],
            "built_workflows": list(self._recipes),
            "saved_workflows": saved,
        }
        if len(json.dumps(result, indent=2).encode("utf-8")) > MAX_DISCOVERY_BYTES:
            raise ValueError("Recipe discovery exceeds byte limit")
        return result
