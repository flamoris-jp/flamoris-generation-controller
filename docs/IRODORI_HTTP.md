# Resident Irodori speech API

The optional HTTP transport reuses the shared Speech recipe, JobStore and Asset
authority. It calls the existing resident Irodori server; it does not start a
runtime, load a second model, download weights or switch the GPU.

The adapter is reviewed against [Irodori-TTS-Server
61012c760f22f7b4a6c21c5c5f8f9e148120b6f9](https://github.com/Aratako/Irodori-TTS-Server/tree/61012c760f22f7b4a6c21c5c5f8f9e148120b6f9).
The profile names the reviewed API contract revision, not an attestation of the
running server source or model weights.

## Configuration

Set `FLAMORIS_IRODORI_URL` to the operator-configured HTTP origin, for example
`http://127.0.0.1:8088`. It must contain no user information, query, fragment or
path beyond `/`. Loopback refers to the Controller host/network namespace. Use
the existing trusted private network; neither service needs public exposure.
Configure `FLAMORIS_IRODORI_MODEL` when the server uses a different model ID
(default `irodori-tts`). Optional `FLAMORIS_IRODORI_TOKEN` supplies server
authentication; keep it private. `FLAMORIS_IRODORI_TIMEOUT` bounds the entire
recording request to 5–300 seconds (default 300).

`FLAMORIS_IRODORI_CONFIG` remains the original CLI transport. Configure exactly
one transport. No configuration leaves Speech disabled. The HTTP adapter checks
`GET /health` with a five-second timeout and bounded JSON; `status=ok`, exact
model ID and boolean `runtime.loaded=true` are required. A stopped server makes
Speech unavailable while Controller and Image remain usable.

The schema-4 `speech-no-reference` descriptor/build identify
`irodori-server-no-reference-v1`, `server_contract_revision`,
`reference_audio=false` and `candidates=1`. Qualification is
`configured-http-contract`; readiness stays `not-attested`. Deploy the matched
Studio contract and enable `STUDIO_SPEECH_ENABLED=true`. Existing CLI discovery
continues to identify `irodori-no-reference-v1` and configured local resources.

## Recording and result

One task sends one `POST /v1/audio/speech`: text, optional caption, duration,
steps and seed use the existing bounds. The adapter explicitly disables reference
audio and text chunking, chooses one sequential candidate and requests WAV.
Credentials, model URLs, reference paths and arbitrary upstream options never
enter browser parameters. The returned WAV must satisfy the existing mono,
sample-rate, finite-sample, 31-second and 8-MiB bounds before it becomes an
`audio` output. Directory-fd confinement and digest checking protect publication
and later materialization. Normal owned Studio playback/download apply.

Redirects and environment proxies are disabled. Provider errors omit raw
response bodies. A timeout, lost/partial/oversized response or unconfirmed server
error stays `unknown` with the shared reservation held. No retry or fallback is
performed. Complete invalid output fails without publication. Staging retains
the existing 128-execution bound; completed asset retention and staging cleanup
are separate operator tasks.

The reviewed API has no targeted cancel or durable request-status endpoint.
Cancellation stays `cancel_requested` while the original response is observed;
the eventual recording may complete. Disconnecting HTTP cannot certify that GPU
inference stopped. Closing/restarting Controller preserves uncertain admission;
a staged WAV or healthy server never proves completion after restart. Reconcile
unknown work using the actual runtime before allowing another recording.

## Acceptance and rollback

Fake HTTP/WAV tests cover payload mapping, loaded/model checks, strict contracts,
result validation, cancellation, response loss, shared reservation and restart.
Source/CI is separate from live acceptance. After the matched packages and private
configuration are installed, activate Irodori through the existing runtime
manager, record a short Japanese phrase in Studio, listen to it and verify owned
preview/download and another account's denial. Record actual output metadata and
receipt separately. Disable Studio Speech and remove the HTTP opt-in for rollback;
preserve all request fences, unknown journals and generated assets.
