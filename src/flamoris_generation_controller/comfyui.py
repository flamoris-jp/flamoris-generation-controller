"""The only module that knows ComfyUI HTTP routes and response shapes."""

from urllib.parse import quote

import httpx

from .config import Settings
from .models import model_name
from .providers.base import (
    ProviderError,
    SubmissionRejected,
    SubmissionUnknown,
    execution_identity,
)

MAX_OUTPUT_BYTES = 64 * 1024 * 1024
INPUT_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
VALIDATION_REJECTIONS = {
    "no_prompt",
    "invalid_prompt_id",
    "missing_node_type",
    "prompt_no_outputs",
    "prompt_outputs_failed_validation",
}


def rejection_detail(response: httpx.Response) -> str:
    """Keep validation errors actionable without returning an entire provider payload."""
    try:
        data = response.json()
        parts = []
        error = data.get("error", {})
        if isinstance(error, dict):
            parts.append(str(error.get("message", ""))[:300])
        for node_id, node in list(data.get("node_errors", {}).items())[:8]:
            for error in node.get("errors", [])[:2]:
                parts.append(
                    f"node {str(node_id)[:40]}: {str(error.get('message', ''))[:150]} "
                    f"{str(error.get('details', ''))[:250]}"
                )
        return "; ".join(part for part in parts if part)[:1500]
    except (ValueError, AttributeError, TypeError):
        return ""


class ComfyUIClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=str(settings.comfyui_url).rstrip("/") + "/",
            timeout=settings.request_timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self.http.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = rejection_detail(exc.response) if path == "prompt" else ""
            raise ProviderError(
                f"ComfyUI {path} returned HTTP {exc.response.status_code}"
                + (f": {detail}" if detail else "")
            ) from None
        except httpx.RequestError:
            raise ProviderError("ComfyUI request failed; check connectivity and timeout") from None
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            raise ProviderError(f"ComfyUI {path} returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ProviderError(f"ComfyUI {path} returned an invalid response object")
        return data

    async def queue(self) -> tuple[set[str], set[str]]:
        data = await self._request("GET", "queue")
        result = []
        for key in ("queue_running", "queue_pending"):
            entries = data.get(key)
            if not isinstance(entries, list) or any(
                not isinstance(item, list) or len(item) < 2 or not isinstance(item[1], str)
                for item in entries
            ):
                raise ProviderError("ComfyUI returned invalid queue data")
            result.append({item[1] for item in entries})
        return result[0], result[1]

    async def health(self) -> dict:
        try:
            running, pending = await self.queue()
            return {"available": True, "running": len(running), "queued": len(pending)}
        except ProviderError as exc:
            return {"available": False, "error": str(exc)}

    async def upload_input(self, data: bytes, mime_type: str, filename: str) -> str:
        """Upload one bounded provider input and return only ComfyUI's safe input name."""
        suffix = INPUT_MIME_EXTENSIONS.get(mime_type)
        if (
            suffix is None
            or not isinstance(data, bytes)
            or not data
            or len(data) > MAX_OUTPUT_BYTES
            or not filename.endswith(suffix)
            or "/" in filename
            or "\\" in filename
        ):
            raise ProviderError("Invalid ComfyUI managed image upload")
        response = await self._request(
            "POST",
            "upload/image",
            files={"image": (filename, data, mime_type)},
            data={"type": "input", "overwrite": "false"},
        )
        name = response.get("name")
        subfolder = response.get("subfolder", "")
        kind = response.get("type")
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or subfolder not in ("", None)
            or kind != "input"
        ):
            raise ProviderError("ComfyUI returned an invalid managed input upload")
        model_name(name)
        return name

    async def submit(self, prompt: dict, client_id: str) -> str:
        # Never retry POST: a timeout can happen after ComfyUI has already accepted the work.
        try:
            response = await self.http.request(
                "POST", "prompt", json={"prompt": prompt, "client_id": client_id}
            )
        except httpx.RequestError:
            raise SubmissionUnknown(
                "submission_unknown: provider acceptance is uncertain"
            ) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        # Only ComfyUI's structured validation rejection proves no queue admission.
        # A gateway 400, a success-body error, or an ID/error conflict proves nothing.
        error = data.get("error") if isinstance(data, dict) else None
        if (
            response.status_code == 400
            and isinstance(data, dict)
            and "prompt_id" not in data
            and isinstance(error, dict)
            and isinstance(error.get("type"), str)
            and error["type"] in VALIDATION_REJECTIONS
            and isinstance(error.get("message"), str)
            and bool(error["message"])
            and isinstance(data.get("node_errors"), dict)
        ):
            detail = rejection_detail(response)
            raise SubmissionRejected(
                "ComfyUI rejected the workflow" + (f": {detail}" if detail else "")
            )
        if response.status_code != 200 or not isinstance(data, dict):
            raise SubmissionUnknown("submission_unknown: provider acceptance is uncertain")
        if data.get("error") or data.get("node_errors"):
            raise SubmissionUnknown("submission_unknown: provider returned conflicting acceptance")
        try:
            return execution_identity(data.get("prompt_id"))
        except ValueError:
            raise SubmissionUnknown(
                "submission_unknown: provider returned an invalid prompt ID"
            ) from None

    async def inspect(self, prompt_id: str) -> dict:
        running, pending = await self.queue()
        # Read history after queue so a job completing between reads is seen as completed.
        data = await self._request("GET", "history/" + quote(prompt_id, safe=""))
        item = data.get(prompt_id)
        if item is not None:
            return self._history(item)
        status = (
            "running" if prompt_id in running else "queued" if prompt_id in pending else "unknown"
        )
        return {"status": status, "error": None, "outputs": []}

    @staticmethod
    def _history(item: dict) -> dict:
        try:
            status = item["status"]
            messages = status.get("messages", [])
            if not isinstance(messages, list):
                raise ValueError
            events = {message[0] for message in messages}
            if "execution_interrupted" in events:
                state, error = "cancelled", None
            elif status["status_str"] == "error" or "execution_error" in events:
                detail = next((m[1] for m in messages if m[0] == "execution_error"), {})
                state = "failed"
                error = {
                    "code": "execution_error",
                    "node_id": str(detail.get("node_id", ""))[:80],
                    "exception_type": str(detail.get("exception_type", ""))[:160],
                }
            elif status.get("completed") is True and status["status_str"] == "success":
                state, error = "completed", None
            else:
                state, error = "unknown", None
            outputs = []
            for node_id, node in item.get("outputs", {}).items():
                if not isinstance(node_id, str) or not isinstance(node, dict):
                    raise ValueError
                for image in node.get("images", []):
                    filename = model_name(image["filename"])
                    if "/" in filename or image.get("type") != "output":
                        raise ValueError
                    subfolder = image.get("subfolder", "")
                    if subfolder:
                        model_name(subfolder)
                    outputs.append(
                        {
                            "node_id": node_id,
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": "output",
                        }
                    )
            if len(outputs) > 64 or (state == "completed" and not outputs):
                raise ValueError
            return {"status": state, "error": error, "outputs": outputs}
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            raise ProviderError("ComfyUI returned invalid execution history") from None

    async def cancel(self, prompt_id: str) -> dict:
        snapshot = await self.inspect(prompt_id)
        if snapshot["status"] == "queued":
            await self._request("POST", "queue", json={"delete": [prompt_id]})
            snapshot = await self.inspect(prompt_id)
            # Queue deletion does not acknowledge which prompt was removed. Absence
            # can also follow a dispatch race, history loss or another owner action.
            # Only terminal history proves an execution outcome.
        if snapshot["status"] == "running":
            if not self.settings.targeted_interrupt:
                return {
                    **snapshot,
                    "cancel_supported": False,
                    "reason": "Running cancellation requires configured targeted-interrupt support",
                }
            await self._request("POST", "interrupt", json={"prompt_id": prompt_id})
            return {**snapshot, "status": "cancel_requested", "cancel_supported": True}
        return snapshot

    async def download(self, output: dict) -> bytes:
        try:
            async with self.http.stream("GET", "view", params=output) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_OUTPUT_BYTES:
                        raise ProviderError("Output exceeds the 64 MiB download limit")
                    data.extend(chunk)
                if not data:
                    raise ProviderError("ComfyUI returned an empty output")
                return bytes(data)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"ComfyUI output returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.RequestError:
            raise ProviderError("ComfyUI output download failed; retry jobs.result") from None

    async def stream_output(self, output: dict):
        """Identity-encoded bounded stream; outer transfer owns total bytes/deadline."""
        from .transfers import CHUNK_BYTES

        try:
            async with self.http.stream(
                "GET", "view", params=output, headers={"Accept-Encoding": "identity"}
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ProviderError("Encoded provider output is unsupported")
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_BYTES):
                    yield chunk
        except httpx.HTTPError:
            raise ProviderError("ComfyUI output stream failed; retry assets.prepare") from None
