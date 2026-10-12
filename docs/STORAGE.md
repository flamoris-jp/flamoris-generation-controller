# Generated media and private job storage

Controller keeps `FLAMORIS_OUTPUT_DIR` as its private authority root. It contains
`job-authority`, `controller-authority`, job UUID archives, provider staging,
managed inputs and transfer/retention records. `FLAMORIS_WORKFLOW_DIR` remains the
retained recipe/definition directory. Neither root is a browsable media folder.

## Optional YuE2 publication

Set the operator-only environment variable `FLAMORIS_YUE2_OUTPUT_ROOT` to an
existing, writable local directory. It defaults to unset: existing behavior and
all private storage paths remain unchanged. The setting does not enable the
provider; `FLAMORIS_YUE2_CONFIG` is still required.

Once a validated YuE2 result is observed as completed, Controller writes one
bundle named `YYYYMMDDTHHMMSSZ-<job_id>` (UTC submission-acknowledgement time).
It contains `audio.wav`, `score.abc` and `replay.json`. These are operator copies,
separate from the private staging/archive files used for API retrieval. They
may include private lyrics and generation parameters. New bundle directories
request mode 0750 and files request 0640, subject to the process umask and the
operator's filesystem ACLs; other users get no access by default. Grant read
access only to the intended operator/group. Private archives/journals continue
to use their original 0700/0600 permissions.

The root is not created implicitly and a symlink root is rejected. Each bundle
is exclusively reserved; existing directories/files are never overwritten.
Publication uses directory-descriptor confinement. A later poll does not
recreate copies an operator moved or deleted, and these copies never supply API
asset bytes. Generation remains completed if publication fails: the snapshot
metadata contains `output_publication.status=failed` and logs contain
`YuE2 output publication failed`. No provider request is replayed. A failure
partway through publication can leave an incomplete bundle for manual inspection;
there is no automatic retry or cleanup of operator files.

For containers, bind only the selected host media directory to a dedicated
writable container path, for example `/data/music-output`, and set
`FLAMORIS_YUE2_OUTPUT_ROOT=/data/music-output`. Preserve the existing private
outputs/workflows mounts. No host path, model location or username is compiled
into the package. The deployment owner must record the added binding in its
managed installation profile; an ad-hoc Docker change alone is not an Updater
profile migration. Existing installations need the updated Controller and
matching facade dependency before the new setting has any effect.

ComfyUI originals continue to use ComfyUI's own configured output root and
filename prefix; this setting does not add per-image job folders or relocate
images. Irodori/SheetSage2 output publication and new provider adapters are
outside this change.

## Finding and deleting data

| Data | Location / deletion |
| --- | --- |
| Human YuE2 bundles | Configured `FLAMORIS_YUE2_OUTPUT_ROOT`; operator-managed copies |
| ComfyUI originals | ComfyUI's configured output root; provider retention rules |
| Controller job journals and lifetime lock | `FLAMORIS_OUTPUT_DIR/job-authority` and `controller-authority` |
| Private generated archives and staging | Job UUID directories and `yue2-staging` inside `FLAMORIS_OUTPUT_DIR` |
| Saved recipes and definitions | `FLAMORIS_WORKFLOW_DIR` |

`assets.delete` removes the requested Controller-managed archive copy only; it
does not delete provider originals or published operator bundles. Manual deletion
of a bundle likewise does not delete a Studio history entry or a private archive.
Changing the publication setting does not move, rewrite or delete old data.
Drain/reconcile active or unknown work before maintenance; never remove authority
journals or the lifetime lock while a Controller is alive. Record exact host paths
and mount mappings in private operational documentation so later cleanup can find
all copies. Model weights, inputs, caches and native runtime installations are
unaffected by publication.


## External discovery receipts

`FLAMORIS_OUTPUT_DIR/job-authority/external-index.json` is a bounded private recent index (maximum 1,024 completed external jobs) used by the internal service discovery API. It records only job ID, monotonic sequence and verified non-secret issuer/subject. It is not a new job authority, user ACL or a media folder. Keep it with private job records; deleting it loses automatic historical discovery, while exact-job metadata/import and retained media remain separate. Existing reservations, job directories and operator-selected human output paths are unchanged.
