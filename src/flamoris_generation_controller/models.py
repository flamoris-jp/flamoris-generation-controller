"""Discover file metadata only. Never deserialize model weights."""

from pathlib import PurePosixPath

from .config import MODEL_FOLDERS, ModelKind, Settings

EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf"}


def model_name(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
        raise ValueError("Model name must be text of at most 1024 UTF-8 bytes")
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Model name must be a relative POSIX filename within its model directory")
    return value


class ModelCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings

    def list(self, kind: ModelKind | None = None) -> list[dict]:
        records = {}
        for current in [kind] if kind else MODEL_FOLDERS:
            for root in self.settings.roots(current):
                root = root.resolve()
                if not root.exists():
                    continue
                if not root.is_dir():
                    raise ValueError(f"Configured {current} model root is not a directory")
                for path in sorted(root.rglob("*")):
                    if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
                        continue
                    if not path.resolve().is_relative_to(root):
                        continue
                    name = model_name(path.relative_to(root).as_posix())
                    identity = f"{current}:{name}"
                    if identity in records:
                        raise ValueError(f"Ambiguous model name in configured roots: {identity}")
                    records[identity] = {
                        "id": identity,
                        "kind": current,
                        "name": name,
                        "size_bytes": path.stat().st_size,
                        "format": path.suffix.lower()[1:],
                    }
        return sorted(records.values(), key=lambda item: item["id"])

    def get(self, model_id: str) -> dict:
        kind, separator, name = model_id.partition(":")
        if not separator or kind not in MODEL_FOLDERS:
            raise ValueError("Model ID must be kind:relative_filename (see models.list)")
        model_name(name)
        for model in self.list(kind):
            if model["name"] == name:
                return model
        raise ValueError(f"Model not found: {model_id}")

    def require(self, kind: ModelKind, name: str) -> dict:
        return self.get(f"{kind}:{model_name(name)}")
