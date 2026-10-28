"""Bounded Wan I2V/FLF2V declarations; production graphs come from the operator."""

import io
import re
from typing import Literal

from pydantic import Field

from .declared_comfy import Declaration, Strict

VIDEO_BYTES = 64 * 1024**2


class VideoContract(Strict):
    mode: Literal["i2v", "flf2v"]
    conditioning_node: str = Field(pattern=r"^[0-9]{1,8}$")


class VideoDeclaration(Declaration):
    version: Literal[2] = 2
    video: VideoContract


def parse_declaration(value):
    if type(value) is dict and value.get("version") == 2:
        return VideoDeclaration.model_validate(value)
    return Declaration.model_validate(value)


def validate_video(graph, decl):
    """Require actual first/last image connections and finite execution budgets.

    This is an execution contract, not evidence that a model follows a pose.
    Trusted node code and a real exported graph still require operator review.
    """
    spec = decl.video
    expected = "WanImageToVideo" if spec.mode == "i2v" else "WanFirstLastFrameToVideo"
    condition = graph.get(spec.conditioning_node, {})
    if condition.get("class_type") != expected:
        raise ValueError("Video mode does not match the declared Wan conditioning node")
    if (
        sum(
            n["class_type"] in {"WanImageToVideo", "WanFirstLastFrameToVideo"}
            for n in graph.values()
        )
        != 1
    ):
        raise ValueError("Video requires exactly one Wan conditioning node")
    roles = {"start_image"} | ({"end_image"} if spec.mode == "flf2v" else set())
    if {k for k, v in decl.parameters.items() if v.role == "managed_input"} != roles:
        raise ValueError("Video mode requires exactly its start/end managed image roles")
    for role in roles:
        binding = decl.parameters[role]
        if len(binding.targets) != 1:
            raise ValueError("Video image roles require distinct single LoadImage bindings")
        target = binding.targets[0]
        if (
            target.input != "image"
            or graph[target.node]["class_type"] != "LoadImage"
            or condition["inputs"].get(role) != [target.node, 0]
        ):
            raise ValueError("Video reference must connect directly to its Wan image input")
    if len({decl.parameters[r].targets[0].node for r in roles}) != len(roles):
        raise ValueError("Start and end images must use distinct graph bindings")
    if condition["inputs"].get("batch_size") != 1:
        raise ValueError("Video supports exactly one output sequence")
    creators = [(k, n) for k, n in graph.items() if n["class_type"] == "CreateVideo"]
    if len(creators) != 1:
        raise ValueError("Video requires exactly one CreateVideo node")
    creator_id, _creator = creators[0]
    output = graph[decl.output.node]["inputs"]
    if (
        output.get("video") != [creator_id, 0]
        or output.get("format") != "mp4"
        or output.get("codec") != "h264"
    ):
        raise ValueError("Video requires a fixed MP4/H.264 SaveVideo output")
    # These semantic parameters must drive the actual graph widgets. A label or
    # unused constant cannot claim to bound width, frame count or playback FPS.
    for key, node, widget, limits in (
        ("width", spec.conditioning_node, "width", (64, 1024)),
        ("height", spec.conditioning_node, "height", (64, 1024)),
        ("frames", spec.conditioning_node, "length", (5, 161)),
        ("fps", creator_id, "fps", (1, 32)),
    ):
        parameter = decl.parameters.get(key)
        if (
            parameter is None
            or parameter.role != "parameter"
            or parameter.type != "integer"
            or len(parameter.targets) != 1
            or parameter.targets[0].model_dump() != {"node": node, "input": widget}
            or parameter.minimum is None
            or parameter.maximum is None
            or parameter.minimum < limits[0]
            or parameter.maximum > limits[1]
        ):
            raise ValueError(f"Video {key} must bind a bounded integer to its semantic widget")
    width, height, frames = (condition["inputs"][k] for k in ("width", "height", "length"))
    fps = graph[creator_id]["inputs"]["fps"]
    if (
        width % 16
        or height % 16
        or frames % 4 != 1
        or frames / fps > 10.1
        or width * height > 832 * 832
    ):
        raise ValueError("Video dimensions/frame count/duration exceed the Wan clip profile")
    for node in graph.values():
        if node["class_type"] in {"KSampler", "KSamplerAdvanced"}:
            steps = node["inputs"].get("steps")
            if type(steps) is not int or not 1 <= steps <= 50:
                raise ValueError("Video sampling steps must be bounded to 1–50")


def video_parameters(graph, decl):
    """The already validated effective values used for output verification."""
    condition = graph[decl.video.conditioning_node]["inputs"]
    creator = next(n for n in graph.values() if n["class_type"] == "CreateVideo")
    return {
        "width": condition["width"],
        "height": condition["height"],
        "frames": condition["length"],
        "fps": creator["inputs"]["fps"],
    }


def owned_video_output(item, job_id):
    if (
        item.get("type") != "output"
        or item.get("subfolder") != "flamoris"
        or type(item.get("filename")) is not str
        or re.fullmatch(re.escape(job_id) + r"_[0-9]+_\.mp4", item["filename"]) is None
    ):
        raise ValueError("Video output is outside the submitted job's MP4 namespace")


def verify_mp4(data, expected):
    """Decode the bounded clip before publishing bytes as a verified MP4 asset.

    PyAV reads memory only; no caller/provider path or URL enters the demuxer.
    Limits are checked before frame decode as well as on every decoded frame.
    """
    import av

    if (
        not 12 <= len(data) <= VIDEO_BYTES
        or data[4:8] != b"ftyp"
        or b"mp4" not in data[8:64]
        and b"isom" not in data[8:64]
    ):
        raise ValueError("Output is not a bounded MP4 container")
    try:
        with av.open(io.BytesIO(data), mode="r", format="mov") as container:
            streams = list(container.streams)
            if len(streams) != 1 or streams[0].type != "video":
                raise ValueError("Video output must contain exactly one video track")
            stream = streams[0]
            if (
                stream.codec_context.name != "h264"
                or (stream.width, stream.height) != (expected["width"], expected["height"])
                or stream.average_rate != expected["fps"]
            ):
                raise ValueError("MP4 codec/dimensions/FPS do not match the submitted recipe")
            count = 0
            for frame in container.decode(stream):
                count += 1
                if (
                    count > expected["frames"]
                    or (frame.width, frame.height) != (expected["width"], expected["height"])
                    or frame.pts is None
                    or abs(float(frame.pts * frame.time_base) - (count - 1) / expected["fps"])
                    > 0.001
                ):
                    raise ValueError("MP4 frames/timestamps do not match the submitted recipe")
            if count != expected["frames"]:
                raise ValueError("MP4 frame count does not match the submitted recipe")
    except av.error.FFmpegError:
        raise ValueError("MP4 could not be decoded") from None
