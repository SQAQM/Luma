"""Source/frozen model-library smoke using inert synthetic files and an isolated DB.

No real Civitai account, image decoder, model runtime or user library is used.
The JSON report contains measured booleans/counts, never fixture paths/hashes.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import threading
from unittest.mock import patch


def _require(condition):
    if not condition:
        raise AssertionError("synthetic model-library verification failed")


def _safe_failure(error):
    functions = []
    trace = error.__traceback__
    while trace is not None and len(functions) < 12:
        name = trace.tb_frame.f_code.co_name
        functions.append(name[:80] if name.isidentifier() else "anonymous")
        trace = trace.tb_next
    return {"type": type(error).__name__[:80], "functions": functions}


def _caller_functions():
    frame = sys._getframe(1)
    functions = []
    while frame is not None and len(functions) < 12:
        name = frame.f_code.co_name
        functions.append(name[:80] if name.isidentifier() else "anonymous")
        frame = frame.f_back
    return functions


def _runtime_read_kind(path, mode):
    """Only PyInstaller's own read-only code/source probes are permitted.

    PySide's feature hook inspects the importing application module. A frozen
    loader probes its bundled .py path, normally absent. This is distinct from
    reading originals and must still leave every other file access denied.
    """
    if not getattr(sys, "frozen", False) or not isinstance(mode, str) or mode not in ("r", "rb"):
        return ""
    if not isinstance(path, (str, bytes, os.PathLike)):
        return ""
    candidate = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    executable = os.path.normcase(os.path.abspath(sys.executable))
    bundle = os.path.normcase(os.path.abspath(sys._MEIPASS))
    in_bundle = candidate.startswith(bundle + os.sep)
    frame = sys._getframe(1)
    for _ in range(24):
        if frame is None: break
        module = frame.f_globals.get("__name__")
        name = frame.f_code.co_name
        if module == "pyimod01_archive" and name == "extract" and candidate == executable:
            return "runtime_archive"
        if module == "pyimod02_importers" and name == "get_source" and in_bundle and candidate.endswith(".py"):
            relative = candidate[len(bundle + os.sep):].replace("\\", "/")
            if relative.startswith(("backend/", "desktop/")):
                return "runtime_source"
        frame = frame.f_back
    return ""


def _wait(service, job, diagnostics=None):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = service.job(job["id"])
        if diagnostics is not None:
            status = state.get("status")
            diagnostics["last_job_status"] = status if status in ("queued", "running", "completed", "completed_with_errors", "failed", "cancelled", "interrupted") else "unknown"
            diagnostics["last_job_errors"] = state["errors"] if type(state.get("errors")) is int else -1
        if state["status"] not in ("queued", "running"):
            _require(state["status"] == "completed" and state["errors"] == 0)
            return state
        time.sleep(.01)
    raise TimeoutError("synthetic model-library task timed out")


class _SyntheticReply(io.BytesIO):
    def __init__(self, value, url):
        body = json.dumps(value).encode("utf-8")
        super().__init__(body)
        self.status = 200
        self.headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        self.url = url

    def geturl(self):
        return self.url


class _SyntheticTransport:
    def __init__(self, digest):
        self.digest = digest

    def open(self, request, timeout):
        _require(request.get_header("Authorization") is None and 0 < timeout <= 15)
        if request.full_url == "https://civitai.com/api/v1/model-versions/by-hash/" + self.digest:
            value = {"id": 502, "modelId": 501, "name": "synthetic-v1", "baseModel": "SDXL 1.0",
                     "description": "<p>Synthetic version notes</p>",
                     "model": {"name": "Synthetic smoke checkpoint", "type": "Checkpoint"},
                     "trainedWords": [], "images": [],
                     "files": [{"name": "smoke-checkpoint.safetensors", "type": "Model",
                                "hashes": {"SHA256": self.digest.upper(), "AutoV2": self.digest[:10].upper()}}]}
        elif request.full_url == "https://civitai.com/api/v1/models/501":
            value = {"id": 501, "name": "Synthetic smoke checkpoint", "type": "Checkpoint",
                     "description": "<p>Synthetic <b>model introduction</b></p><script>omit()</script>",
                     "creator": {"username": "Synthetic fixture author"}}
        else:
            raise AssertionError("unexpected synthetic transport endpoint")
        return _SyntheticReply(value, request.full_url)


class _SyntheticCivitai:
    def __init__(self, digest):
        from .civitai import CivitaiClient
        self.digest = digest
        self.version_requests = 0
        self.model_requests = 0
        self.cover_requests = 0
        self.client = CivitaiClient(transport=_SyntheticTransport(digest))

    def version_by_hash(self, digest, cancelled=lambda: False):
        _require(not cancelled() and digest == self.digest)
        self.version_requests += 1
        return self.client.version_by_hash(digest, cancelled=cancelled)

    def model(self, model_id, cancelled=lambda: False):
        _require(not cancelled() and model_id == 501)
        self.model_requests += 1
        return self.client.model(model_id, cancelled=cancelled)

    def download_cover(self, *args, **kwargs):
        self.cover_requests += 1
        raise AssertionError("synthetic smoke forbids cover requests")


def run_model_library_smoke(report_path):
    """Exercise actual service/jobs/index; write a safe report and return 0/1."""
    report = {"verification": "failed", "framework": "LibraryService + JobManager + SQLite",
              "runtime": "frozen" if getattr(sys, "frozen", False) else "source",
              "fixtures": "one inert model file and two inert indexed image placeholders",
              "user_images_accessed": False, "user_database_accessed": False,
              "production_capacity_validated": False, "real_account_authentication_tested": False}
    phase = "initialize"
    blocked_attempts = {"network": 0, "pixel_decode": 0, "pickle_execution": 0}
    diagnostics = {"file_read_attempt_count": 0, "runtime_source_probes": 0, "runtime_archive_reads": 0}
    original_open = open
    diagnostic_timer = None
    diagnostic_fd = None
    if os.environ.get("LUMA_MODEL_SMOKE_DIAGNOSTICS") == "1":
        diagnostic_path = Path(report_path).absolute().with_suffix(".progress.json")
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_fd = os.open(diagnostic_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)

    def snapshot_functions():
        # Explicit diagnostic mode, synthetic process only. Never retain frame
        # locals, file paths, exception messages or model/image values.
        stacks = []
        for frame in list(sys._current_frames().values())[:16]:
            functions = []
            while frame is not None and len(functions) < 18:
                name = frame.f_code.co_name
                functions.append(name if name.isidentifier() else "anonymous")
                frame = frame.f_back
            stacks.append(functions)
        payload = json.dumps({"phase": phase, "safe_diagnostics": dict(diagnostics), "thread_functions": stacks}).encode("utf-8")
        os.write(diagnostic_fd, payload)

    def forbidden(kind):
        def fail(*args, **kwargs):
            blocked_attempts[kind] += 1
            raise AssertionError("synthetic smoke forbids " + kind)
        return fail

    def deny_file_read(*args, **kwargs):
        kind = _runtime_read_kind(args[0] if args else kwargs.get("file"),
                                  args[1] if len(args)>1 else kwargs.get("mode", "r"))
        if kind:
            key = "runtime_source_probes" if kind == "runtime_source" else "runtime_archive_reads"
            diagnostics[key] += 1
            return original_open(*args, **kwargs)
        diagnostics["file_read_attempt_count"] += 1
        diagnostics["file_read_caller_functions"] = _caller_functions()
        raise AssertionError("synthetic smoke forbids file byte reads in cached action")

    try:
        from desktop.service import LibraryService

        with tempfile.TemporaryDirectory(prefix="luma-model-library-smoke-") as temporary:
            root = Path(temporary)
            models = root / "synthetic-models"
            models.mkdir()
            model = models / "smoke-checkpoint.safetensors"
            model_bytes = b"Luma synthetic inert model fixture; not a tensor or pickle."
            model.write_bytes(model_bytes)
            digest = hashlib.sha256(model_bytes).hexdigest()
            images = [root / "verified-placeholder.png", root / "name-only-placeholder.png"]
            image_bytes = [b"synthetic image index placeholder 1", b"synthetic image index placeholder 2"]
            for path, data in zip(images, image_bytes):
                path.write_bytes(data)
            client = _SyntheticCivitai(digest)

            with ExitStack() as guards:
                guards.enter_context(patch("socket.socket", side_effect=forbidden("network")))
                guards.enter_context(patch("PIL.Image.open", side_effect=forbidden("pixel_decode")))
                guards.enter_context(patch("pickle.load", side_effect=forbidden("pickle_execution")))
                guards.enter_context(patch("pickle.loads", side_effect=forbidden("pickle_execution")))
                service = LibraryService(root / "isolated-data", civitai_client=client)
                try:
                    # Store.upsert consumes explicit stat/metadata only; no import
                    # task or image parser is started for these inert fixtures.
                    phase = "synthetic_index"
                    ids = [service.store.upsert(images[0], None, images[0].stat(),
                               {"model": "smoke-checkpoint", "parameters": {"model_sha256": digest}}),
                           service.store.upsert(images[1], None, images[1].stat(),
                               {"model": "smoke-checkpoint", "parameters": {}})]
                    phase = "stat_only_scan"
                    registered = service.add_model_root(str(models))
                    _require(service.models()["total"] == 0 and client.version_requests == 0)
                    with patch("builtins.open", side_effect=AssertionError("scan must not read file bytes")), \
                         patch("pathlib.Path.open", side_effect=AssertionError("scan must not read file bytes")), \
                         patch("backend.duplicates.VerifiedFile.digest", side_effect=AssertionError("scan must not hash")):
                        scan = _wait(service, service.scan_model_directory(registered["id"]))
                    listing = service.models()
                    _require(listing["total"] == 1 and len(listing["items"]) == 1)
                    item = listing["items"][0]
                    _require(item["sha256"] == "" and client.version_requests == 0 and scan["processed"] == 1)
                    report.update(model_fixture_count=1, indexed_image_count=2, stat_only_scan_verified=True,
                                  registration_and_scan_did_not_query_civitai=True)

                    phase = "explicit_sync"
                    _wait(service, service.sync_models([item["id"]], download_covers=False))
                    detail = service.model_detail(item["id"])
                    _require(detail["status"] == "synced" and detail["sha256"] == digest
                             and detail["family"] == "Stable Diffusion" and client.version_requests == 1)
                    _require("Synthetic model introduction" in detail["description"]
                             and "<" not in detail["description"] and "omit()" not in detail["description"]
                             and detail["creator"] == "Synthetic fixture author"
                             and client.model_requests == 1)
                    _require(service.model_cover_path(item["id"]) is None and client.cover_requests == 0)
                    report.update(explicit_hash_sync_verified=True, cover_request_count=client.cover_requests,
                                  synthetic_version_request_count=client.version_requests,
                                  real_client_with_fake_transport=True, model_profile_lookup_count=client.model_requests,
                                  plain_text_introduction_and_creator_verified=True)

                    phase = "cached_guard_setup"
                    if diagnostic_fd is not None:
                        diagnostic_timer = threading.Timer(8, snapshot_functions)
                        diagnostic_timer.daemon = True
                        diagnostic_timer.start()
                    original_reclassify = service._reclassify_models_job

                    def observed_reclassification(*args, **kwargs):
                        try:
                            return original_reclassify(*args, **kwargs)
                        except Exception as error:
                            diagnostics["cached_task_failure"] = _safe_failure(error)
                            raise

                    service._reclassify_models_job = observed_reclassification
                    with patch("backend.metadata.extract_metadata", side_effect=AssertionError("cached task must not parse files")), \
                         patch("builtins.open", side_effect=deny_file_read), \
                         patch("pathlib.Path.open", side_effect=deny_file_read), \
                         patch("backend.duplicates.VerifiedFile.digest", side_effect=AssertionError("cached task must not hash")):
                        phase = "cached_submit"
                        links_job = service.rebuild_model_links()
                        phase = "cached_wait"
                        _wait(service, links_job, diagnostics)
                    phase = "cached_verified_filter"
                    verified_filters = service.model_image_filters(item["id"], "verified")
                    phase = "cached_verified_query"
                    verified = service.query(verified_filters)
                    phase = "cached_name_filter"
                    name_filters = service.model_image_filters(item["id"], "name")
                    phase = "cached_name_query"
                    named = service.query(name_filters)
                    verified_ids = {row["id"] for row in verified["items"]}
                    named_ids = {row["id"] for row in named["items"]}
                    diagnostics["verified_image_count"] = verified["total"]
                    diagnostics["name_match_count"] = named["total"]
                    phase = "cached_verified_assert"
                    _require(verified["total"] == 1 and verified_ids == {ids[0]})
                    phase = "cached_candidate_assert"
                    _require(named["total"] == 2 and named_ids - verified_ids == {ids[1]})
                    phase = "cached_verified_family"
                    _require(service.detail(ids[0])["family"] == "Stable Diffusion")
                    phase = "cached_candidate_family"
                    _require(service.detail(ids[1])["family"] != "Stable Diffusion")
                    phase = "cached_request_counts"
                    _require(client.version_requests == 1 and client.model_requests == 1)
                    report.update(cached_reclassification_and_links_verified=True, verified_image_count=verified["total"],
                                  name_match_count=named["total"], name_only_candidate_count=len(named_ids - verified_ids),
                                  same_name_candidate_remained_unconfirmed=True)

                    phase = "remove_model_root"
                    result = service.remove_model_root(registered["id"])
                    _require(result["removed"] and service.model_roots() == [] and service.models()["total"] == 0)
                    _require(service.query()["total"] == 2)
                    report.update(root_index_removed=True, gallery_images_preserved=True)
                finally:
                    service.close()

            phase = "bytes_unchanged"
            _require(model.read_bytes() == model_bytes)
            _require(all(path.read_bytes() == data for path, data in zip(images, image_bytes)))
            _require(all(count == 0 for count in blocked_attempts.values()))
            _require(diagnostics["file_read_attempt_count"] == 0)
            report.update(verification="passed", model_bytes_unchanged=True, image_placeholder_bytes_unchanged=True,
                          safe_diagnostics=diagnostics,
                          network_attempt_count=blocked_attempts["network"],
                          pixel_decode_attempt_count=blocked_attempts["pixel_decode"],
                          pickle_execution_attempt_count=blocked_attempts["pickle_execution"])
    except Exception as error:
        # Exception messages can include temporary paths, hashes or upstream
        # values. Only a fixed error code and fixed phase identifier are saved.
        report.update(verification="failed", error_code="model_library_smoke_failed", failed_phase=phase,
                      failure=_safe_failure(error), safe_diagnostics=diagnostics,
                      network_attempt_count=blocked_attempts["network"],
                      pixel_decode_attempt_count=blocked_attempts["pixel_decode"],
                      pickle_execution_attempt_count=blocked_attempts["pickle_execution"])

    try:
        if diagnostic_timer is not None:
            diagnostic_timer.cancel()
        if diagnostic_fd is not None:
            os.close(diagnostic_fd)
        output = Path(report_path).absolute()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return 1
    return 0 if report["verification"] == "passed" else 1
