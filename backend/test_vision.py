"""Isolated visual classifier safety tests; no image library or network reads."""
from contextlib import ExitStack
import hashlib
import io
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
import numpy as np

from . import vision


class Reply(io.BytesIO):
    pass


class DownloadFixture:
    def __init__(self, blobs):
        self.blobs, self.requests = blobs, []

    def open(self, request, timeout):
        self.requests.append(request)
        name = request.full_url.rsplit("/",1)[-1]
        return Reply(self.blobs[name])


class VisionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="luma-vision-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "models"
        self.classifier = vision.LocalContentClassifier(self.directory)
        self.definitions = [{"id":"blue", "name":"蓝色圆形", "description":"a drawing of a blue circle on a white background", "threshold":0.28}]
        self.blobs = {"text.onnx":b"text model fixture", "image.onnx":b"image model fixture", "tokenizer.json":b'{"test":true}'}
        self.assets = tuple(vision.ModelAsset(name,name,len(blob),hashlib.sha256(blob).hexdigest()) for name,blob in self.blobs.items())
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(vision,"MODEL_ASSETS",self.assets))
        self.stack.enter_context(patch.object(vision,"DOWNLOAD_BYTES",sum(a.size for a in self.assets)))

    def install_fixture(self):
        fixture=DownloadFixture(self.blobs)
        with patch.object(vision.urllib.request,"build_opener",return_value=fixture):
            result=self.classifier.install()
        return fixture,result

    def test_status_and_constructor_never_create_directories_or_download(self):
        with patch.object(vision.urllib.request,"build_opener",side_effect=AssertionError("network")):
            state=self.classifier.status()
        self.assertFalse(state["ready"])
        self.assertEqual(state["status"],"missing")
        self.assertFalse(self.directory.exists())

    def test_missing_model_never_opens_an_image_or_downloads(self):
        with patch.object(vision.Image,"open",side_effect=AssertionError("private image")), patch.object(vision.urllib.request,"build_opener",side_effect=AssertionError("network")):
            with self.assertRaises(vision.VisionError) as caught:
                self.classifier.classify(self.root/"not-opened.png",self.definitions)
        self.assertEqual(caught.exception.code,"not_ready")

    def test_definitions_reject_invalid_threshold_duplicate_and_non_english(self):
        for threshold in (float("nan"),float("inf"),-0.1,True,1.1):
            with self.subTest(threshold=threshold),self.assertRaises(vision.VisionError):
                vision.normalize_definitions([{**self.definitions[0],"threshold":threshold}])
        with self.assertRaises(vision.VisionError):
            vision.normalize_definitions(self.definitions*2)
        with self.assertRaises(vision.VisionError):
            vision.normalize_definitions([{**self.definitions[0],"description":"中文描述不能作为英文模型输入"}])
        with self.assertRaises(vision.VisionError):
            vision.normalize_definitions([{**self.definitions[0],"description":"a"*501}])
        self.assertEqual(vision.normalize_definitions([{**self.definitions[0],"name":"自定义中文分类"}])[0]["name"],"自定义中文分类")

    def test_relative_model_directory_is_rejected(self):
        with self.assertRaises(vision.VisionError):
            vision.LocalContentClassifier("relative/models")

    def test_downloads_only_pinned_public_get_requests_and_verifies_all_hashes(self):
        fixture,result=self.install_fixture()
        self.assertTrue(result["ready"])
        self.assertEqual(result["integrity"],"verified")
        self.assertEqual(len(fixture.requests),3)
        for request in fixture.requests:
            self.assertIn("/resolve/"+vision.MODEL_REVISION+"/",request.full_url)
            self.assertTrue(request.full_url.startswith("https://huggingface.co/"))
            self.assertEqual(request.get_method(),"GET")
            self.assertIsNone(request.data)
        self.assertEqual(list(self.directory.glob("*.part")),[])
        for name,blob in self.blobs.items():
            self.assertEqual((self.directory/name).read_bytes(),blob)
        with patch.object(vision.urllib.request,"build_opener") as opener:
            self.classifier.install()
        opener.return_value.open.assert_not_called()

    def test_bad_download_is_not_published_and_partial_is_removed(self):
        fixture=DownloadFixture({**self.blobs,"text.onnx":b"bad"})
        with patch.object(vision.urllib.request,"build_opener",return_value=fixture):
            with self.assertRaises(vision.VisionError) as caught:
                self.classifier.install()
        self.assertEqual(caught.exception.code,"integrity")

        self.assertFalse((self.directory/"text.onnx").exists())
        self.assertEqual(list(self.directory.iterdir()),[])

    def test_cancelled_download_cleans_partial_and_releases_install_lock(self):
        fixture=DownloadFixture(self.blobs)
        cancelled=threading.Event()
        with patch.object(vision.urllib.request,"build_opener",return_value=fixture):
            with self.assertRaises(vision.VisionCancelled):
                self.classifier.install(progress=lambda _:cancelled.set(),cancelled=cancelled.is_set)
        self.assertFalse((self.directory/"text.onnx").exists())
        self.assertEqual(list(self.directory.iterdir()),[])
        self.assertTrue(self.install_fixture()[1]["ready"])

    def test_same_size_corruption_is_rejected_before_any_image_read(self):
        self.install_fixture()
        target=self.directory/"image.onnx"
        data=target.read_bytes()
        target.write_bytes(bytes([data[0]^1])+data[1:])
        self.assertEqual(target.stat().st_size,len(data))
        with patch.object(vision.Image,"open",side_effect=AssertionError("private image")):
            with self.assertRaises(vision.VisionError) as caught:
                self.classifier.classify(self.root/"not-opened.png",self.definitions)
        self.assertEqual(caught.exception.code,"integrity")
        # Same-size corrupt files must allow an explicit UI reinstall.
        self.assertFalse(self.classifier.status()["ready"])
        self.assertEqual(self.classifier.status()["status"],"integrity_failed")
        self.install_fixture()
        self.assertTrue(self.classifier.status()["ready"])
        self.assertEqual(self.classifier.status()["integrity"],"verified")

    def test_public_redirect_rejects_http_private_hosts_and_credentials(self):
        handler=vision._PublicRedirectHandler()
        request=vision.urllib.request.Request("https://huggingface.co/fixed")
        for url in ("http://huggingface.co/a","https://127.0.0.1/a","https://example.org/a","https://u:p@huggingface.co/a","https://huggingface.co:8000/a"):
            with self.subTest(url=url),self.assertRaises(vision.VisionError):
                handler.redirect_request(request,None,302,"",{},url)

    def test_model_junction_chain_is_rejected_without_writing_outside(self):
        outside=self.root/"outside"
        outside.mkdir()
        if os.name=="nt":
            result=subprocess.run(["cmd","/c","mklink","/J",str(self.directory),str(outside)],capture_output=True)
            if result.returncode:
                self.skipTest("NTFS junction creation unavailable")
        else:
            self.directory.symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.classifier.status()["status"],"unsafe")
        with patch.object(vision.urllib.request,"build_opener",side_effect=AssertionError("network")):
            with self.assertRaises(vision.VisionError):
                self.classifier.install()
        self.assertEqual(list(outside.iterdir()),[])

    def test_synthetic_rgba_preprocessing_is_finite_normalized_and_bounded(self):
        path=self.root/"synthetic-alpha.png"
        with Image.new("RGBA",(600,30),(255,0,0,120)) as image:
            image.save(path)
        pixels=vision.LocalContentClassifier._pixels(path,None)
        self.assertEqual(pixels.shape,(1,3,224,224))
        self.assertEqual(pixels.dtype,np.float32)
        self.assertTrue(np.isfinite(pixels).all())
        self.assertAlmostEqual(float(pixels[0,0,100,100]),(1-0.48145466)/0.26862954,places=5)

    def test_decode_limit_and_cancel_are_checked_before_loading_pixels(self):
        path=self.root/"synthetic.png"
        with Image.new("RGB",(10,10)) as image:
            image.save(path)
        with patch.object(vision,"MAX_IMAGE_PIXELS",50):
            with self.assertRaises(vision.VisionError) as caught:
                vision.LocalContentClassifier._pixels(path,None)
        self.assertEqual(caught.exception.code,"image_limit")
        with patch.object(vision.Image,"open",side_effect=AssertionError("pixels")):
            with self.assertRaises(vision.VisionCancelled):
                vision.LocalContentClassifier._pixels(path,lambda:True)

    def test_two_work_slots_and_waiting_cancellation_remain_bounded(self):
        held=threading.Event()
        release=threading.Event()
        def worker():
            with self.classifier._slot(None):
                held.set()
                release.wait(2)
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:thread.start()
        self.assertTrue(held.wait(1))
        deadline=time.monotonic()+1
        while vision._WORK_SLOTS._value and time.monotonic()<deadline:time.sleep(0.005)
        try:
            with self.assertRaises(vision.VisionCancelled):
                with self.classifier._slot(lambda:True):pass
        finally:
            release.set()
            for thread in threads:thread.join(2)
        self.assertEqual(vision._WORK_SLOTS._value,2)


if __name__=="__main__":
    unittest.main()
