"""Only generated JPEG/PNG fixtures; never consult a user library."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageFile, PngImagePlugin

from . import metadata


class CommentMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def jpeg(self, *comments):
        path = self.root / "synthetic.jpg"
        Image.new("RGB", (24, 16), "navy").save(path)
        original = path.read_bytes()
        chunks = []
        for text in comments:
            value = text.encode("utf-8") if isinstance(text, str) else text
            self.assertLessEqual(len(value), 65533)
            chunks.append(b"\xff\xfe" + (len(value) + 2).to_bytes(2, "big") + value)
        path.write_bytes(original[:2] + b"".join(chunks) + original[2:])
        return path

    def graph(self, text="花, landscape"):
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "unregistered-model"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": text}},
            "3": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["4", 0]}},
            "4": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["2", 0]}},
            "99": {"class_type": "CLIPTextEncode", "inputs": {"text": "unused branch"}},
        }

    def png_graph(self, graph, key="prompt"):
        path = self.root / "synthetic.png"
        info = PngImagePlugin.PngInfo()
        info.add_itxt(key, json.dumps(graph, ensure_ascii=False))
        Image.new("RGB", (24, 16)).save(path, pnginfo=info)
        return metadata.extract_metadata(path)

    def test_jpeg_comment_a1111_utf8_no_pixels_and_unchanged_bytes(self):
        path = self.jpeg("花, landscape\nNegative prompt: blur\nSteps: 20, Model: unregistered-model")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(Image.Image, "load", side_effect=AssertionError("pixel decode")), \
                patch.object(ImageFile.ImageFile, "load", side_effect=AssertionError("codec pixel decode")):
            result = metadata.extract_metadata(path)
        self.assertEqual(result["prompt"], "花, landscape")
        self.assertEqual(result["negative_prompt"], "blur")
        self.assertEqual(result["family"], "未知")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_jpeg_json_and_explicit_graph_labels(self):
        for prefix in ("", "prompt=", "workflow="):
            with self.subTest(prefix=prefix):
                result = metadata.extract_metadata(self.jpeg(prefix + json.dumps(self.graph(), ensure_ascii=False)))
                self.assertEqual(result["prompt"], "花, landscape")
                self.assertEqual(result["negative_prompt"], "")
                self.assertEqual(result["family"], "未知")

    def test_label_rejects_junk_nonfinite_oversize_and_fake_model(self):
        valid = json.dumps(self.graph())
        for value in ("prompt=" + valid + "junk", "workflow={\"model\":\"anima\"}",
                      "prompt=" + valid[:-1] + ', "bad": NaN}',
                      "prompt=" + valid[:-1] + ', "bad": 1e999}',
                      "caption prompt=" + valid, "prompt=" + " " * metadata.MAX_TEXT_BYTES + valid):
            with self.subTest(value=value[:40]):
                records = list(metadata._metadata_records({"comment": value}))
                self.assertFalse(records)

    def test_multiple_comments_independent_not_stitched_and_bounded(self):
        path = self.jpeg("flower\nSteps: 10, Model: opaque-a", "sky\nSteps: 20, Model: opaque-b")
        result = metadata.extract_metadata(path)
        self.assertEqual(set(result["parameters"]["model_candidates"]), {"opaque-a", "opaque-b"})
        self.assertEqual(metadata.extract_metadata(self.jpeg('prompt={"1":', '"bad"}'))["model"], "")
        with patch.object(metadata, "MAX_RAW_BYTES", 64):
            result = metadata.extract_metadata(self.jpeg("x" * 65, "flower\nSteps: 1, Model: opaque"))
        self.assertNotIn("x" * 65, str(result["raw_metadata"]))

    def test_unknown_binary_info_and_invalid_utf8_not_decoded(self):
        result = metadata.extract_metadata(self.jpeg(b"\xff\xfe\x00Model: anima"))
        self.assertEqual(result["model"], "")
        self.assertEqual(result["prompt"], "")
        self.assertNotIn("comment", result["raw_metadata"])

    def test_string_chain_shared_dag_and_zero_conditioning(self):
        graph = self.graph(["7", 0])
        graph["5"] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "花"}}
        graph["6"] = {"class_type": "StringConcatenate", "inputs": {"string_a": ["5", 0], "string_b": "sky", "delimiter": ", "}}
        graph["7"] = {"class_type": "StringConcatenate", "inputs": {"string_a": ["6", 0], "string_b": ["6", 0], "delimiter": " | "}}
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "花, sky | 花, sky")
        self.assertEqual(result["negative_prompt"], "")
        self.assertNotIn("prompt_extraction_incomplete", result["parameters"])
        self.assertEqual(result["family"], "未知")

    def test_string_cycle_unknown_slot_and_dynamic_are_partial(self):
        for node in (
            {"class_type": "UnknownDynamicText", "inputs": {"text": "MUST NOT LEAK"}},
            {"class_type": "PrimitiveStringMultiline", "inputs": {"value": ["6", 0]}},
        ):
            graph = self.graph(["7", 0])
            graph["6"] = node
            graph["7"] = {"class_type": "StringConcatenate", "inputs": {"string_a": "known", "string_b": ["6", 0], "delimiter": ", "}}
            result = self.png_graph(graph)
            self.assertIn("known", result["prompt"])
            self.assertNotIn("MUST NOT LEAK", result["prompt"])
            self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))
        graph = self.graph(["5", 1])
        graph["5"] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "wrong slot"}}
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_no_sampler_does_not_promote_unused_text(self):
        graph = self.graph()
        del graph["3"]
        self.assertEqual(self.png_graph(graph)["prompt"], "")

    def test_unknown_conditioning_transform_does_not_expose_input(self):
        graph = self.graph()
        graph["6"] = {"class_type": "UnknownConditionTransform", "inputs": {"conditioning": ["2", 0]}}
        graph["3"]["inputs"]["positive"] = ["6", 0]
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_lora_loader_text_not_output_and_toggle_switches(self):
        graph = self.graph(["6", 0])
        graph["5"] = {"class_type": "Lora Loader (LoraManager)", "inputs": {"text": "NOT TRIGGER WORDS", "loras": []}}
        graph["6"] = {"class_type": "TriggerWord Toggle (LoraManager)", "inputs": {
            "trigger_words": ["5", 2], "orinalMessage": "active, disabled", "group_mode": False,
            "default_active": True, "allow_strength_adjustment": False,
            "toggle_trigger_words": [{"text": "active", "active": True}, {"text": "disabled", "active": False}]}}
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "active")
        self.assertNotIn("prompt_extraction_incomplete", result["parameters"])
        graph["5"]["inputs"]["loras"] = [{"name": "opaque", "active": True, "strength": 1}]
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_toggle_override_strength_group_and_wrapped_values(self):
        inputs = {"trigger_words": "changed", "orinalMessage": "original", "group_mode": True,
                  "default_active": False, "allow_strength_adjustment": False,
                  "toggle_trigger_words": [{"text": "ignored", "active": False}]}
        graph = self.graph(["6", 0])
        graph["6"] = {"class_type": "TriggerWord Toggle (LoraManager)", "inputs": inputs}
        self.assertEqual(self.png_graph(graph)["prompt"], "changed")
        inputs["trigger_words"] = {"__value__": "original"}
        inputs["orinalMessage"] = {"__value__": "original"}
        inputs["allow_strength_adjustment"] = True
        inputs["toggle_trigger_words"] = {"__value__": json.dumps([
            {"text": "group", "active": True, "strength": 1.25, "items": [
                {"text": "a", "active": True}, {"text": "b", "active": False}]},
            {"text": "off", "active": False}])}
        self.assertEqual(self.png_graph(graph)["prompt"], "(a:1.25)")

    def test_ultimate_upscale_condition_links(self):
        graph = self.graph()
        graph["3"]["class_type"] = "UltimateSDUpscale"
        graph["3"]["inputs"]["seed"] = 12
        self.assertEqual(self.png_graph(graph)["prompt"], "花, landscape")

    def test_workflow_disabled_text_and_sampler_do_not_contribute(self):
        workflow = {"nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["unregistered-model"]},
            {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["disabled text"], "mode": 4},
            {"id": 3, "type": "KSampler", "inputs": [{"name": "model", "link": 1}, {"name": "positive", "link": 2}]},
        ], "links": [[1, 1, 0, 3, 0, "MODEL"], [2, 2, 0, 3, 1, "CONDITIONING"]]}
        result = self.png_graph(workflow, "workflow")
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_condition_dag_and_expansion_budget(self):
        graph = self.graph()
        for index in range(5, 60):
            previous = "2" if index == 5 else str(index - 1)
            graph[str(index)] = {"class_type": "ConditioningCombine", "inputs": {
                "conditioning_1": [previous, 0], "conditioning_2": [previous, 0]}}
        graph["3"]["inputs"]["positive"] = ["59", 0]
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "花, landscape")
        self.assertLessEqual(len(result["prompt"].encode("utf-8")), metadata.MAX_TEXT_BYTES)

    def test_unknown_binary_pillow_info_is_never_text(self):
        path = self.jpeg()
        from PIL import JpegImagePlugin
        original = JpegImagePlugin.JpegImageFile._open
        def additional_info(image):
            original(image)
            image.info["unexpected_binary"] = b"Steps: 20, Model: anima"
        with patch.object(JpegImagePlugin.JpegImageFile, "_open", additional_info):
            result = metadata.extract_metadata(path)
        self.assertNotIn("unexpected_binary", result["raw_metadata"])
        self.assertEqual(result["model"], "")

    def test_com_limits_marker_limit_and_truncated_marker(self):
        path = self.jpeg("a" * 65533)
        self.assertEqual(len(metadata._read_header(path)[3]["jpeg_comments"][0]), 65533)
        with patch.object(metadata, "MAX_TEXT_BYTES", 64):
            raw = metadata._read_header(path)[3]
            self.assertNotIn("jpeg_comments", raw)
            self.assertTrue(raw["jpeg_comments_truncated"])
        raw = metadata._read_header(self.jpeg(*([""] * (metadata.MAX_METADATA_ENTRIES + 1))))[3]
        self.assertEqual(len(raw["jpeg_comments"]), metadata.MAX_METADATA_ENTRIES)
        self.assertTrue(raw["jpeg_comments_truncated"])
        path = self.jpeg(*([""] * 4097))
        with self.assertRaises(ValueError):
            metadata._read_header(path)
        path.write_bytes(b"\xff\xd8\xff\xfe\xff\xffbad")
        with self.assertRaises(ValueError):
            metadata._read_header(path)

    def test_sampler_types_and_wrong_condition_output_slot(self):
        for node_type in ("KSampler", "KSamplerAdvanced", "UltimateSDUpscale"):
            graph = self.graph()
            graph["3"]["class_type"] = node_type
            graph["3"]["inputs"]["seed"] = 1
            self.assertEqual(self.png_graph(graph)["prompt"], "花, landscape")
        graph = self.graph()
        graph["6"] = {"class_type": "ConditioningCombine", "inputs": {"conditioning_1": ["2", 0]}}
        graph["3"]["inputs"]["positive"] = ["6", 1]
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_active_zero_strength_lora_is_still_external_unknown(self):
        graph = self.graph(["5", 2])
        graph["5"] = {"class_type": "Lora Loader (LoraManager)", "inputs": {
            "text": "not output", "loras": {"__value__": [{"name": "opaque", "active": True, "strength": 0, "clipStrength": 0}]}}}
        result = self.png_graph(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_concat_expansion_exceeds_budget_without_silent_truncation(self):
        graph = self.graph(["8", 0])
        graph["5"] = {"class_type": "PrimitiveString", "inputs": {"value": "abc" * 10}}
        for index in range(6, 9):
            graph[str(index)] = {"class_type": "StringConcatenate", "inputs": {
                "string_a": [str(index - 1), 0], "string_b": [str(index - 1), 0], "delimiter": ""}}
        # Direct parser keeps the synthetic JSON encoding outside this small
        # expansion budget, so the linked output itself is what gets tested.
        with patch.object(metadata, "MAX_TEXT_BYTES", 100):
            result = metadata._comfy(graph)
        self.assertEqual(result["prompt"], "")
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))

    def test_many_expanded_strings_share_a_cumulative_budget(self):
        graph = self.graph()
        graph["2"]["inputs"] = {"text_g": ["7", 0], "text_l": ["8", 0]}
        graph["5"] = {"class_type": "PrimitiveString", "inputs": {"value": "a" * 20}}
        graph["6"] = {"class_type": "PrimitiveString", "inputs": {"value": "b" * 20}}
        for key, a, b in (("7", "5", "6"), ("8", "6", "5")):
            graph[key] = {"class_type": "StringConcatenate", "inputs": {
                "string_a": [a, 0], "string_b": [b, 0], "delimiter": ""}}
        with patch.object(metadata, "MAX_RAW_BYTES", 64):
            result = metadata._comfy(graph)
        self.assertEqual(result["prompt"], "a" * 20 + "b" * 20)
        self.assertTrue(result["parameters"].get("prompt_extraction_incomplete"))


if __name__ == "__main__":
    unittest.main()
