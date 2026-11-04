"""Synthetic HTTP/credential fixtures only; never use real accounts or models."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from PIL import Image

try:
    from . import civitai, credentials
except ImportError:
    civitai = credentials = None


SHA = "ab" * 32
TOKEN = "synthetic-test-secret-never-a-real-token"
COVER = "https://image.civitai.com/synthetic/width=512/example.png"


def version():
    return {"id": 31, "modelId": 17, "name": "Synthetic v1", "baseModel": "Illustrious",
            "description": "<p>Version <b>notes</b></p>", "updatedAt": "2026-10-18T00:00:00Z",
            "model": {"name": "Synthetic checkpoint", "type": "Checkpoint"},
            "trainedWords": ["synthetic word"],
            "files": [{"name": "synthetic.safetensors", "type": "Model", "hashes": {"SHA256": SHA.upper(), "AutoV2": SHA[:10].upper()}, "primary": True}],
            "images": [{"url": COVER, "nsfwLevel": 1, "meta": {"prompt": "do not return"}}]}


class Reply(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None, url=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.url = url

    def geturl(self):
        return self.url


class Transport:
    """Inject below real request construction and response validation."""
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def document(value, **kwargs):
    return Reply(json.dumps(value).encode(), **kwargs)


class CivitaiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(civitai, "Civitai transport and credentials are not implemented")

    def client(self, *replies, token=TOKEN):
        transport = Transport(*replies)
        return civitai.CivitaiClient(lambda: token, transport), transport

    def test_original_cdn_cover_uses_bounded_thumbnail_route_without_following_redirect(self):
        image = Image.new('RGB', (8, 6), (21, 42, 63)); buffer = io.BytesIO(); image.save(buffer, 'PNG')
        url = 'https://image.civitai.com/route/image-id/original=true/cover.jpeg'
        transport = Transport(Reply(buffer.getvalue()))
        client = civitai.CivitaiClient(lambda: self.fail('Cover must not read credentials'), transport)
        client.download_cover(url)
        request = transport.requests[0][0]
        self.assertEqual('https://image.civitai.com/route/image-id/width=640,height=640,fit=scale-down,quality=85,anim=false/cover.jpeg', request.full_url)
        self.assertIsNone(request.get_header('Authorization'))

    def test_cover_follows_public_blob_redirect_without_credentials(self):
        buffer = io.BytesIO(); Image.new('RGB', (8, 6), (12, 34, 56)).save(buffer, 'PNG')
        target = 'https://blobs-b2.civitai.com/file/blobs-managed-public/ABCDEFGHIJKL123456789.png'
        transport = Transport(urllib.error.HTTPError(COVER, 301, 'moved', {'Location': target}, None), Reply(buffer.getvalue()))
        client = civitai.CivitaiClient(lambda: self.fail('Cover must not read credentials'), transport)
        self.assertEqual(buffer.getvalue(), client.download_cover(COVER))
        self.assertEqual(target, transport.requests[-1][0].full_url)
        self.assertTrue(all(request.get_header('Authorization') is None for request, _ in transport.requests))

    def test_cover_redirect_rejects_external_host_private_bucket_and_query(self):
        for target in ('https://private.example/image.png',
                       'http://blobs-b2.civitai.com/file/blobs-managed-public/ABCDEFGHIJKL123456789.png',
                       'https://blobs-b2.civitai.com/file/private/ABCDEFGHIJKL123456789.png',
                       'https://blobs-b2.civitai.com/file/blobs-managed-public/ABCDEFGHIJKL123456789.png?token=private',
                       'https://image.civitai.com@private.example/image.png'):
            transport = Transport(Reply(b'', status=302, headers={'Location': target}))
            client = civitai.CivitaiClient(transport=transport)
            self.assert_error('redirect', lambda: client.download_cover(COVER))
            self.assertEqual(1, len(transport.requests))

    def test_cover_redirect_chain_has_fixed_hop_limit(self):
        target = 'https://blobs-b2.civitai.com/file/blobs-managed-public/ABCDEFGHIJKL123456789.png'
        transport = Transport(*(Reply(b'', status=302, headers={'Location': target}) for _ in range(4)))
        client = civitai.CivitaiClient(transport=transport)
        self.assert_error('redirect', lambda: client.download_cover(COVER))
        self.assertEqual(3, len(transport.requests))

    def assert_error(self, code, operation):
        with self.assertRaises(civitai.CivitaiError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)
        rendered = str(caught.exception) + repr(caught.exception)
        for secret in (TOKEN, SHA, "private body", "private.example"):
            self.assertNotIn(secret, rendered)

    def test_exact_sha_normalizes_whitelisted_fields_without_fetching_cover(self):
        client, transport = self.client(document(version()))
        result = client.version_by_hash(SHA)
        self.assertEqual((17, 31, "Stable Diffusion", "SDXL"),
                         (result["model_id"], result["version_id"], result["family"], result["model_variant"]))
        self.assertEqual(SHA, result["matched_sha256"])
        self.assertEqual("sha256", result["matched_hash_kind"])
        self.assertEqual("Version notes", result["version_description"])
        self.assertEqual([{"url": COVER, "nsfw": False}], result["images"])
        self.assertNotIn("prompt", json.dumps(result))
        self.assertEqual(1, len(transport.requests))
        request, timeout = transport.requests[0]
        self.assertEqual("https://civitai.com/api/v1/model-versions/by-hash/" + SHA, request.full_url)
        self.assertEqual("Bearer " + TOKEN, request.get_header("Authorization"))
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 15)

    def test_sha_missing_mismatch_and_other_algorithm_cannot_confirm_identity(self):
        for hashes in ({}, {"SHA256": "cd" * 32}, {"BLAKE3": SHA}, {"AutoV2": SHA[:10]}):
            with self.subTest(hashes=hashes):
                value = version()
                value["files"][0]["hashes"] = hashes
                client, _ = self.client(document(value))
                self.assert_error("hash_mismatch", lambda: client.version_by_hash(SHA))

    def test_trigger_words_alias_uses_only_explicit_nonempty_trained_words(self):
        value = version()
        value["trainedWords"] = ["  synthetic phrase  ", "", "\x00\x01", None, 7, "second"]
        value["tags"] = ["not a trigger"]
        value["model"]["tags"] = ["not evidence"]
        value["source_url"] = "https://private.example/"
        client, _ = self.client(document(value))
        result = client.version_by_hash(SHA)
        self.assertEqual(["synthetic phrase", "second"], result.get("trigger_words"))
        self.assertEqual(result["trigger_words"], result["trained_words"])
        self.assertEqual("https://civitai.com/models/17?modelVersionId=31", result["source_url"])

    def test_trigger_words_absent_is_empty_and_cannot_guess_from_model_name(self):
        value = version()
        del value["trainedWords"]
        value["model"]["name"] = "synthetic trigger-like name"
        client, _ = self.client(document(value))
        result = client.version_by_hash(SHA)
        self.assertEqual([], result.get("trigger_words"))
        self.assertEqual([], result["trained_words"])

    def test_autov2_requires_explicit_exact_field_not_sha_prefix(self):
        client, _ = self.client(document(version()))
        result = client.version_by_hash(SHA[:10])
        self.assertEqual("autov2", result["matched_hash_kind"])
        self.assertEqual(SHA, result["matched_sha256"])
        value = version()
        del value["files"][0]["hashes"]["AutoV2"]
        client, _ = self.client(document(value))
        self.assert_error("hash_mismatch", lambda: client.version_by_hash(SHA[:10]))

    def test_autov2_collision_different_returned_sha_fails_closed(self):
        value = version()
        value["files"].append({"name": "collision.safetensors", "hashes": {"AutoV2": SHA[:10], "SHA256": "cd" * 32}})
        client, _ = self.client(document(value))
        self.assert_error("hash_mismatch", lambda: client.version_by_hash(SHA[:10]))

    def test_invalid_hash_does_not_request(self):
        client, transport = self.client()
        for digest in ("", "a" * 8, "a" * 12, "a" * 63, "g" * 64, SHA + "/other", None):
            self.assert_error("invalid_hash", lambda: client.version_by_hash(digest))
        self.assertEqual([], transport.requests)

    def test_http_errors_are_fixed_and_do_not_echo_body_or_url(self):
        for status, code in ((401, "unauthorized"), (403, "forbidden"), (404, "not_found"), (429, "rate_limited"), (500, "unavailable")):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://private.example/" + SHA, status, TOKEN, {}, io.BytesIO(b"private body"))
                client, _ = self.client(error)
                self.assert_error(code, lambda: client.version_by_hash(SHA))

    def test_redirects_rejected_even_same_origin_and_never_replayed(self):
        for target in ("https://civitai.com/api/v1/me", "https://private.example/" + TOKEN):
            client, transport = self.client(Reply(b"", status=302, headers={"Location": target}))
            self.assert_error("redirect", lambda: client.version_by_hash(SHA))
            self.assertEqual(1, len(transport.requests))
        client, _ = self.client(document(version(), url="https://private.example/"))
        self.assert_error("redirect", lambda: client.version_by_hash(SHA))
        handler = civitai._NoRedirect()
        self.assertIsNone(handler.redirect_request(urllib.request.Request("https://civitai.com/api/v1/me"), None, 302, "", {}, "https://private.example/"))

    def test_json_body_limit_streaming_and_content_length(self):
        for reply in (Reply(b" " * (2 * 1024 * 1024 + 1)), Reply(b"{}", headers={"Content-Length": str(2 * 1024 * 1024 + 1)})):
            client, _ = self.client(reply)
            self.assert_error("response_too_large", lambda: client.version_by_hash(SHA))
            self.assertTrue(reply.closed)

    def test_invalid_json_nested_types_and_missing_ids_are_safe_errors(self):
        for body in (b"<html>private body</html>", b"[]", b'{"id":true}', b'{"id":31,"modelId":17,"files":null}', b"{" * 1500):
            client, _ = self.client(Reply(body))
            self.assert_error("invalid_response", lambda: client.version_by_hash(SHA))

    def test_timeout_and_transport_exception_are_redacted(self):
        for failure in (socket.timeout(TOKEN), urllib.error.URLError("private.example"), OSError(SHA)):
            client, _ = self.client(failure)
            self.assert_error("network", lambda: client.version_by_hash(SHA))

    def test_cancellation_before_and_during_read_closes_response(self):
        client, transport = self.client(document(version()))
        self.assert_error("cancelled", lambda: client.version_by_hash(SHA, cancelled=lambda: True))
        self.assertEqual([], transport.requests)
        state = {"cancelled": False}
        class CancellingReply(Reply):
            def read(self, size=-1):
                result = super().read(size)
                state["cancelled"] = True
                return result
        reply = CancellingReply(json.dumps(version()).encode())
        client, _ = self.client(reply)
        self.assert_error("cancelled", lambda: client.version_by_hash(SHA, cancelled=lambda: state["cancelled"]))
        self.assertTrue(reply.closed)

    def test_model_html_becomes_plain_text_with_no_script_style_image(self):
        value = {"id": 17, "name": "Synthetic", "type": "Checkpoint", "description": '<p>Hello &amp; <b>world</b></p><script>secret()</script><style>hidden</style><img src="https://private.example" alt="image"><p>Next</p>', "creator": {"username": "Synthetic author", "image": COVER}}
        client, transport = self.client(document(value))
        result = client.model(17)
        self.assertEqual("Hello & world\nNext", result["description"])
        self.assertEqual("Synthetic author", result["creator"])
        self.assertEqual(1, len(transport.requests))
        self.assertNotIn("image", result)

    def test_model_id_validation_and_returned_model_id_match(self):
        client, transport = self.client()
        for identifier in (True, 0, -1, "1/../../me", "https://private.example"):
            self.assert_error("invalid_id", lambda: client.model(identifier))
        self.assertEqual([], transport.requests)
        client, _ = self.client(document({"id": 18, "name": "Other"}))
        self.assert_error("invalid_response", lambda: client.model(17))

    def test_creator_is_bounded_and_only_comes_from_explicit_username(self):
        client, _ = self.client(document({'id':17,'creator':{'username':'\x00'+'a'*600,'image':COVER}}))
        result = client.model(17)
        self.assertLessEqual(len(result['creator']),500)
        self.assertNotIn('\x00',result['creator'])
        for creator in (None,'not a username object',{'name':'not a username'},{'username':7}):
            value=version();value['model']['creator']=creator
            client, _ = self.client(document(value))
            self.assertEqual(client.version_by_hash(SHA)['creator'],'')

    def test_unknown_base_stays_unknown_and_lora_role_is_preserved(self):
        for base, family in (("Future Super SDXLish", "未知"), ("Pony", "Stable Diffusion"), ("Flux.1 D", "FLUX"), ("Anima", "Anima"), ("Krea 2", "Krea 2"), ("NovelAI", "NovelAI")):
            value = version()
            value["baseModel"] = base
            value["model"]["type"] = "LORA"
            client, _ = self.client(document(value))
            result = client.version_by_hash(SHA)
            self.assertEqual(family, result["family"])
            self.assertEqual("LORA", result["type"])

    def test_strings_and_collection_sizes_are_bounded(self):
        value = version()
        value["name"] = "v" * 5000
        value["description"] = "x" * 60000
        value["trainedWords"] = ["w" * 5000] * 200
        client, _ = self.client(document(value))
        result = client.version_by_hash(SHA)
        self.assertLessEqual(len(result["version_name"]), 500)
        self.assertLessEqual(len(result["version_description"]), 20000)
        self.assertLessEqual(len(result["trained_words"]), 128)
        self.assertLessEqual(len(result["trained_words"][0]), 500)
        self.assertEqual(result["trained_words"], result.get("trigger_words"))

    def test_connection_with_token_uses_authenticated_me_and_redacts_identity(self):
        client, transport = self.client(document({"id": 27, "username": TOKEN, "status": "active"}))
        result = client.check_connection()
        self.assertTrue(result["ok"])
        self.assertTrue(result["authenticated"])
        self.assertEqual("https://civitai.com/api/v1/me", transport.requests[0][0].full_url)
        self.assertNotIn(TOKEN, json.dumps(result))
        client, _ = self.client(urllib.error.HTTPError("private.example", 401, TOKEN, {}, None))
        result = client.check_connection()
        self.assertFalse(result["ok"])
        self.assertEqual("unauthorized", result["code"])

    def test_connection_anonymous_does_not_claim_authentication(self):
        client, transport = self.client(document({"items": [], "metadata": {}}), token="")
        result = client.check_connection()
        self.assertTrue(result["ok"])
        self.assertFalse(result["authenticated"])
        self.assertEqual("https://civitai.com/api/v1/models?limit=1", transport.requests[0][0].full_url)
        self.assertIsNone(transport.requests[0][0].get_header("Authorization"))

    def test_token_newlines_and_invalid_provider_fail_without_network(self):
        for token in (TOKEN + "\r\nX: injected", "x\ny", "x y", "汉字", None, 0):
            client, transport = self.client(token=token)
            self.assert_error("credentials", lambda: client.version_by_hash(SHA))
            self.assertEqual([], transport.requests)

    def test_conflicting_nsfw_labels_never_mark_preview_safe(self):
        value = version()
        value["images"] = [{"url": COVER, "nsfw": False, "nsfwLevel": 16}]
        client, _ = self.client(document(value))
        self.assertTrue(client.version_by_hash(SHA)["images"][0]["nsfw"])

    def test_duplicate_json_identity_keys_are_rejected(self):
        body = json.dumps(version()).encode().replace(b'"id": 31', b'"id": 31, "id": 32')
        client, _ = self.client(Reply(body))
        self.assert_error("invalid_response", lambda: client.version_by_hash(SHA))

    def test_total_deadline_rejects_slow_drip_despite_small_reads(self):
        client, _ = self.client(document(version()))
        with patch.object(civitai.time, "monotonic", side_effect=[0, 31]):
            self.assert_error("network", lambda: client.version_by_hash(SHA))

    def test_cover_valid_header_bounded_bytes_without_bearer_or_token_provider(self):
        data = io.BytesIO()
        Image.new("RGB", (32, 24), "blue").save(data, format="PNG")
        transport = Transport(Reply(data.getvalue(), headers={"Content-Type": "image/png"}))
        client = civitai.CivitaiClient(lambda: self.fail("cover must never read token"), transport)
        result = client.download_cover(COVER)
        self.assertEqual(data.getvalue(), result)
        self.assertIsNone(transport.requests[0][0].get_header("Authorization"))

    def test_cover_origin_credentials_query_and_redirects_rejected(self):
        client, transport = self.client()
        for url in ("http://image.civitai.com/a.png", "https://image.civitai.com.evil/a.png", "https://user@image.civitai.com/a.png", "https://image.civitai.com:444/a.png", "https://image.civitai.com/a.png?token=" + TOKEN, "https://private.example/a.png", "https://image.civitai.com/a.png#fragment"):
            self.assert_error("invalid_cover", lambda: client.download_cover(url))
        self.assertEqual([], transport.requests)
        client, _ = self.client(Reply(b"", status=301, headers={"Location": "https://private.example"}))
        self.assert_error("redirect", lambda: client.download_cover(COVER))

    def test_cover_invalid_format_oversize_and_dimensions_rejected(self):
        huge = io.BytesIO()
        Image.new("L", (9000, 1)).save(huge, format="PNG")
        for reply, code in ((Reply(b"<svg>bad</svg>", headers={"Content-Type": "image/svg+xml"}), "invalid_cover"), (Reply(b"x" * (8 * 1024 * 1024 + 1)), "response_too_large"), (Reply(huge.getvalue(), headers={"Content-Type": "image/png"}), "invalid_cover")):
            client, _ = self.client(reply)
            self.assert_error(code, lambda: client.download_cover(COVER))


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(credentials, "Civitai credential protection is not implemented")
        self.temp = tempfile.TemporaryDirectory(prefix="luma-civitai-credentials-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "credentials"

    def test_session_secret_never_written_and_clear_removes_it(self):
        store = credentials.CivitaiCredentials(self.directory)
        self.assertFalse(store.configured())
        self.assertEqual("", store.get())
        store.set(TOKEN, remember=False)
        self.assertEqual(TOKEN, store.get())
        self.assertTrue(store.configured())
        self.assertFalse(self.directory.exists())
        store.clear()
        self.assertEqual("", store.get())

    def test_safe_status_distinguishes_session_from_remembered_without_get(self):
        store = credentials.CivitaiCredentials(self.directory)
        with patch.object(store, "get", side_effect=AssertionError("status may not read secret")):
            self.assertEqual({"configured": False, "remembered": False}, store.status())
            store.set(TOKEN, remember=False)
            self.assertEqual({"configured": True, "remembered": False}, store.status())
            if os.name == "nt":
                store.set(TOKEN, remember=True)
                self.assertEqual({"configured": True, "remembered": True}, store.status())

    def test_dpapi_real_windows_roundtrip_never_persists_plaintext(self):
        store = credentials.CivitaiCredentials(self.directory)
        if os.name != "nt":
            with self.assertRaises(credentials.CredentialsError):
                store.set(TOKEN, remember=True)
            self.assertFalse(self.directory.exists())
            return
        store.set(TOKEN, remember=True)
        blobs = [p.read_bytes() for p in self.directory.iterdir()]
        self.assertEqual(1, len(blobs))
        self.assertNotIn(TOKEN.encode(), blobs[0])
        self.assertEqual(TOKEN, credentials.CivitaiCredentials(self.directory).get())
        store.set("synthetic-session-replacement", remember=False)
        self.assertEqual("", credentials.CivitaiCredentials(self.directory).get())
        store.set(TOKEN, remember=True)
        store.clear()
        self.assertFalse(store.configured())
        self.assertEqual([], list(self.directory.iterdir()))

    def test_invalid_secret_rejected_without_file_or_error_echo(self):
        store = credentials.CivitaiCredentials(self.directory)
        for value in ("", TOKEN + "\r\n", "x y", "x" * 4097, None):
            with self.assertRaises(credentials.CredentialsError) as caught:
                store.set(value)
            self.assertNotIn(TOKEN, str(caught.exception))
        self.assertFalse(self.directory.exists())

    def test_dpapi_corruption_fails_closed_without_plaintext_fallback(self):
        if os.name != "nt":
            return
        store = credentials.CivitaiCredentials(self.directory)
        store.set(TOKEN)
        path = next(self.directory.iterdir())
        path.write_bytes(b"corrupt encrypted synthetic fixture")
        with self.assertRaises(credentials.CredentialsError):
            credentials.CivitaiCredentials(self.directory).get()


if __name__ == "__main__":
    unittest.main()
