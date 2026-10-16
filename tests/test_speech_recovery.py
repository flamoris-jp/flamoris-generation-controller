import copy

import httpx
import pytest

from flamoris_generation_controller.asset_files import AssetFiles
from flamoris_generation_controller.authority import Authority
from flamoris_generation_controller.contracts import CallerContext, ControllerError
from flamoris_generation_controller.durable import CommitUnknown, Records
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.speech_recovery import main, reconcile

JOB = "a" * 32
EXECUTION = "b" * 32
EVIDENCE = "Operator matched execution in runtime journal; synthesis completed; response lost."


def reservation(root):
    record = {
        "active": {
            "job_id": JOB,
            "workflow_id": "c" * 32,
            "execution_id": EXECUTION,
            "provider_id": "irodori",
            "operation": "speech.generate",
            "recipe": {
                "schema_version": 4,
                "template": "speech-no-reference",
                "parameters": {"text": "こんにちは"},
            },
        }
    }
    Records(root, "job-authority").write("active.json", record)
    with AssetFiles(root / "irodori-http-staging", EXECUTION):
        pass
    return record


def apply(root):
    return reconcile(root, JOB, EXECUTION, EVIDENCE, confirmed_runtime_settled=True, apply=True)


def test_dry_run_requires_exact_reservation_and_preserves_it(tmp_path):
    original = reservation(tmp_path)
    assert reconcile(tmp_path, JOB, EXECUTION, EVIDENCE)["dry_run"]
    assert Records(tmp_path, "job-authority").read("active.json") == original
    assert Records(tmp_path, "job-authority").read("reconciled-" + JOB + ".json") is None
    with pytest.raises(ValueError, match="Confirm actual"):
        reconcile(tmp_path, JOB, EXECUTION, EVIDENCE, apply=True)
    assert Records(tmp_path, "job-authority").read("active.json") == original


def test_apply_audits_before_release_and_is_idempotent(tmp_path):
    original = reservation(tmp_path)
    assert apply(tmp_path)["released"]
    records = Records(tmp_path, "job-authority")
    assert records.read("active.json") == {"active": None}
    receipt = records.read("reconciled-" + JOB + ".json")
    assert receipt["original_reservation"] == original
    assert receipt["evidence_note"] == EVIDENCE
    assert receipt["decision"] == "runtime_settled_output_unavailable"
    assert apply(tmp_path)["already_reconciled"]
    assert records.read("reconciled-" + JOB + ".json") == receipt


def test_live_owner_excludes_reconciliation(tmp_path):
    original = reservation(tmp_path)
    owner = Authority(tmp_path)
    try:
        with pytest.raises(ControllerError, match="authority_busy"):
            apply(tmp_path)
    finally:
        owner.close()
    assert Records(tmp_path, "job-authority").read("active.json") == original


@pytest.mark.parametrize(
    "key,value",
    [
        ("job_id", "d" * 32),
        ("execution_id", "d" * 32),
        ("provider_id", "comfyui"),
        ("operation", "image.generate"),
        ("runtime_delegation", {}),
        ("recipe", {"schema_version": 3, "template": "custom"}),
    ],
)
def test_other_or_retired_work_is_never_released(tmp_path, key, value):
    original = reservation(tmp_path)
    original["active"][key] = value
    Records(tmp_path, "job-authority").write("active.json", original)
    with pytest.raises(ValueError):
        apply(tmp_path)
    assert Records(tmp_path, "job-authority").read("active.json") == original


def test_cli_speech_without_http_staging_is_not_released(tmp_path):
    original = reservation(tmp_path)
    (tmp_path / "irodori-http-staging" / EXECUTION).rmdir()
    with pytest.raises((ValueError, OSError)):
        apply(tmp_path)
    assert Records(tmp_path, "job-authority").read("active.json") == original


def test_audit_failure_retains_reservation(tmp_path, monkeypatch):
    original = reservation(tmp_path)
    write = Records.write

    def fail_audit(self, name, data):
        if name.startswith("reconciled-"):
            raise OSError("test audit failure")
        return write(self, name, data)

    monkeypatch.setattr(Records, "write", fail_audit)
    with pytest.raises(OSError):
        apply(tmp_path)
    assert Records(tmp_path, "job-authority").read("active.json") == original


def test_uncertain_release_commit_stops_then_explicit_retry_uses_audit(tmp_path, monkeypatch):
    original = reservation(tmp_path)
    write = Records.write

    def fail_after_publish(self, name, data):
        write(self, name, data)
        if name == "active.json":
            raise CommitUnknown("test directory sync failure")

    monkeypatch.setattr(Records, "write", fail_after_publish)
    with pytest.raises(CommitUnknown):
        apply(tmp_path)
    records = Records(tmp_path, "job-authority")
    assert records.read("reconciled-" + JOB + ".json")["original_reservation"] == original
    monkeypatch.setattr(Records, "write", write)
    assert apply(tmp_path)["already_reconciled"]


def test_prior_receipt_does_not_release_a_new_job(tmp_path):
    reservation(tmp_path)
    apply(tmp_path)
    another = copy.deepcopy(reservation(tmp_path))
    another["active"]["job_id"] = "e" * 32
    Records(tmp_path, "job-authority").write("active.json", another)
    with pytest.raises(ValueError):
        apply(tmp_path)
    assert Records(tmp_path, "job-authority").read("active.json") == another


def test_corrupt_receipt_is_not_treated_as_reconciliation(tmp_path):
    reservation(tmp_path)
    apply(tmp_path)
    records = Records(tmp_path, "job-authority")
    records.write(
        "reconciled-" + JOB + ".json", {"job_id": JOB, "provider_execution_id": EXECUTION}
    )
    with pytest.raises(ValueError, match="Invalid reconciliation receipt"):
        apply(tmp_path)


def test_reference_receipt_and_input_identity_are_preserved(tmp_path):
    original = reservation(tmp_path)
    original["active"]["recipe"] = {
        "schema_version": 9,
        "template": "speech-reference",
        "parameters": {"text": "こんにちは", "reference_audio": "d" * 32},
    }
    Records(tmp_path, "job-authority").write("active.json", original)
    with AssetFiles(tmp_path / "irodori-http-staging", EXECUTION, create=False) as files:
        files.write("reference.json", b'{"voice_id":"private-voice"}')
    assert apply(tmp_path)["released"]
    with AssetFiles(tmp_path / "irodori-http-staging", EXECUTION, create=False) as files:
        assert files.read("reference.json", 1024) == b'{"voice_id":"private-voice"}'
    receipt = Records(tmp_path, "job-authority").read("reconciled-" + JOB + ".json")
    assert receipt["original_reservation"] == original


def test_cli_refusal_omits_private_evidence(tmp_path, capsys):
    reservation(tmp_path)
    with pytest.raises(SystemExit) as result:
        main(
            [
                "--output-root",
                str(tmp_path),
                "--job-id",
                JOB,
                "--execution-id",
                EXECUTION,
                "--evidence-note",
                "private detail",
                "--apply",
            ]
        )
    assert result.value.code == 1
    assert "private detail" not in capsys.readouterr().err


async def test_lost_http_response_can_be_reconciled_offline_without_replay(settings):
    posts = []

    def handle(request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={"status": "ok", "model": {"id": "irodori-tts"}, "runtime": {"loaded": True}},
            )
        posts.append(request)
        raise httpx.ReadTimeout("test lost response")

    settings.irodori_url = "http://127.0.0.1:8088"
    transport = httpx.MockTransport(handle)
    context = CallerContext.internal()
    controller = GenerationController(settings, transport=transport)
    recipe = controller.workflows.build("speech-no-reference", {"text": "こんにちは"})
    made = await controller.invoke(
        "jobs.submit", {"workflow_id": recipe["workflow_id"]}, context=context
    )
    provider = controller.providers.get("irodori")
    execution = made["provider_execution_id"]
    await provider._runs[execution].worker
    assert (await controller.jobs.status(made["job_id"]))["status"] == "unknown"
    await controller.close()
    assert reconcile(
        settings.output_dir,
        made["job_id"],
        execution,
        EVIDENCE,
        confirmed_runtime_settled=True,
        apply=True,
    )["released"]
    restarted = GenerationController(settings, transport=transport)
    try:
        assert not restarted.jobs.activity()["busy"]
        assert len(posts) == 1
        assert not (settings.output_dir / made["job_id"] / "metadata.json").exists()
    finally:
        await restarted.close()
