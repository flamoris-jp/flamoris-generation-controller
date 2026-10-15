# Reference speech

Controller #21 adds the HTTP-only `speech-reference` template, recipe schema 9,
with the existing text/caption/seconds/steps/seed parameters plus a required
`reference_audio` managed input ID. The profile is
`irodori-server-reference-v1`. Existing schema-4 no-reference recipes and CLI
generation remain compatible. No runtime or model is activated by this change.

Upload one PCM16 WAV through `inputs.upload.begin/write/finish`, or reuse an
owned generated WAV through `inputs.create`. Uploads are bounded to 8 MiB and
30 seconds, mono/stereo, at 16/22.05/24/44.1/48/96 kHz. The same immutable input
authority, digests, expiry, quotas and staging leases apply. Invalid audio is
never published. Reference bytes are validated again when staged for execution.
No public endpoint accepts a local path, upstream voice ID or external URL.

The selected Irodori HTTP contract is the existing pinned
`61012c760f22f7b4a6c21c5c5f8f9e148120b6f9`. Reference execution requires a loaded
server reporting the v4-Small or v4.1-Small checkpoint identity. Unsupported,
missing and no-reference-only model identities reject before upload/synthesis;
health includes a bounded `reference_audio` support flag. This is a contract
check, not evidence that model weights or voice similarity were verified.

One execution uploads bytes to `POST /v1/audio/voices` under a random private
`flamoris_<execution-id>` ID with replace disabled, validates the receipt, then
makes exactly one speech request with that voice and `no_ref=false`. The adapter
does not assume a shared filesystem. Job records retain the reference input
ID/digest and schema/profile. References are not silently dropped.

Confirmed speech completion/rejection deletes only that temporary voice.
Ambiguous synthesis keeps the voice, the ordinary unknown job reservation and
the private `irodori-http-staging/<execution-id>/reference.json` receipt. Upload or
cleanup uncertainty also retains the bounded receipt; failed cleanup does not
discard valid audio. After a restart, no uploads or synthesis are replayed and
voices are not deleted based on server health. Reconcile the actual server work
before deleting a retained voice. Staging remains bounded by the existing
128-execution capacity; this feature does not add a voice library or an
unbounded cleanup queue.

Matched Studio validates owner-scoped handles before resolving input IDs and
records the reference against the execution. Generation MCP and Hub only extend
the existing upload MIME enum to WAV; no new tools or routing implementation
are required. Normal CI uses fake HTTP/WAV fixtures. Real voice quality and
operator acceptance remain separate from source tests.
