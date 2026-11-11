"""Create an owned model-sync profile; retry fails locally before any network call."""
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from backend.test_model_sync_scope import Client
from desktop.service import LibraryService

profile = Path(sys.argv[1]).resolve()
if not profile.is_relative_to(root / "build") or profile.exists():
    raise ValueError("Fresh profile inside project build required")
profile.mkdir(parents=True)
(profile / ".luma-winui-preview.json").write_text(json.dumps({"purpose": "luma-winui-stage8-preview"}))
name = "engine-probe-" + uuid.uuid4().hex
engine = profile / name
engine.mkdir()
(engine / ".luma-engine-preview.json").write_text(json.dumps({"schema": 1, "purpose": "luma-stage3-preview"}))
(profile / "winui-library-v1.json").write_text(json.dumps({"directory": name}))
fixtures = profile / "fixtures"
fixtures.mkdir()
for i in range(67):
    (fixtures / f"fixture-{i:03}.safetensors").write_bytes(f"inert weight {i}".encode())
client = Client()
client.fail[hashlib.sha256(b"inert weight 0").hexdigest()] = "not_found"
with LibraryService(engine, civitai_client=client, automatic_recovery=False) as service:
    def settled(task):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = service.job(task["id"])
            if result["status"] not in ("queued", "running"):
                return result
            time.sleep(.01)
        raise RuntimeError("Own synthetic sync timed out")
    ident = service.add_model_root(str(fixtures))["id"]
    settled(service.scan_model_directory(ident))
    job = settled(service.sync_model_scope({"root_id": ident}, False))
    assert (job["total"], job["processed"], job["errors"]) == (67, 67, 1)
    assert service.jobs()[0]["retryable_count"] == 1
# Remove only our newly generated failed item. The UI's real retry RPC will
# resolve this local missing file before it can send a request to Civitai.
(fixtures / "fixture-000.safetensors").unlink()
print("OWN_SYNC_RETRY_FIXTURE_READY")
