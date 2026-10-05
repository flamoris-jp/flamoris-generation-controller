"""Authenticated internal JSON/binary adapter; no MCP protocol or SDK dependency."""

import asyncio
import hmac
import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .contracts import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    OPERATIONS,
    AssetContent,
    CallerContext,
    ControllerError,
)
from .jobs import GenerationBusyError
from .providers import ProviderError
from .providers.base import SubmissionUnknown


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value


def _constant(_):
    raise ValueError("Nonfinite JSON value")


class HTTPAPI:
    """One fixed operator credential grants the trusted backend this bounded surface."""

    def __init__(self, controller, token: str | None):
        if token is not None and (
            not 32 <= len(token) <= 512 or not all(33 <= ord(c) <= 126 for c in token)
        ):
            raise ValueError("Invalid Controller credential configuration")
        self.controller = controller
        self._credential = ("Bearer " + token).encode("ascii") if token else None

    @staticmethod
    def error(code: str, status: int):
        return JSONResponse(
            {"error": {"code": code}},
            status_code=status,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def __call__(self, request: Request):
        if self._credential is None:
            return self.error("unavailable", 503)
        auth = [
            value for name, value in request.scope["headers"] if name.lower() == b"authorization"
        ]
        if len(auth) != 1 or not hmac.compare_digest(auth[0], self._credential):
            return self.error("unauthorized", 401)
        operation = request.path_params.get("operation")
        if operation not in OPERATIONS:
            return self.error("unknown_operation", 404)
        if request.headers.get("content-encoding", "identity") != "identity":
            return self.error("validation", 415)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            return self.error("validation", 415)
        try:
            length = request.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > MAX_REQUEST_BYTES):
                return self.error("request_too_large", 413)
            body = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        return self.error("request_too_large", 413)
                    body.extend(chunk)
            arguments = json.loads(body, object_pairs_hook=_object, parse_constant=_constant)
            if not isinstance(arguments, dict):
                return self.error("validation", 400)
            result = await self.controller.invoke(
                operation, arguments, context=CallerContext.internal()
            )
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            if isinstance(result, AssetContent):
                return Response(result.data, media_type=result.mime_type, headers=headers)
            encoded = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
            if len(encoded) > MAX_RESPONSE_BYTES:
                return self.error("response_too_large", 502)
            return Response(encoded, media_type="application/json", headers=headers)
        except GenerationBusyError:
            return self.error("busy", 409)
        except SubmissionUnknown:
            return self.error("submission_unknown", 503)
        except ControllerError as exc:
            if exc.code == "forbidden":
                return self.error("forbidden", 403)
            return self.error("unavailable", 503)
        except (ValueError, TypeError, RecursionError) as exc:
            if operation == "assets.get" and any(
                marker in str(exc).lower() for marker in ("retrieval limit", "download limit")
            ):
                return self.error("asset_too_large", 413)
            return self.error("validation", 400)
        except ProviderError:
            return self.error("upstream_failure", 502)
        except Exception:
            # Neither paths, private input nor provider exceptions enter public errors.
            return self.error("unavailable", 503)
