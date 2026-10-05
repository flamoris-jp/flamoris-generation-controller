# Internal generation HTTP v1

Hosted initially by Generation MCP on its existing HTTP listener, using the same `GenerationController` instance as external MCP tools. The base package is MCP-free; the optional `http` extra supplies this adapter.

## Authentication and context

An operator-configured `FLAMORIS_CONTROLLER_TOKEN` is required: 32–512 printable ASCII characters. Each request carries exactly one `Authorization: Bearer <credential>` header. Missing configuration returns 503; missing/incorrect/duplicated credentials return 401 before parsing arguments or effects. HTTPS or a trusted loopback tunnel protects transport. External MCP still requires its operator-controlled authenticated network boundary.

This credential grants a trusted backend all operations below. It is service permission, not a Studio user identity. Studio independently enforces account/session/CSRF, owner-scoped opaque mappings and access rechecks before dispatch/publication. An upstream ID does not grant user access. Never expose the credential or raw upstream IDs to the browser. Split backend credentials/instances if the operator requires separate service trust domains; this API does not implement per-user ACLs.

The adapter supplies `CallerContext.internal()` after authentication. JSON fields and identity/provenance headers cannot populate trusted context. MCP's verified issuer/subject enters `CallerContext.external()` through its signed ingress; it is non-secret provenance and never an ownership grant. Core checks the context's operation permission before effects.

## Requests and results

POST `/api/v1/generation/{operation}` with `Content-Type: application/json` and an ordinary argument object. Extra fields, coercions, duplicate JSON keys, nonfinite values and unsupported operations are rejected. No MCP handshake, JSON-RPC envelope, Hub namespace or automatic fallback.

| Operations | Arguments | Result |
| --- | --- | --- |
| `system.health` | `{}` | Provider health, current reservation and API/ownership metadata |
| `capabilities.list`, `capabilities.get` | `{}` / `capability_id` | Configured capability metadata |
| `models.list`, `models.get` | Optional `kind` / `model_id` | Installed model metadata |
| `workflows.list`, `workflows.build`, `workflows.save` | `{}` / `template`, `parameters` / `workflow_id` | Retained recipe catalog, built recipe or saved recipe |
| `jobs.submit` | `workflow_id` | Admitted job ID/status |
| `jobs.status`, `jobs.result`, `jobs.cancel` | `job_id` | Job observation/result/scoped cancellation |
| `assets.list` | `job_id` | Generated asset metadata |
| `assets.delete`, `assets.get`, `assets.prepare` | `asset_id` | Deletion metadata / image bytes / immutable transfer metadata |
| `assets.read` | `asset_id`, `sha256`, `offset`, optional `length` | Digest-checked bounded base64 chunk |
| `inputs.create` | `asset_id` | Immutable expiring snapshot metadata |
| `inputs.get`, `inputs.delete` | `input_id` | Snapshot metadata/deletion |
| `inputs.upload.begin` | `upload_id`, `mime_type`, `size_bytes`, `sha256` | Ordered upload acknowledgement |
| `inputs.upload.write` | `upload_id`, `offset`, `data_base64`, `chunk_sha256` | Ordered digest-checked acknowledgement |
| `inputs.upload.finish` | `upload_id` | Immutable uploaded-image metadata |

Exact strict request models are in [`contracts.py`](../src/flamoris_generation_controller/contracts.py). Retained recipe, output and archive structures are unchanged; consumers still validate results. Optional old definition/readiness arguments to build reject explicitly; they do not enable retired features.

JSON objects are returned directly with HTTP 200. `assets.get` returns `image/png`, `image/jpeg` or `image/webp` bytes with no base64/MCP wrapping. Other media uses prepare/read. Responses carry `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

Limits: 512 KiB request body, 15-second body-read deadline, 2 MiB JSON response, existing 64 MiB image retrieval/provider-download limit, 256 KiB raw transfer/upload chunk, 8 MiB uploaded image. Stored input decode/staging/disk/expiry limits remain enforced by the retained domain. Compressed request/response bodies are unsupported. Studio additionally bounds each read and consumer preview size, forbids redirects/environment proxies and uses 45-second normal / 300-second image / 330-second prepare timeouts. Upload runs in one connection under a 75-second deadline with 15-second chunk calls.

## Safe failures and uncertainty

Errors are constant JSON `{"error":{"code":"..."}}`, without prompts, tokens, paths or provider details.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `validation` | Invalid retained request or body |
| 401 / 403 | `unauthorized` / `forbidden` | Failed ingress authentication / missing operation permission |
| 404 | `unknown_operation` | Operation unavailable; never silently use another transport |
| 409 | `busy` | Shared generation reservation occupied |
| 413 / 415 | `request_too_large`, `asset_too_large` / `validation` | Body/media limit or unsupported encoding/type |
| 502 | `upstream_failure`, `response_too_large` | Safe provider failure or bounded publication failure |
| 503 | `submission_unknown`, `unavailable` | Uncertain submit or unavailable service/authority |

No retry/fallback follows a submit timeout, lost acknowledgement, uncertain journal commit or transport replacement. The core keeps its reservation; Studio keeps its durable owned request fence. A failed response does not prove generation stopped. Cancellation must confirm a terminal provider outcome before reservation release. Keep original retired active journals for reconciliation by their previous matched authority.
