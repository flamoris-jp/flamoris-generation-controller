"""Write a supported ComfyUI API graph using explicitly selected model names."""

import argparse
import json

from flamoris_generation_controller.reference_comfy import conditioning_prompt
from flamoris_generation_controller.registered_comfy import image_prompt
from flamoris_generation_controller.workflows import Conditioning, Parameters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--initial-image", action="store_true", help="Use img2img initialization")
    parser.add_argument("--character-adapter")
    parser.add_argument("--style-adapter")
    parser.add_argument("--clip-vision")
    parser.add_argument("--controlnet", help="ControlNet model for an already prepared control map")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--denoise", type=float)
    args = parser.parse_args()
    try:
        controls = Conditioning(
            character_adapter=args.character_adapter,
            style_adapter=args.style_adapter,
            clip_vision=args.clip_vision,
            controlnet=args.controlnet,
        )
        roles = [
            role
            for role, enabled in (
                ("initial_image", args.initial_image),
                ("character_image", args.character_adapter),
                ("style_image", args.style_adapter),
                ("control_image", args.controlnet),
            )
            if enabled
        ]
        parameters = Parameters(
            checkpoint=args.checkpoint,
            positive_prompt="Describe the desired scene",
            width=args.width,
            height=args.height,
            denoise=args.denoise if args.denoise is not None else 0.65 if args.initial_image else 1,
        )
        if not roles:
            parser.error("Select --initial-image or a character/style/control reference model")
        graph = (
            conditioning_prompt(parameters, controls, roles)
            if any(role != "initial_image" for role in roles)
            else image_prompt(parameters, reference=True)
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(graph, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

