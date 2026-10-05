"""Immutable, bounded checkpoint ComfyWorkFlow profiles; no arbitrary node execution."""

import hashlib
import json
import re
from copy import deepcopy

from .workflows import (
    Parameters,
    Recipe,
    RegisteredRecipe,
    atomic_write,
    build_prompt,
    decode_recipe,
)

MAX_DEFINITION_BYTES = 256 * 1024
MAX_DEFINITIONS = 64


def image_prompt(parameters, *, reference=False):
    # This catalog only validates the graph's structure. Actual model presence is
    # checked by WorkflowStore at build and again immediately before submission.
    class Names:
        def require(self, *_args):
            return None

    prompt = build_prompt(Recipe(template="text-to-image", parameters=parameters), Names())
    if reference:
        prompt.pop("4")
        prompt.update(
            {
                "8": {"class_type": "LoadImage", "inputs": {"image": "$reference_image"}},
                "9": {
                    "class_type": "ImageScale",
                    "inputs": {
                        "image": ["8", 0],
                        "upscale_method": "lanczos",
                        "width": parameters.width,
                        "height": parameters.height,
                        "crop": "disabled",
                    },
                },
                "10": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["1", 2]}},
            }
        )
        prompt["5"]["inputs"]["latent_image"] = ["10", 0]
    return prompt


def validate_graph(graph):
    """Normalize arbitrary numeric node IDs, then require the reviewed exact topology.

    No caller filename, executable/custom class, extra widget, second sampler or
    unused side-effect node can pass this comparison. Parameter widgets are the
    only varying values. A reference slot is a literal placeholder, never a path.
    """
    if type(graph) is not dict or not 7 <= len(graph) <= 10:
        raise ValueError("Unsupported ComfyWorkFlow graph profile")
    if len(json.dumps(graph, allow_nan=False).encode()) > MAX_DEFINITION_BYTES:
        raise ValueError("ComfyWorkFlow graph exceeds byte limit")
    classes = {}
    for node_id, node in graph.items():
        if (
            type(node_id) is not str
            or not re.fullmatch(r"[0-9]{1,8}", node_id)
            or type(node) is not dict
            or set(node) - {"class_type", "inputs", "_meta"}
            or type(node.get("inputs")) is not dict
            or type(node.get("class_type")) is not str
        ):
            raise ValueError("Invalid ComfyWorkFlow API-format node")
        classes.setdefault(node["class_type"], []).append(node_id)
    reference = "LoadImage" in classes
    expected = {
        "CheckpointLoaderSimple": "1",
        "EmptyLatentImage": "4",
        "KSampler": "5",
        "VAEDecode": "6",
        "SaveImage": "7",
    }
    if reference:
        expected.pop("EmptyLatentImage")
        expected.update({"LoadImage": "8", "ImageScale": "9", "VAEEncode": "10"})
    if set(classes) != {*expected, "CLIPTextEncode"} or any(
        len(classes[k]) != (2 if k == "CLIPTextEncode" else 1) for k in classes
    ):
        raise ValueError("Unsupported ComfyWorkFlow node classes or counts")
    ids = {classes[k][0]: value for k, value in expected.items()}
    sampler = graph[classes["KSampler"][0]]["inputs"]
    try:
        positive, negative = sampler["positive"], sampler["negative"]
        if (
            type(positive) is not list
            or type(negative) is not list
            or positive[1:] != [0]
            or negative[1:] != [0]
            or set((positive[0], negative[0])) != set(classes["CLIPTextEncode"])
        ):
            raise ValueError()
        ids.update({positive[0]: "2", negative[0]: "3"})
        normalized = {}
        for node_id, node in graph.items():
            inputs = {}
            for key, value in node["inputs"].items():
                if isinstance(value, list):
                    if len(value) != 2 or type(value[0]) is not str or type(value[1]) is not int:
                        raise ValueError()
                    value = [ids[value[0]], value[1]]
                inputs[key] = value
            normalized[ids[node_id]] = {"class_type": node["class_type"], "inputs": inputs}
        dimensions = normalized["9" if reference else "4"]["inputs"]
        parameters = Parameters.model_validate(
            {
                "checkpoint": normalized["1"]["inputs"]["ckpt_name"],
                "positive_prompt": normalized["2"]["inputs"]["text"],
                "negative_prompt": normalized["3"]["inputs"]["text"],
                "width": dimensions["width"],
                "height": dimensions["height"],
                **{k: sampler[k] for k in ("seed", "steps", "cfg", "scheduler", "denoise")},
                "sampler": sampler["sampler_name"],
            }
        )
        if normalized != image_prompt(parameters, reference=reference):
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("ComfyWorkFlow does not match the bounded checkpoint profile") from None
    return normalized, parameters, reference


class ComfyDefinitions:
    def __init__(self, directory):
        self.directory = directory / "comfy-definitions-v1"

    def register(self, name, graph):
        if type(name) is not str or not 1 <= len(name.strip()) <= 120:
            raise ValueError("Invalid ComfyWorkFlow display name")
        normalized, _parameters, _reference = validate_graph(graph)
        raw = json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.directory / (digest + ".json")
        if path.is_symlink():
            raise ValueError("Invalid ComfyWorkFlow definition storage")
        duplicate = path.exists()
        if duplicate:
            self.get("comfy-" + digest)
        else:
            if len(list(self.directory.glob("*.json"))) >= MAX_DEFINITIONS:
                raise ValueError("ComfyWorkFlow definition capacity exceeded")
            record = {"name": name.strip(), "graph": normalized}
            atomic_write(path, json.dumps(record, ensure_ascii=False).encode())
        return {**self.descriptor("comfy-" + digest), "duplicate": duplicate}

    def get(self, definition_id):
        if type(definition_id) is not str or not re.fullmatch(r"comfy-[a-f0-9]{64}", definition_id):
            raise ValueError("Invalid ComfyWorkFlow definition ID")
        path = self.directory / (definition_id[6:] + ".json")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DEFINITION_BYTES:
            raise ValueError("Unknown or invalid ComfyWorkFlow definition")
        record = decode_recipe(path.read_bytes())
        if (
            type(record) is not dict
            or set(record) != {"name", "graph"}
            or type(record["name"]) is not str
            or not 1 <= len(record["name"].strip()) <= 120
        ):
            raise ValueError("Invalid ComfyWorkFlow definition")
        graph, parameters, reference = validate_graph(record["graph"])
        digest = hashlib.sha256(
            json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if "comfy-" + digest != definition_id:
            raise ValueError("ComfyWorkFlow definition digest mismatch")
        return record["name"], graph, parameters, reference

    def descriptor(self, definition_id):
        from .workflows import builtin_descriptors

        name, _graph, defaults, reference = self.get(definition_id)
        base = deepcopy(builtin_descriptors()[0])
        base.update(
            {
                "id": definition_id,
                "name": name,
                "kind": "registered-comfy",
                "definition_digest": definition_id[6:],
                "definition_version": 1,
                "readiness": {
                    "state": "validated",
                    "basis": "static_checkpoint_profile",
                    "live_provider_verified": False,
                },
            }
        )
        base["image"].update(
            {"profile": "checkpoint-comfy-v1", "mode": "img2img" if reference else "txt2img"}
        )
        for key, spec in base["parameters"].items():
            spec.update({"required": False, "default": getattr(defaults, key)})
        if reference:
            base["parameters"]["reference_image"] = {
                "type": "string",
                "role": "managed_input",
                "required": True,
                "pattern": "^[a-f0-9]{32}$",
                "mime_types": ["image/png", "image/jpeg", "image/webp"],
            }
            base["image"]["reference_semantics"] = "init_image"
            base["image"]["resize_policy"] = "lanczos-no-crop"
        return base

    def list(self):
        paths = sorted(self.directory.glob("*.json"))
        if len(paths) > MAX_DEFINITIONS:
            raise ValueError("ComfyWorkFlow definition capacity exceeded")
        return [self.descriptor("comfy-" + path.stem) for path in paths]

    def inspect(self, definition_id):
        _name, graph, defaults, _reference = self.get(definition_id)
        return {**self.descriptor(definition_id), "graph": graph, "defaults": defaults.model_dump()}

    def build(self, definition_id, parameters):
        _name, _graph, defaults, reference = self.get(definition_id)
        values = dict(parameters)
        input_id = values.pop("reference_image", None)
        if (
            reference != (input_id is not None)
            or input_id is not None
            and (type(input_id) is not str or not re.fullmatch(r"[a-f0-9]{32}", input_id))
        ):
            raise ValueError("ComfyWorkFlow requires exactly its declared reference image input")
        params = Parameters.model_validate({**defaults.model_dump(), **values})
        if params.loras:
            raise ValueError("Registered checkpoint profiles do not accept LoRAs")
        return RegisteredRecipe(
            definition_id=definition_id, parameters=params, reference_image=input_id
        )

    def prompt(self, recipe, job_id=None):
        _name, _graph, _defaults, reference = self.get(recipe.definition_id)
        if reference != (recipe.reference_image is not None):
            raise ValueError("ComfyWorkFlow reference binding mismatch")
        prompt = image_prompt(recipe.parameters, reference=reference)
        if job_id is not None:
            prompt["7"]["inputs"]["filename_prefix"] = "flamoris/" + job_id
        return prompt
