"""Declarative image graphs validated against installed ComfyUI node specifications.

Installed custom nodes are executable trusted code. An operator allowlist, never
the registration request, selects the classes allowed by this optional profile.
"""

import json
import math
import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import MODEL_FOLDERS
from .models import model_name

MAX_BYTES = 256 * 1024


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Target(Strict):
    node: str = Field(pattern=r"^[0-9]{1,8}$")
    input: str = Field(min_length=1, max_length=128)


class Parameter(Strict):
    type: Literal["string", "integer", "number", "boolean"]
    role: Literal["prompt", "parameter", "managed_input", "model"]
    targets: list[Target] = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=120)
    default: Any = None
    required: bool = False
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    min_length: int = Field(default=0, ge=0, le=20000)
    max_length: int = Field(default=20000, ge=1, le=20000)
    enum: list[str | int | float | bool] | None = Field(default=None, min_length=1, max_length=512)
    model_kind: str | None = None


class Output(Strict):
    node: str = Field(pattern=r"^[0-9]{1,8}$")
    prefix_input: Literal["filename_prefix"] = "filename_prefix"


class Declaration(Strict):
    version: Literal[1] = 1
    parameters: dict[str, Parameter] = Field(min_length=1, max_length=64)
    constants: list[Target] = Field(default_factory=list, max_length=512)
    output: Output


def scalar(value, spec, *, name):
    types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}
    if type(value) is int and abs(value) > 2**64 - 1:
        raise ValueError(f"Parameter {name}: integer exceeds supported range")
    if type(value) not in types[spec.type] or (
        type(value) in (int, float) and not math.isfinite(value)
    ):
        raise ValueError(f"Parameter {name}: expected {spec.type}")
    if spec.enum is not None and not any(
        (type(v) in (int, float) if spec.type == "number" else type(v) is type(value))
        and v == value
        for v in spec.enum
    ):
        raise ValueError(f"Parameter {name}: value outside declared choices")
    if type(value) in (int, float):
        if (spec.minimum is not None and value < spec.minimum) or (
            spec.maximum is not None and value > spec.maximum
        ):
            raise ValueError(f"Parameter {name}: value outside declared range")
    if isinstance(value, str) and not spec.min_length <= len(value) <= spec.max_length:
        raise ValueError(f"Parameter {name}: invalid text length")
    if spec.role == "managed_input" and not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError(f"Parameter {name}: expected managed input ID")
    if spec.role == "model":
        model_name(value)
    return value


def widget_definition(definition):
    """Read legacy choices and V3 static COMBO choices without widening types."""
    kind = definition[0]
    options = definition[1] if len(definition) == 2 else {}
    if type(options) is not dict:
        raise ValueError("Invalid installed node input options")
    if kind == "COMBO":
        choices = options.get("options")
        if (
            type(choices) is not list
            or not choices
            or options.get("multiselect", False) is not False
            or "multi_select" in options
            or options.get("remote") is not None
            or any(type(v) not in (str, int) for v in choices)
        ):
            raise ValueError("Unsupported installed COMBO choices")
        kind = choices
    return kind, options


def widget(value, definition, *, where, placeholder=False):
    """Fail closed for unsupported dynamic/custom widget schemas."""
    if not isinstance(definition, list) or not 1 <= len(definition) <= 2:
        raise ValueError(f"{where}: unsupported node input specification")
    if type(value) is int and abs(value) > 2**64 - 1:
        raise ValueError(f"{where}: integer exceeds supported range")
    kind, options = widget_definition(definition)
    if not isinstance(options, dict):
        raise ValueError(f"{where}: invalid node input options")
    if isinstance(kind, list):
        if value not in kind and not placeholder:
            raise ValueError(f"{where}: dependency or value is not installed/available")
    elif kind in {"STRING", "INT", "FLOAT", "BOOLEAN"}:
        expected = {"STRING": (str,), "INT": (int,), "FLOAT": (int, float), "BOOLEAN": (bool,)}
        if type(value) not in expected[kind] or (
            type(value) in (int, float) and not math.isfinite(value)
        ):
            raise ValueError(f"{where}: expected node input type {kind}")
        if type(value) in (int, float) and (
            ("min" in options and value < options["min"])
            or ("max" in options and value > options["max"])
        ):
            raise ValueError(f"{where}: value outside installed node range")
    else:
        raise ValueError(f"{where}: input requires a typed node connection")


def validate(graph, declaration, node_specs, allowed_nodes):
    from .video import parse_declaration

    decl = parse_declaration(declaration)
    if type(node_specs) is not dict:
        raise ValueError("Installed ComfyUI node specifications are required")
    for schema in node_specs.values():
        if (
            type(schema) is not dict
            or type(schema.get("output")) is not list
            or any(type(v) is not str for v in schema["output"])
            or type(schema.get("input", {})) is not dict
            or type(schema.get("output_node", False)) is not bool
        ):
            raise ValueError("Invalid installed ComfyUI node specification")
        for group in ("required", "optional"):
            fields = schema.get("input", {}).get(group, {})
            if type(fields) is not dict or any(
                type(v) is not list
                or not 1 <= len(v) <= 2
                or (len(v) == 2 and type(v[1]) is not dict)
                for v in fields.values()
            ):
                raise ValueError("Unsupported installed ComfyUI input specification")
            for definition in fields.values():
                kind, _ = widget_definition(definition)
                if type(kind) not in (str, list):
                    raise ValueError("Unsupported installed ComfyUI input type")
                options = definition[1] if len(definition) == 2 else {}
                for bound in ("min", "max"):
                    if bound in options and (
                        type(options[bound]) not in (int, float)
                        or abs(options[bound]) > 2**64 - 1
                        or not math.isfinite(options[bound])
                    ):
                        raise ValueError("Invalid installed ComfyUI numeric range")
                if isinstance(kind, list) and (
                    not kind
                    or any(
                        type(v) not in (str, int, float, bool)
                        or (type(v) in (int, float) and not math.isfinite(v))
                        or (type(v) is str and len(v) > 20000)
                        for v in kind
                    )
                ):
                    raise ValueError("Unsupported installed ComfyUI choices")
    if type(graph) is not dict or not 1 <= len(graph) <= 128:
        raise ValueError("Declared ComfyWorkFlow requires 1–128 nodes")
    if len(json.dumps([graph, declaration], allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("Declared ComfyWorkFlow exceeds byte limit")
    normalized = {}
    for node_id, node in graph.items():
        if (
            type(node_id) is not str
            or not re.fullmatch(r"[0-9]{1,8}", node_id)
            or type(node) is not dict
            or set(node) - {"class_type", "inputs", "_meta"}
            or type(node.get("class_type")) is not str
            or type(node.get("inputs")) is not dict
        ):
            raise ValueError("Invalid declared API-format node")
        cls = node["class_type"]
        if cls not in allowed_nodes:
            raise ValueError(f"Node {node_id} ({cls[:120]}): excluded by operator execution policy")
        if cls not in node_specs:
            raise ValueError(f"Node {node_id} ({cls[:120]}): node is not installed")
        normalized[node_id] = {"class_type": cls, "inputs": deepcopy(node["inputs"])}
    output = decl.output
    video = getattr(decl, "video", None)
    output_class = "SaveVideo" if video else "SaveImage"
    if output.node not in normalized or normalized[output.node]["class_type"] != output_class:
        raise ValueError(f"Output must declare one {output_class} node")
    if video:
        from .video import video_node_specs

        node_specs = video_node_specs(normalized, decl, node_specs)
    covered = {(output.node, output.prefix_input): "output"}
    if sum(spec.role == "managed_input" for spec in decl.parameters.values()) > 4:
        raise ValueError("At most four managed images are supported per job")
    for name, spec in decl.parameters.items():
        if name in {"constant", "output"} or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("Invalid declared parameter name")
        if spec.role in {"prompt", "managed_input", "model"} and spec.type != "string":
            raise ValueError(f"Parameter {name}: role requires string type")
        if (spec.role == "model" and spec.model_kind not in MODEL_FOLDERS) or (
            spec.role != "model" and spec.model_kind is not None
        ):
            raise ValueError(f"Parameter {name}: declare model kind only for model bindings")
        if spec.role == "managed_input":
            if len(name) > 32:
                raise ValueError("Managed image parameter names are limited to 32 characters")
            if not spec.required or spec.default is not None:
                raise ValueError("Managed images must be required and have no default")
        elif spec.default is None:
            if not spec.required:
                raise ValueError(f"Parameter {name}: missing required flag or default")
        else:
            scalar(spec.default, spec, name=name)
        for target in spec.targets:
            key = (target.node, target.input)
            if key in covered:
                raise ValueError("Overlapping ComfyWorkFlow bindings")
            covered[key] = name
            node = normalized.get(target.node)
            schema = node_specs[node["class_type"]] if node else {}
            fields = {
                **schema.get("input", {}).get("required", {}),
                **schema.get("input", {}).get("optional", {}),
            }
            definition = fields.get(target.input)
            if not definition:
                raise ValueError(f"Parameter {name}: missing node input target")
            kind, options = widget_definition(definition)
            if isinstance(kind, list) and spec.role != "managed_input":
                choices = kind if spec.enum is None else [v for v in spec.enum if v in kind]
                if not choices or len(choices) > 512:
                    raise ValueError(f"Parameter {name}: unsupported or empty node choices")
                spec.enum = choices
            elif kind in ("INT", "FLOAT"):
                if spec.type not in ({"integer"} if kind == "INT" else {"integer", "number"}):
                    raise ValueError(f"Parameter {name}: declaration/node type mismatch")
                if "min" in options:
                    spec.minimum = max(
                        spec.minimum if spec.minimum is not None else options["min"], options["min"]
                    )
                if "max" in options:
                    spec.maximum = min(
                        spec.maximum if spec.maximum is not None else options["max"], options["max"]
                    )
            elif kind in ("STRING", "BOOLEAN") and spec.type != (
                "string" if kind == "STRING" else "boolean"
            ):
                raise ValueError(f"Parameter {name}: declaration/node type mismatch")
    for target in decl.constants:
        key = (target.node, target.input)
        if key in covered:
            raise ValueError("Overlapping constant binding")
        covered[key] = "constant"
    edges = {}
    seen = set()
    for node_id, node in normalized.items():
        schema = node_specs[node["class_type"]]
        if not isinstance(schema, dict) or type(schema.get("output")) is not list:
            raise ValueError(f"Node {node_id}: invalid installed specification")
        if bool(schema.get("output_node")) != (node_id == output.node):
            raise ValueError(f"Node {node_id}: undeclared output/side-effect node")
        groups = schema.get("input", {})
        required = groups.get("required", {})
        available = {**required, **groups.get("optional", {})}
        if set(required) - set(node["inputs"]) or set(node["inputs"]) - set(available):
            raise ValueError(f"Node {node_id}: missing or unknown inputs")
        edges[node_id] = []
        for key, value in node["inputs"].items():
            target = (node_id, key)
            where = f"Node {node_id} input {key}"
            if type(value) is list:
                if (
                    target in covered
                    or len(value) != 2
                    or type(value[0]) is not str
                    or type(value[1]) is not int
                ):
                    raise ValueError(f"{where}: invalid connection/binding")
                source = normalized.get(value[0])
                outputs = node_specs[source["class_type"]]["output"] if source else []
                if not 0 <= value[1] < len(outputs) or available[key][0] != outputs[value[1]]:
                    raise ValueError(f"{where}: connection type or slot mismatch")
                edges[node_id].append(value[0])
                continue
            if target not in covered:
                raise ValueError(f"{where}: undeclared literal input")
            seen.add(target)
            binding = covered[target]
            placeholder = (
                binding not in {"constant", "output"}
                and decl.parameters[binding].role == "managed_input"
            )
            if placeholder and value != "$" + binding:
                raise ValueError(f"{where}: expected managed image placeholder")
            if node["class_type"] == "LoadImage" and key == "image" and not placeholder:
                raise ValueError(f"{where}: file loading requires a managed image binding")
            if (
                binding == "constant"
                and isinstance(value, str)
                and (
                    len(value) > 1024
                    or any(c in value for c in ("/", "\\", ":"))
                    or value.startswith("$")
                )
            ):
                raise ValueError(f"{where}: paths/URLs/placeholders are not constant widgets")
            if binding == "output" and value != "flamoris":
                raise ValueError("Output prefix must be Controller-owned flamoris")
            if binding not in {"constant", "output"} and not placeholder:
                spec = decl.parameters[binding]
                scalar(value, spec, name=binding)
                if spec.default is not None and value != spec.default:
                    raise ValueError(f"Parameter {binding}: graph/default mismatch")
            widget(value, available[key], where=where, placeholder=placeholder)
    if set(covered) != seen:
        raise ValueError("Binding targets an absent input or a connection")
    visited, visiting = set(), set()

    def visit(node):
        if node in visiting:
            raise ValueError("ComfyWorkFlow contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for source in edges[node]:
            visit(source)
        visiting.remove(node)
        visited.add(node)

    visit(output.node)
    if visited != set(normalized):
        raise ValueError("ComfyWorkFlow contains nodes unreachable from declared image output")
    if video:
        from .video import validate_video

        validate_video(normalized, decl)
    return normalized, decl


def values(decl, parameters, catalog):
    if type(parameters) is not dict or set(parameters) - set(decl.parameters):
        raise ValueError("Unknown declared ComfyWorkFlow parameters")
    result = {}
    for name, spec in decl.parameters.items():
        value = parameters.get(name, spec.default)
        if value is None:
            raise ValueError(f"Missing declared parameter {name}")
        result[name] = scalar(value, spec, name=name)
        if spec.role == "model":
            catalog.require(spec.model_kind, value)
    return result


def render(graph, decl, parameters, *, job_id=None):
    prompt = deepcopy(graph)
    for name, value in parameters.items():
        spec = decl.parameters[name]
        if spec.role != "managed_input":
            for target in spec.targets:
                prompt[target.node]["inputs"][target.input] = value
    prompt[decl.output.node]["inputs"][decl.output.prefix_input] = "flamoris" + (
        "/" + job_id if job_id else ""
    )
    return prompt
