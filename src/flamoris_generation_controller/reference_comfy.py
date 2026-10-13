"""Bounded ComfyUI reference conditioning; graphs execute only in ComfyUI."""

from pydantic import BaseModel, ConfigDict

from .workflows import Conditioning, Parameters, ReferenceRecipe

IMAGE_ROLES = ("initial_image", "character_image", "style_image", "control_image")
IMAGE_NODES = dict(zip(IMAGE_ROLES, ("8", "20", "30", "40"), strict=True))
MODEL_KINDS = {
    "character_adapter": "ipadapter",
    "style_adapter": "ipadapter",
    "clip_vision": "clip_vision",
    "controlnet": "controlnet",
}


class ReferenceDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parameters: Parameters
    conditioning: Conditioning
    roles: tuple[str, ...]


def conditioning_prompt(parameters, conditioning, roles):
    from .registered_comfy import image_prompt

    roles = set(roles)
    expected = (
        ({"character_image"} if conditioning.character_adapter else set())
        | ({"style_image"} if conditioning.style_adapter else set())
        | ({"control_image"} if conditioning.controlnet else set())
    )
    if roles - {"initial_image"} != expected or parameters.loras:
        raise ValueError("Reference roles do not match the declared conditioning")
    prompt = image_prompt(parameters, reference="initial_image" in roles)
    if "initial_image" in roles:
        prompt["8"]["inputs"]["image"] = "$initial_image"
    if conditioning.clip_vision:
        prompt["11"] = {
            "class_type": "CLIPVisionLoader",
            "inputs": {"clip_name": conditioning.clip_vision},
        }
    model = ["1", 0]
    for role, image_id, loader_id, apply_id in (
        ("character", "20", "21", "22"),
        ("style", "30", "31", "32"),
    ):
        adapter = getattr(conditioning, role + "_adapter")
        if not adapter:
            continue
        prompt[image_id] = {"class_type": "LoadImage", "inputs": {"image": "$" + role + "_image"}}
        prompt[loader_id] = {
            "class_type": "IPAdapterModelLoader",
            "inputs": {"ipadapter_file": adapter},
        }
        prompt[apply_id] = {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": model,
                "ipadapter": [loader_id, 0],
                "clip_vision": ["11", 0],
                "image": [image_id, 0],
                "weight": getattr(conditioning, role + "_strength"),
                "weight_type": "linear" if role == "character" else "style transfer",
                "combine_embeds": "concat",
                "start_at": getattr(conditioning, role + "_start"),
                "end_at": getattr(conditioning, role + "_end"),
                "embeds_scaling": "V only",
            },
        }
        model = [apply_id, 0]
    prompt["5"]["inputs"]["model"] = model
    if conditioning.controlnet:
        prompt["40"] = {"class_type": "LoadImage", "inputs": {"image": "$control_image"}}
        prompt["41"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["40", 0],
                "upscale_method": "nearest-exact",
                "width": parameters.width,
                "height": parameters.height,
                "crop": "disabled",
            },
        }
        prompt["42"] = {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": conditioning.controlnet},
        }
        prompt["43"] = {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["2", 0],
                "negative": ["3", 0],
                "control_net": ["42", 0],
                "image": ["41", 0],
                "strength": conditioning.control_strength,
                "start_percent": conditioning.control_start,
                "end_percent": conditioning.control_end,
                "vae": ["1", 2],
            },
        }
        prompt["5"]["inputs"].update(positive=["43", 0], negative=["43", 1])
    return prompt


def canonical_graph(graph):
    """Renumber a bounded reachable DAG, preserving sharing and every output index."""
    if type(graph) is not dict or not 7 <= len(graph) <= 24:
        raise ValueError("Unsupported reference graph size")
    outputs = [key for key, node in graph.items() if node.get("class_type") == "SaveImage"]
    if len(outputs) != 1:
        raise ValueError("Reference graph requires one output")
    ids, active, result = {}, set(), {}

    def visit(key):
        if key in active:
            raise ValueError("Cyclic ComfyWorkFlow")
        if key in ids:
            return ids[key]
        node = graph[key]
        if set(node) - {"class_type", "inputs", "_meta"} or type(node.get("inputs")) is not dict:
            raise ValueError("Invalid reference node")
        ids[key] = str(len(ids) + 1)
        active.add(key)
        values = {}
        for name, value in sorted(node["inputs"].items()):
            if type(value) is list:
                if (
                    len(value) != 2
                    or type(value[0]) is not str
                    or value[0] not in graph
                    or type(value[1]) is not int
                ):
                    raise ValueError("Invalid reference link")
                value = [visit(value[0]), value[1]]
            values[name] = value
        result[ids[key]] = {"class_type": node["class_type"], "inputs": values}
        active.remove(key)
        return ids[key]

    visit(outputs[0])
    if len(ids) != len(graph):
        raise ValueError("Unused reference graph node")
    return result


def validate_reference_graph(graph):
    """Extract only declared widgets, then compare the entire graph to our builder."""
    normalized = canonical_graph(graph)

    def unique(kind):
        values = [n["inputs"] for n in graph.values() if n["class_type"] == kind]
        if len(values) != 1:
            raise ValueError("Unsupported reference node count")
        return values[0]

    sampler = unique("KSampler")
    roles = {}
    for key, node in graph.items():
        if node["class_type"] == "LoadImage":
            marker = node["inputs"].get("image")
            if marker not in {"$" + role for role in IMAGE_ROLES} or marker[1:] in roles:
                raise ValueError("Reference images must use distinct declared placeholders")
            roles[marker[1:]] = key
    if not roles or not set(roles) - {"initial_image"}:
        raise ValueError("Conditioning graph requires IPAdapter or ControlNet")
    conditioning = {}
    for role in ("character", "style"):
        if role + "_image" in roles:
            applies = [
                n["inputs"]
                for n in graph.values()
                if n["class_type"] == "IPAdapterAdvanced"
                and n["inputs"].get("image") == [roles[role + "_image"], 0]
            ]
            if len(applies) != 1:
                raise ValueError("Reference must reach one IPAdapter")
            value = applies[0]
            loader = graph[value["ipadapter"][0]]
            if loader["class_type"] != "IPAdapterModelLoader":
                raise ValueError("Explicit IPAdapter loader required")
            conditioning[role + "_adapter"] = loader["inputs"]["ipadapter_file"]
            for source, target in (
                ("weight", "strength"),
                ("start_at", "start"),
                ("end_at", "end"),
            ):
                conditioning[role + "_" + target] = value[source]
    if conditioning:
        conditioning["clip_vision"] = unique("CLIPVisionLoader")["clip_name"]
    text = sampler
    if "control_image" in roles:
        text = unique("ControlNetApplyAdvanced")
        conditioning["controlnet"] = unique("ControlNetLoader")["control_net_name"]
        for source, target in (
            ("strength", "strength"),
            ("start_percent", "start"),
            ("end_percent", "end"),
        ):
            conditioning["control_" + target] = text[source]
    dimensions = (
        unique("EmptyLatentImage")
        if "initial_image" not in roles
        else next(
            n["inputs"]
            for n in graph.values()
            if n["class_type"] == "ImageScale"
            and n["inputs"].get("image") == [roles["initial_image"], 0]
        )
    )
    params = Parameters.model_validate(
        {
            "checkpoint": unique("CheckpointLoaderSimple")["ckpt_name"],
            "positive_prompt": graph[text["positive"][0]]["inputs"]["text"],
            "negative_prompt": graph[text["negative"][0]]["inputs"]["text"],
            "width": dimensions["width"],
            "height": dimensions["height"],
            **{key: sampler[key] for key in ("seed", "steps", "cfg", "scheduler", "denoise")},
            "sampler": sampler["sampler_name"],
        }
    )
    controls = Conditioning.model_validate(conditioning)
    if normalized != canonical_graph(conditioning_prompt(params, controls, roles)):
        raise ValueError("Unsupported reference graph topology or widgets")
    return (
        normalized,
        ReferenceDefaults(
            parameters=params,
            conditioning=controls,
            roles=tuple(role for role in IMAGE_ROLES if role in roles),
        ),
        True,
    )


def active_fields(defaults):
    fields = {}
    for key, kind in MODEL_KINDS.items():
        value = getattr(defaults.conditioning, key)
        if value:
            fields[key] = {
                "type": "string",
                "model_kind": kind,
                "enum": [value],
                "default": value,
                "required": False,
            }
    for role, model in (
        ("character", "character_adapter"),
        ("style", "style_adapter"),
        ("control", "controlnet"),
    ):
        if getattr(defaults.conditioning, model):
            for suffix in ("strength", "start", "end"):
                key = role + "_" + suffix
                fields[key] = {
                    "type": "number",
                    "minimum": 0,
                    "maximum": (2 if role == "control" else 5) if suffix == "strength" else 1,
                    "default": getattr(defaults.conditioning, key),
                    "required": False,
                }
    return fields


def build_reference(definition_id, defaults, values):
    values = dict(values)
    images = {role: values.pop(role) for role in defaults.roles if role in values}
    controls = {key: values.pop(key) for key in active_fields(defaults) if key in values}
    if values.get("checkpoint", defaults.parameters.checkpoint) != defaults.parameters.checkpoint:
        raise ValueError("Register a new ComfyWorkFlow to change the checkpoint")
    if any(
        key in controls and controls[key] != getattr(defaults.conditioning, key)
        for key in MODEL_KINDS
    ):
        raise ValueError("Register a new ComfyWorkFlow to change conditioning models")
    parameters = Parameters.model_validate({**defaults.parameters.model_dump(), **values})
    conditioning = Conditioning.model_validate({**defaults.conditioning.model_dump(), **controls})
    if set(images) != set(defaults.roles) or parameters.loras:
        raise ValueError("All declared reference images are required; LoRAs are unsupported")
    return ReferenceRecipe(
        definition_id=definition_id, parameters=parameters, conditioning=conditioning, images=images
    )

