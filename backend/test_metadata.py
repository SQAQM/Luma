"""Meaningful parser regressions. These are fixtures, not scale benchmarks."""

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin, features

try:
    from .metadata import MAX_TEXT_BYTES, classify_model, extract_metadata, parse_tags, reclassify_indexed
except ImportError:
    from metadata import MAX_TEXT_BYTES, classify_model, extract_metadata, parse_tags, reclassify_indexed


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def png(self, name="image.png", **metadata):
        path = self.root / name
        info = PngImagePlugin.PngInfo()
        for key, value in metadata.items():
            info.add_itxt(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        Image.new("RGB", (32, 24), "navy").save(path, pnginfo=info)
        return path

    def comfy(self, checkpoint="sdxl_base_1.0.safetensors"):
        return {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, 蓝色头发, (smile:1.2)", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad anatomy, blurry", "clip": ["1", 1]}},
            "4": {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": "anima-flux-lora.safetensors"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["2", 0], "negative": ["3", 0], "seed": 42, "steps": 20}},
        }

    def test_a1111_unicode_prompt_and_parameters(self):
        path = self.png(parameters="1girl, 蓝色头发, (smile:1.2), <lora:anima:0.8>\nNegative prompt: bad hands, blurry\nSteps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 42, Model: sdXL_base_1.0, Size: 32x24")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["model"], "sdXL_base_1.0")
        self.assertEqual(result["tags"], ["1girl", "蓝色头发", "smile"])
        self.assertEqual(result["negative_prompt"], "bad hands, blurry")
        self.assertEqual(result["parameters"]["Seed"], "42")
        self.assertEqual((result["width"], result["height"], result["format"]), (32, 24, "PNG"))

    def test_unknown_finetune_never_infers_from_tags_or_lora(self):
        path = self.png(parameters="anima, masterpiece, anime, <lora:SDXL:1>\nSteps: 20, Seed: 1, Model: illustriousXL_v10")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["confidence"], 0)
        self.assertIn("普通 TAG", result["reason"])

    def test_explicit_names_and_boundaries(self):
        for name, family in (("anima-v2.safetensors", "Anima"), ("krea2.safetensors", "Krea 2"), ("krea-2-dev", "Krea 2"), ("SD1.5", "Stable Diffusion"), ("sd15-base", "Stable Diffusion"), ("stable-diffusion-v1-5", "Stable Diffusion"), ("flux1-dev", "FLUX"), ("nai-diffusion-4-full", "NovelAI")):
            with self.subTest(name=name):
                self.assertEqual(classify_model(name)["family"], family)
        for name in ("animagineXL", "animal_portraits", "illustriousXL", "anything-v5", "foosdbar", "krea1", "abc_fluxlora"):
            with self.subTest(name=name):
                self.assertEqual(classify_model(name)["family"], "未知")

    def test_explicit_base_metadata_and_conflict(self):
        result = classify_model("opaque_finetune.safetensors", "anima", {"base_model": "SDXL"})
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(classify_model("anima", metadata={"base_family": "FLUX"})["family"], "未知")
        self.assertEqual(classify_model("<lora:anima:0.5>")["family"], "未知")
        self.assertEqual(classify_model("", "Anima SDXL Krea2")["family"], "未知")

    def test_comfy_sampler_graph_and_lora_exclusion(self):
        result = extract_metadata(self.png(prompt=self.comfy()))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["model"], "sdxl_base_1.0.safetensors")
        self.assertEqual(result["negative_prompt"], "bad anatomy, blurry")
        self.assertEqual(result["tags"], ["1girl", "蓝色头发", "smile"])
        self.assertEqual(result["parameters"]["steps"], 20)

    def test_unused_comfy_checkpoint_does_not_conflict(self):
        graph = self.comfy()
        graph["99"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux1-dev.safetensors"}}
        self.assertEqual(extract_metadata(self.png(prompt=graph))["family"], "Stable Diffusion")

    def test_multiple_used_checkpoints_conflict(self):
        graph = self.comfy()
        graph["99"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}}
        graph["100"] = {"class_type": "KSampler", "inputs": {"model": ["99", 0], "positive": ["2", 0], "negative": ["3", 0]}}
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual(result["family"], "未知")
        self.assertEqual(len(result["parameters"]["model_candidates"]), 2)
        graph["99"]["inputs"]["unet_name"] = "unknown_finetune"
        self.assertEqual(extract_metadata(self.png("unknown.png", prompt=graph))["family"], "未知")

    def test_comfy_workflow_serialization(self):
        workflow = {"nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["anima-v1.safetensors"]},
            {"id": 2, "type": "CLIPTextEncode", "widgets_values": ["girl, 花"], "inputs": []},
            {"id": 3, "type": "KSampler", "inputs": [{"name": "model", "link": 1}, {"name": "positive", "link": 2}]},
        ], "links": [[1, 1, 0, 3, 0, "MODEL"], [2, 2, 0, 3, 1, "CONDITIONING"]]}
        result = extract_metadata(self.png(workflow=workflow))
        self.assertEqual(result["family"], "Anima")
        self.assertEqual(result["tags"], ["girl", "花"])

    def test_novelai_comment_and_description(self):
        path = self.png(Software="NovelAI", Source="nai-diffusion-4-full", Description="1girl, pink hair", Comment={"uc": "blurry", "steps": 28, "seed": 123})
        result = extract_metadata(path)
        self.assertEqual(result["family"], "NovelAI")
        self.assertEqual(result["tags"], ["1girl", "pink hair"])
        self.assertEqual(result["negative_prompt"], "blurry")
        self.assertEqual(result["parameters"]["seed"], 123)

    def test_jpeg_ascii_exif_and_artist(self):
        path = self.root / "ascii.jpg"
        exif = Image.Exif()
        exif[37510] = b"ASCII\x00\x00\x00girl, clouds\nSteps: 24, Model: krea2, Seed: 42"
        exif[315] = "Example Artist"
        Image.new("RGB", (32, 24)).save(path, exif=exif)
        result = extract_metadata(path)
        self.assertEqual(result["family"], "Krea 2")
        self.assertEqual(result["artist"], "Example Artist")

    def test_jpeg_utf16_exif_unicode(self):
        for encoding in ("utf-16", "utf-16-be", "utf-16-le"):
            with self.subTest(encoding=encoding):
                path = self.root / (encoding + ".jpg")
                exif = Image.Exif()
                exif[37510] = b"UNICODE\x00" + "花, 少女\nSteps: 20, Model: anima-v1, Seed: 1".encode(encoding)
                Image.new("RGB", (32, 24)).save(path, exif=exif)
                result = extract_metadata(path)
                self.assertEqual(result["family"], "Anima")
                self.assertEqual(result["tags"], ["花", "少女"])

    def test_description_a1111(self):
        path = self.root / "description.jpg"
        exif = Image.Exif()
        exif[270] = "cat\nSteps: 20, Model: SD15, Seed: 42"
        Image.new("RGB", (32, 24)).save(path, exif=exif)
        self.assertEqual(extract_metadata(path)["family"], "Stable Diffusion")

    @unittest.skipUnless(features.check("webp"), "WebP codec unavailable")
    def test_webp_exif_and_dimensions(self):
        path = self.root / "image.webp"
        exif = Image.Exif()
        exif[37510] = b"ASCII\x00\x00\x00landscape\nSteps: 20, Model: flux1-dev, Seed: 1"
        Image.new("RGB", (32, 24)).save(path, exif=exif, lossless=True)
        result = extract_metadata(path)
        self.assertEqual(result["family"], "FLUX")
        self.assertEqual((result["width"], result["height"]), (32, 24))

    def test_json_sidecar_artist_tags_and_base_family(self):
        path = self.png("12345678_p2.png")
        path.with_suffix(".txt").write_text(json.dumps({"model": "custom_v9", "base_family": "SDXL", "tags": ["少女", "portrait"], "artist": "真实画师"}, ensure_ascii=False), encoding="utf-8")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["artist"], "真实画师")
        self.assertEqual(result["tags"], ["少女", "portrait"])
        self.assertEqual(result["parameters"]["pixiv_id"], "12345678")
        self.assertEqual(result["parameters"]["pixiv_page"], 2)

    def test_actual_json_sidecars_stem_and_full_filename(self):
        for full_name in (False, True):
            with self.subTest(full_filename=full_name):
                path = self.png(f"actual-{int(full_name)}.png")
                sidecar = Path(str(path) + ".json") if full_name else path.with_suffix(".json")
                sidecar.write_text(json.dumps({"model": "unknown_finetune", "base_model": "Anima", "prompt": "少女, 花", "artist": "实际画师"}, ensure_ascii=False), encoding="utf-8")
                result = extract_metadata(path)
                self.assertEqual(result["family"], "Anima")
                self.assertEqual(result["artist"], "实际画师")
                self.assertEqual(result["tags"], ["少女", "花"])
                self.assertEqual(result["raw_metadata"]["sidecar_name"], sidecar.name)

    def test_json_complements_txt_and_conflict_stays_unknown(self):
        path = self.png()
        path.with_suffix(".txt").write_text("girl\nSteps: 20, Model: anima, Seed: 1", encoding="utf-8")
        path.with_suffix(".json").write_text('{"artist":"Actual author","tags":["portrait"]}', encoding="utf-8")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "Anima")
        self.assertEqual(result["artist"], "Actual author")
        self.assertEqual(result["prompt"], "girl")
        path.with_suffix(".json").write_text('{"model":"flux1-dev","artist":"Actual author"}', encoding="utf-8")
        self.assertEqual(extract_metadata(path)["family"], "未知")

    def test_invalid_oversized_json_never_becomes_prompt(self):
        path = self.png()
        for content in ('["anima"]', '{"model":"anima", broken', '{"model":"anima","cfg":NaN}', "anima " * MAX_TEXT_BYTES):
            with self.subTest(content_type=content[:24]):
                path.with_suffix(".json").write_text(content, encoding="utf-8")
                result = extract_metadata(path)
                self.assertEqual(result["prompt"], "")
                self.assertEqual(result["family"], "未知")
                self.assertIn("sidecar_warning", result["raw_metadata"])

    def test_pixiv_filename_does_not_invent_artist(self):
        result = extract_metadata(self.png("987654321_p0.png"))
        self.assertEqual(result["artist"], "")
        self.assertEqual(result["family"], "未知")
        self.assertIn("不能据此", result["reason"])

    def test_sidecar_a1111_utf16_and_plain_tags(self):
        path = self.png()
        path.with_suffix(".txt").write_text("girl, 花\nNegative prompt: blurry\nSteps: 20, Model: anima-v2, Seed: 1", encoding="utf-16")
        self.assertEqual(extract_metadata(path)["family"], "Anima")
        path.with_suffix(".txt").write_text("girl, 花, anima", encoding="utf-8")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["tags"], ["girl", "花", "anima"])

    def test_non_ai_requires_explicit_metadata(self):
        path = self.png()
        path.with_suffix(".txt").write_text('{"ai": false, "artist": "Photographer"}', encoding="utf-8")
        self.assertEqual(extract_metadata(path)["family"], "非 AI")
        self.assertEqual(classify_model("SDXL", metadata={"ai": False})["family"], "未知")

    def test_symlink_sidecar_is_not_read(self):
        path = self.png()
        target = self.root / "external.txt"
        target.write_text('{"model":"anima","artist":"Should not read"}', encoding="utf-8")
        try:
            path.with_suffix(".txt").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host")
        result = extract_metadata(path)
        self.assertEqual(result["model"], "")
        self.assertEqual(result["artist"], "")
        self.assertIn("符号链接", result["raw_metadata"]["sidecar_warning"])

    def test_json_symlink_sidecar_is_not_read(self):
        path = self.png()
        target = self.root / "external.json"
        target.write_text('{"model":"anima","artist":"Should not read"}', encoding="utf-8")
        try:
            Path(str(path) + ".json").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host")
        result = extract_metadata(path)
        self.assertEqual(result["model"], "")
        self.assertEqual(result["artist"], "")
        self.assertIn("JSON", result["raw_metadata"]["sidecar_warning"])

    def test_tags_nested_weighted_escaped_and_lora(self):
        tags = parse_tags(r"(red hair:1.2), ((blue sky)), tag\,one, [soft light], <lora:anima,SDXL:0.8>, girl \(portrait\), red hair")
        self.assertEqual(tags, ["red hair", "blue sky", "tag,one", "soft light", "girl (portrait)"])

    def test_no_pixel_decode(self):
        png = self.png(parameters="cat\nSteps: 20, Model: anima, Seed: 1")
        other = []
        for fmt, suffix in (("JPEG", "jpg"), ("BMP", "bmp"), ("GIF", "gif"), ("TIFF", "tiff"), ("ICO", "ico"), ("WEBP", "webp"), ("AVIF", "avif")):
            path = self.root / ("image." + suffix)
            try:
                Image.new("RGB", (32, 24)).save(path, format=fmt)
            except (KeyError, OSError):
                if fmt in ("AVIF", "WEBP"):
                    continue
                raise
            other.append(path)
        with patch.object(Image.Image, "load", side_effect=AssertionError("must not decode pixels")):
            self.assertEqual(extract_metadata(png)["family"], "Anima")
            for path in other:
                self.assertGreater(extract_metadata(path)["width"], 0)

    def test_png_text_after_pixels(self):
        path = self.png(parameters="cat\nSteps: 20, Model: anima, Seed: 1")
        data = path.read_bytes()
        chunks = []
        offset = 8
        while offset < len(data):
            size = int.from_bytes(data[offset:offset + 4], "big") + 12
            chunks.append(data[offset:offset + size])
            offset += size
        text = [chunk for chunk in chunks if chunk[4:8] == b"iTXt"]
        ordered = [chunk for chunk in chunks if chunk[4:8] not in (b"iTXt", b"IEND")] + text + [chunks[-1]]
        path.write_bytes(data[:8] + b"".join(ordered))
        self.assertEqual(extract_metadata(path)["family"], "Anima")

    def test_bounds_oversized_sidecar_and_compressed_text(self):
        path = self.png()
        path.with_suffix(".txt").write_bytes(b"x" * (MAX_TEXT_BYTES + 1))
        result = extract_metadata(path)
        self.assertEqual(result["family"], "未知")
        self.assertIn("sidecar_warning", result["raw_metadata"])
        path.with_suffix(".txt").unlink()
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "x" * (MAX_TEXT_BYTES + 1), zip=True)
        Image.new("RGB", (32, 24)).save(path, pnginfo=info)
        self.assertNotIn("parameters", extract_metadata(path)["raw_metadata"])

    def test_huge_dimensions_do_not_decode(self):
        path = self.png()
        data = bytearray(path.read_bytes())
        data[16:24] = struct.pack(">II", 100000, 100000)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
        path.write_bytes(data)
        self.assertEqual(extract_metadata(path)["width"], 100000)

    def test_corrupt_and_unsupported_raise(self):
        path = self.root / "broken.png"
        path.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            extract_metadata(path)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 12)
        with self.assertRaises(ValueError):
            extract_metadata(path)

    def test_other_available_header_formats(self):
        for fmt, suffix in (("GIF", "gif"), ("BMP", "bmp"), ("TIFF", "tiff"), ("ICO", "ico"), ("AVIF", "avif")):
            with self.subTest(format=fmt):
                path = self.root / ("image." + suffix)
                try:
                    Image.new("RGB", (32, 24)).save(path, format=fmt)
                except (KeyError, OSError):
                    if fmt == "AVIF":
                        continue
                    raise
                result = extract_metadata(path)
                self.assertEqual(result["format"], fmt)
                self.assertGreater(result["width"], 0)

    def test_malformed_json_is_not_model_evidence(self):
        result = extract_metadata(self.png(prompt='{"model": "anima", broken'))
        self.assertEqual(result["family"], "未知")

    def test_malformed_workflow_types_do_not_crash(self):
        workflow = {"nodes": [{"id": 1, "type": "CLIPTextEncode", "inputs": {}, "widgets_values": {}}], "links": {}}
        result = extract_metadata(self.png(workflow=workflow))
        self.assertEqual(result["family"], "未知")

    def test_non_finite_json_is_not_accepted(self):
        path = self.png()
        path.with_suffix(".txt").write_text('{"model": "anima", "cfg": NaN}', encoding="utf-8")
        result = extract_metadata(path)
        self.assertEqual(result["family"], "未知")
        json.dumps(result, allow_nan=False)

    def test_plain_prompt_dedicated_fields(self):
        result = extract_metadata(self.png(prompt="girl, sky", negative_prompt="blurry", Model="anima"))
        self.assertEqual(result["family"], "Anima")
        self.assertEqual(result["tags"], ["girl", "sky"])
        self.assertEqual(result["negative_prompt"], "blurry")

    def test_png_metadata_crc_damage_raises(self):
        path = self.png(parameters="cat\nSteps: 20, Model: anima, Seed: 1")
        data = bytearray(path.read_bytes())
        position = data.find(b"anima")
        data[position] = ord("X")
        path.write_bytes(data)
        with self.assertRaises(ValueError):
            extract_metadata(path)

    def test_a1111_reordered_settings_aliases_and_quoted_model(self):
        result = extract_metadata(self.png(parameters='花, forest\nnegative PROMPT : blurry\nbad hands\nSampler: Euler, Seed: 71, Checkpoint: "opaque, tune", Base model: SDXL, CFG scale: 6'))
        self.assertEqual(result["model"], "opaque, tune")
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["parameters"]["model_variant"], "SDXL")
        self.assertEqual(result["negative_prompt"], "blurry\nbad hands")
        self.assertEqual(result["parameters"]["Seed"], "71")
        self.assertTrue(any(item["kind"] == "base" for item in result["parameters"]["model_evidence"]))

    def test_a1111_uppercase_and_multiline_tail(self):
        result = extract_metadata(self.png(parameters="portrait\nSTEPS: 25, SEED: 17, SD Model Checkpoint: stable-diffusion-v2-1\nModel hash: 123abc, Size: 32x24"))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["parameters"]["model_variant"], "SD2.1")
        self.assertEqual(result["parameters"]["model_hash"], "123abc")
        self.assertEqual(result["prompt"], "portrait")

    def test_settings_like_caption_does_not_establish_model(self):
        result = extract_metadata(self.png(description="Steps: through a forest, Model: anima, beautiful"))
        self.assertEqual(result["model"], "")
        self.assertEqual(result["family"], "未知")

    def test_explicit_sd_variants_and_architecture_classes(self):
        for value, variant in (
            ("SD1", "SD1"), ("SD-1.5", "SD1.5"), ("stable-diffusion-v1-4", "SD1.4"),
            ("SD2", "SD2"), ("sd2.1", "SD2.1"), ("SDXL", "SDXL"),
            ("sd3", "SD3"), ("stable-diffusion-3.5-large", "SD3.5"),
            ("StableDiffusionXLImg2ImgPipeline", "SDXL"),
            ("SD3Transformer2DModel", "SD3"),
        ):
            with self.subTest(architecture=value):
                result = classify_model("opaque-custom.safetensors", metadata={"model_architecture": value})
                self.assertEqual(result["family"], "Stable Diffusion")
                self.assertEqual(result["model_variant"], variant)
                self.assertEqual(result["reason_code"], "explicit")
                self.assertIn(variant, result["reason"])
        self.assertEqual(classify_model("opaque", metadata={"architecture": "FluxTransformer2DModel"})["family"], "FLUX")

    def test_nested_generation_json_and_model_object(self):
        generation = {"metadata": {"generationData": json.dumps({
            "model": {"name": "custom-微调.safetensors", "baseModel": "SDXL", "hash": "a1b2"},
            "positivePrompt": "少女, 月光", "negativePrompt": "blur", "seed": 8,
        }, ensure_ascii=False)}}
        result = extract_metadata(self.png(metadata=generation))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["model"], "custom-微调.safetensors")
        self.assertEqual(result["prompt"], "少女, 月光")
        self.assertEqual(result["negative_prompt"], "blur")
        self.assertEqual(result["parameters"]["model_hash"], "a1b2")
        self.assertTrue(any(".generation_data." in item["source"] for item in result["parameters"]["model_evidence"]))

    def test_nested_json_sidecar_same_parser(self):
        path = self.png()
        path.with_suffix(".json").write_text(json.dumps({"generation": {"settings": {"checkpointName": "private-tune", "baseFamily": "Anima", "positivePrompt": "flower"}}}), encoding="utf-8")
        result = extract_metadata(path)
        self.assertEqual((result["model"], result["family"]), ("private-tune", "Anima"))
        self.assertEqual(result["tags"], ["flower"])

    def test_nested_base_conflict_preserves_both_provenance_sources(self):
        result = extract_metadata(self.png(metadata={"model": "opaque", "baseModel": "SDXL", "generation": {"baseModel": "FLUX"}}))
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["reason_code"], "conflict")
        evidence = result["parameters"]["model_evidence"]
        self.assertEqual({item["value"] for item in evidence if item["kind"] == "base"}, {"SDXL", "FLUX"})

    def test_sd_major_variant_conflict_not_collapsed_to_family(self):
        result = classify_model("sdxl-base", metadata={"base_model": "SD1.5"})
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["reason_code"], "conflict")
        self.assertEqual(classify_model("sd3.5-large", metadata={"architecture": "SD3Transformer2DModel"})["model_variant"], "SD3.5")

    def test_custom_comfy_named_loader_pipeline_sampler(self):
        graph = {
            "1": {"class_type": "VendorCheckpointLoader", "inputs": {"checkpoint_name": "private-tune", "base_model": "SDXL"}},
            "2": {"class_type": "VendorBasicPipe", "inputs": {"model": ["1", 0], "positive": "flower, 花", "negative": "noise"}},
            "3": {"class_type": "VendorSampler", "inputs": {"pipe": ["2", 0], "steps": 12, "seed": 19}},
            "99": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev"}},
        }
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual((result["family"], result["model"]), ("Stable Diffusion", "private-tune"))
        self.assertEqual(result["prompt"], "flower, 花")
        self.assertEqual(result["negative_prompt"], "noise")
        self.assertEqual(result["parameters"]["steps"], 12)
        self.assertEqual(result["parameters"]["model_candidates"], ["private-tune"])

    def test_comfy_advanced_guider_noise_and_scheduler(self):
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}},
            "2": {"class_type": "CLIPTextEncodeFlux", "inputs": {"clip_l": "花", "t5xxl": "flowers in sunlight"}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blur"}},
            "4": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "cfg": 3.5}},
            "5": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}},
            "6": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "7": {"class_type": "BasicScheduler", "inputs": {"model": ["1", 0], "scheduler": "simple", "steps": 16, "denoise": 1}},
            "8": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["5", 0], "guider": ["4", 0], "sampler": ["6", 0], "sigmas": ["7", 0]}},
        }
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual(result["family"], "FLUX")
        self.assertEqual(result["prompt"], "花, flowers in sunlight")
        self.assertEqual(result["negative_prompt"], "blur")
        self.assertEqual(result["parameters"]["noise_seed"], 42)
        self.assertEqual(result["parameters"]["steps"], 16)
        self.assertEqual(result["parameters"]["cfg"], 3.5)

    def test_named_custom_workflow_widgets_but_opaque_order_unknown(self):
        workflow = {"nodes": [
            {"id": 1, "type": "VendorLoader", "widgets_values": {"model_name": "private", "base_model": "Anima"}},
            {"id": 2, "type": "VendorSampler", "inputs": [{"name": "model", "link": 1}]},
        ], "links": [[1, 1, 0, 2, 0, "MODEL"]]}
        self.assertEqual(extract_metadata(self.png(workflow=workflow))["family"], "Anima")
        workflow["nodes"][0]["widgets_values"] = ["anima.safetensors", "SDXL"]
        result = extract_metadata(self.png("opaque.png", workflow=workflow))
        self.assertEqual((result["model"], result["family"]), ("", "未知"))
        self.assertEqual(result["reason_code"], "missing")

    def test_legacy_checkpoint_workflow_reads_ckpt_after_config(self):
        workflow = {"nodes": [{"id": 1, "type": "CheckpointLoader", "widgets_values": ["SDXL-config.yaml", "anima.safetensors"]}]}
        result = extract_metadata(self.png(workflow=workflow))
        self.assertEqual((result["model"], result["family"]), ("anima.safetensors", "Anima"))

    def test_auxiliary_comfy_fields_and_unused_models_are_not_ancestry(self):
        graph = self.comfy("private-finetune")
        graph["7"] = {"class_type": "ControlNetLoader", "inputs": {"checkpoint_name": "flux1-dev"}}
        graph["8"] = {"class_type": "DualCLIPLoader", "inputs": {"model_name": "anima"}}
        graph["9"] = {"class_type": "VAELoader", "inputs": {"checkpoint_name": "SDXL"}}
        graph["2"]["class_type"] = "CLIPTextEncodeSDXL"
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual((result["model"], result["family"]), ("private-finetune", "未知"))
        self.assertEqual(result["reason_code"], "unregistered")
        self.assertEqual({item["value"] for item in result["parameters"]["model_evidence"]}, {"private-finetune"})

    def test_comfy_model_merge_and_cycle_are_conservative(self):
        graph = self.comfy()
        graph["10"] = {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev"}}
        graph["11"] = {"class_type": "ModelMergeSimple", "inputs": {"model1": ["1", 0], "model2": ["10", 0]}}
        graph["5"]["inputs"]["model"] = ["11", 0]
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["reason_code"], "conflict")
        self.assertEqual(len(result["parameters"]["model_candidates"]), 2)
        graph["11"]["inputs"] = {"model": ["11", 0]}
        self.assertEqual(extract_metadata(self.png("cycle.png", prompt=graph))["family"], "未知")

    def test_prompt_json_lora_resources_and_styles_not_model_provenance(self):
        cases = (
            {"prompt": {"model": "anima"}},
            {"metadata": {"prompt": {"model": "anima"}, "resources": [{"type": "checkpoint", "name": "SDXL"}]}},
            {"metadata": {"model": {"type": "LoRA", "name": "anima", "baseModel": "SDXL"}}},
            {"metadata": {"type": "LoRA", "model": "SDXL", "baseModel": "SDXL"}},
            {"metadata": {"styles": {"model": "anima"}, "tags": ["SDXL", "FLUX"]}},
        )
        for index, fields in enumerate(cases):
            with self.subTest(case=index):
                result = extract_metadata(self.png(f"false-{index}.png", **fields))
                self.assertEqual((result["model"], result["family"]), ("", "未知"))

    def test_reason_codes_and_non_ai_conflicts(self):
        self.assertEqual(classify_model("private")["reason_code"], "unregistered")
        self.assertEqual(classify_model("", metadata={"ai_evidence": True})["reason_code"], "missing")
        self.assertEqual(classify_model("", metadata={"ai": False})["reason_code"], "non_ai")
        self.assertEqual(classify_model("anima", metadata={"ai": False})["reason_code"], "conflict")
        result = extract_metadata(self.png(metadata={"ai": False, "generation": {"isAI": True}}))
        self.assertEqual(result["reason_code"], "conflict")

    def test_reclassify_saved_raw_without_any_image_io(self):
        cached = {"path": r"Z:\unmounted\private.png", "family": "未知", "model": "",
                  "prompt": "keep original prompt", "tags": ["keep-tag"], "parameters": {},
                  "raw_metadata": {"metadata": json.dumps({"generation": {"checkpointName": "new-custom", "baseModel": "SD3.5"}})}}
        original = json.loads(json.dumps(cached))
        with patch("builtins.open", side_effect=AssertionError("no files")), \
             patch.object(Path, "open", side_effect=AssertionError("no paths")), \
             patch.object(Image, "open", side_effect=AssertionError("no pixels")):
            result = reclassify_indexed(cached)
        self.assertEqual((result["model"], result["family"]), ("new-custom", "Stable Diffusion"))
        self.assertEqual(result["parameters"]["model_variant"], "SD3.5")
        self.assertEqual(result["reason_code"], "explicit")
        self.assertEqual(cached, original)
        self.assertNotIn("prompt", result)
        self.assertNotIn("tags", result)

    def test_reclassify_old_comfy_json_and_saved_sidecar(self):
        graph = {"1": {"class_type": "VendorLoader", "inputs": {"checkpointName": "custom", "modelArchitecture": "SD3Transformer2DModel"}},
                 "2": {"class_type": "VendorSampler", "inputs": {"model": ["1", 0]}}}
        result = reclassify_indexed({"model": "", "raw_metadata": {"prompt": json.dumps(graph)}})
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["parameters"]["model_variant"], "SD3")
        result = reclassify_indexed({"model": "", "raw_metadata": {"sidecar_text": '{"generation":{"model":"anima"}}'}})
        self.assertEqual(result["family"], "Anima")

    def test_reclassify_preserves_conflict_non_ai_and_unknown_names(self):
        conflict = reclassify_indexed({"model": "SDXL", "parameters": {"base_model": "FLUX"}, "raw_metadata": {}})
        self.assertEqual(conflict["reason_code"], "conflict")
        multiple = reclassify_indexed({"model": "SDXL | private", "parameters": {"model_candidates": ["SDXL", "private"]}})
        self.assertEqual(multiple["reason_code"], "conflict")
        self.assertEqual(multiple["parameters"]["model_candidates"], ["SDXL", "private"])
        non_ai = reclassify_indexed({"parameters": {"ai": False}})
        self.assertEqual(non_ai["reason_code"], "non_ai")
        unknown = reclassify_indexed({"model": "illustriousXL", "parameters": {"model_variant": "SDXL"}, "tags": ["anima"]})
        self.assertEqual(unknown["reason_code"], "unregistered")
        self.assertNotIn("model_variant", unknown["parameters"])

    def test_malformed_cached_values_and_excessive_nesting_stay_bounded(self):
        deep = {"model": "anima"}
        for _ in range(30):
            deep = {"metadata": deep}
        result = reclassify_indexed({"raw_metadata": {"metadata": json.dumps(deep)}, "parameters": {
            "model_evidence": [{"value": [], "source": [], "kind": "base"}, {"value": "opaque", "source": [], "kind": "checkpoint"}]}})
        self.assertEqual(result["family"], "未知")
        json.dumps(result, allow_nan=False)

    def test_checkpoint_hash_provenance_retained_without_guessing_family(self):
        digest = "1234567890abcdef" * 4
        result = extract_metadata(self.png(metadata={"generation": {"model": {"name": "opaque", "sha256": digest}, "hashes": {"model": digest, "lora": "abcdef1234"}}}))
        self.assertEqual(result["family"], "未知")
        self.assertEqual(result["parameters"]["model_sha256"], digest)
        self.assertEqual(result["parameters"]["hashes"]["model"], digest)
        only_hash = extract_metadata(self.png("hash.png", metadata={"modelHash": digest}))
        self.assertEqual(only_hash["parameters"]["model_hash"], digest)
        self.assertEqual(only_hash["family"], "未知")
        self.assertEqual(reclassify_indexed({"raw_metadata": {"metadata": json.dumps({"checkpointHash": digest})}})["parameters"]["checkpoint_hash"], digest)
        graph = self.comfy("opaque")
        graph["1"]["inputs"]["checkpoint_sha256"] = digest
        self.assertEqual(extract_metadata(self.png("comfy-hash.png", prompt=graph))["parameters"]["checkpoint_sha256"], digest)

    def test_old_cached_auxiliary_resource_cannot_be_promoted_to_checkpoint(self):
        result = reclassify_indexed({"model": "SDXL", "parameters": {"type": "LoRA", "base_model": "SDXL"}})
        self.assertEqual((result["family"], result["reason_code"]), ("未知", "conflict"))
        self.assertEqual(classify_model("anima", metadata={"resource_type": "ControlNet"})["family"], "未知")

    def test_explicit_model_type_auxiliary_json_and_cache_are_not_checkpoint(self):
        for index, resource_type in enumerate(("LoRA", "ControlNet", "VAE", "upscaler")):
            with self.subTest(resource_type=resource_type):
                params = {"type": "checkpoint", "model_type": resource_type, "base_model": "SDXL", "model_hash": "abcdef0123456789"}
                result = reclassify_indexed({"model": "opaque_adapter", "parameters": params})
                self.assertEqual((result["family"], result["reason_code"]), ("未知", "conflict"))
                top = extract_metadata(self.png(f"aux-top-{index}.png", metadata={**params, "model": "anima"}))
                self.assertEqual((top["model"], top["family"]), ("", "未知"))
                self.assertNotIn("model_hash", top["parameters"])
                nested = extract_metadata(self.png(f"aux-object-{index}.png", metadata={"model": {**params, "name": "anima"}}))
                self.assertEqual((nested["model"], nested["family"]), ("", "未知"))
                self.assertNotIn("model_candidates", nested["parameters"])

    def test_krea_requires_explicit_second_version_and_does_not_conflict_with_flux(self):
        for name in ("krea2.safetensors", "Krea-2-dev", "krea_2", "Krea 2"):
            with self.subTest(name=name):
                self.assertEqual(classify_model(name)["family"], "Krea 2")
        self.assertEqual(classify_model("krea")["family"], "未知")
        self.assertEqual(classify_model("krea1")["family"], "未知")
        result = classify_model("flux1-krea-dev.safetensors")
        self.assertEqual((result["family"], result["reason_code"]), ("FLUX", "explicit"))
        cached = reclassify_indexed({"model": "flux1-krea-dev.safetensors", "parameters": {"model_reason_code": "conflict"}})
        self.assertEqual((cached["family"], cached["reason_code"]), ("FLUX", "explicit"))

    def test_fooocus_checkpoint_name_and_sha256_are_not_base_family(self):
        digest = "a1" * 32
        path = self.png(fooocus_scheme="fooocus", parameters={
            "base_model": "private-finetune", "base_model_hash": digest,
            "prompt": "anima, forest", "negative_prompt": "blur", "steps": 30,
            "loras": [["SDXL-LoRA", 0.8, "b2" * 32]], "vae": "flux-vae",
        })
        result = extract_metadata(path)
        self.assertEqual((result["model"], result["family"]), ("private-finetune", "未知"))
        self.assertEqual(result["parameters"]["checkpoint_sha256"], digest)
        self.assertEqual(result["parameters"]["checkpoint_hashes"], [digest])
        self.assertNotIn("base_model", result["parameters"])
        self.assertEqual(result["parameters"]["metadata_source"], "Fooocus")
        self.assertTrue(all(item["kind"] == "checkpoint" for item in result["parameters"]["model_evidence"]))

    def test_fooocus_marked_exif_and_unmarked_official_json_shape(self):
        params = {"base_model": "anima-custom", "base_model_hash": "a2" * 32,
                  "full_prompt": ["flowers"], "full_negative_prompt": ["blur"], "prompt": "flowers"}
        path = self.root / "fooocus.jpg"
        exif = Image.Exif()
        exif[305] = "Fooocus v2.5.5"
        exif[37510] = json.dumps(params)
        Image.new("RGB", (32, 24)).save(path, exif=exif)
        self.assertEqual(extract_metadata(path)["family"], "Anima")
        path = self.png("unmarked.png", parameters=params)
        self.assertEqual(extract_metadata(path)["model"], "anima-custom")
        # Generic baseModel retains its original ancestry meaning.
        generic = extract_metadata(self.png("generic.png", metadata={"model": "opaque", "base_model": "SDXL"}))
        self.assertEqual((generic["model"], generic["family"]), ("opaque", "Stable Diffusion"))

    def test_fooocus_actual_refiner_preserves_both_checkpoints_and_hashes(self):
        result = extract_metadata(self.png(fooocus_scheme="fooocus", parameters={
            "base_model": "private-main", "base_model_hash": "a3" * 32,
            "refiner_model": "private-refiner", "refiner_model_hash": "b3" * 32,
            "prompt": "flowers", "steps": 30,
        }))
        self.assertEqual(result["parameters"]["model_candidates"], ["private-main", "private-refiner"])
        self.assertEqual(set(result["parameters"]["checkpoint_hashes"]), {"a3" * 32, "b3" * 32})
        self.assertEqual(result["conflict_kind"], "unresolved_checkpoints")
        conflict = extract_metadata(self.png("refiner-conflict.png", fooocus_scheme="fooocus", parameters={
            "base_model": "anima", "refiner_model": "flux1-dev", "steps": 30}))
        self.assertEqual(conflict["reason_code"], "conflict")
        self.assertNotIn("conflict_kind", conflict)

    def test_invokeai_main_base_and_blake3_have_correct_semantics(self):
        digest = "c4" * 32
        for base, family, variant in (("sd-1", "Stable Diffusion", "SD1"), ("sd-2", "Stable Diffusion", "SD2"),
                                      ("sdxl", "Stable Diffusion", "SDXL"), ("anima", "Anima", ""),
                                      ("krea-2", "Krea 2", ""), ("flux", "FLUX", "")):
            with self.subTest(base=base):
                result = extract_metadata(self.png(base + ".png", invokeai_metadata={
                    "model": {"key": "synthetic-key", "name": "private-model", "base": base, "type": "main", "hash": digest},
                    "positive_prompt": "forest", "negative_prompt": "blur", "seed": 42,
                    "vae": {"name": "anima", "base": "anima", "type": "vae", "hash": "d5" * 32},
                    "loras": [{"model": {"name": "flux1-dev", "base": "flux", "type": "lora"}}],
                }))
                self.assertEqual((result["model"], result["family"]), ("private-model", family))
                self.assertEqual(result["parameters"]["invokeai_model_hash"], digest)
                self.assertEqual(result["parameters"]["invokeai_hash_algorithm"], "unspecified")
                self.assertEqual(result["parameters"]["invokeai_hash_default_algorithm"], "blake3")
                self.assertNotIn("model_hash", result["parameters"])
                self.assertNotIn("checkpoint_hashes", result["parameters"])
                if variant:
                    self.assertEqual(result["parameters"]["model_variant"], variant)
                self.assertTrue(any(item["source"].endswith(".model.base") for item in result["parameters"]["model_evidence"]))

    def test_invokeai_auxiliary_types_and_untyped_base_are_not_ancestry(self):
        for index, kind in enumerate(("lora", "controlnet", "vae", "clip_embed", "embedding", "t5_encoder", "ip_adapter", "unknown")):
            result = extract_metadata(self.png(f"invoke-aux-{index}.png", invokeai_metadata={
                "model": {"name": "anima", "base": "sdxl", "type": kind, "hash": "d6" * 32}}))
            self.assertEqual((result["model"], result["family"]), ("", "未知"))
            self.assertNotIn("model_hash", result["parameters"])
        generic = extract_metadata(self.png("generic-base.png", metadata={"model": {"name": "private", "base": "SDXL"}}))
        self.assertEqual(generic["family"], "未知")

    def test_cached_invokeai_hash_is_repaired_without_image_io(self):
        digest = "e7" * 32
        cached = {"model": "private-model", "parameters": {"model_hash": digest, "hashes": {"model": digest}},
                  "raw_metadata": {"invokeai_metadata": json.dumps({"model": {
                      "key": "test", "name": "private-model", "base": "unknown", "type": "main", "hash": digest}})}}
        original = json.loads(json.dumps(cached))
        with patch("builtins.open", side_effect=AssertionError("no files")), \
             patch.object(Path, "open", side_effect=AssertionError("no paths")), \
             patch.object(Image, "open", side_effect=AssertionError("no pixels")):
            result = reclassify_indexed(cached)
        self.assertEqual(result["family"], "未知")
        self.assertNotIn("model_hash", result["parameters"])
        self.assertNotIn("model", result["parameters"].get("hashes", {}))
        self.assertEqual(result["parameters"]["invokeai_model_hash"], digest)
        self.assertEqual(cached, original)

    def test_cached_fooocus_base_checkpoint_gets_correct_provenance_without_image_io(self):
        digest = "ee" * 32
        cached = {"model": "", "parameters": {"base_model": "private-tune", "base_model_hash": digest,
                   "model_evidence": [{"value": "private-tune", "kind": "base", "source": "元数据.parameters.base_model"}]},
                  "raw_metadata": {"fooocus_scheme": "fooocus", "parameters": json.dumps({
                      "base_model": "private-tune", "base_model_hash": digest, "prompt": "flowers", "steps": 30})}}
        original = json.loads(json.dumps(cached))
        with patch("builtins.open", side_effect=AssertionError("no files")), \
             patch.object(Path, "open", side_effect=AssertionError("no paths")), \
             patch.object(Image, "open", side_effect=AssertionError("no pixels")):
            result = reclassify_indexed(cached)
        self.assertEqual((result["model"], result["family"]), ("private-tune", "未知"))
        self.assertEqual(result["parameters"]["checkpoint_hashes"], [digest])
        self.assertNotIn("base_model", result["parameters"])
        self.assertTrue(all(item["kind"] == "checkpoint" for item in result["parameters"]["model_evidence"]))
        self.assertEqual(cached, original)

    def test_cached_invokeai_string_hashes_and_case_aliases_never_become_file_hashes(self):
        from backend.model_registry import model_hashes
        first, second, auxiliary = "ab" * 32, "bc" * 32, "cd" * 32
        raw = {"model": {"key": "main", "name": "private-main", "base": "unknown", "type": "main", "hash": first},
               "refiner_model": {"key": "refiner", "name": "private-refiner", "base": "unknown", "type": "main", "hash": second}}
        for field in ("hashes", "HashES"):
            for value in ({"MoDeL": first, "CheckPoint": second, "lora:private": auxiliary},
                          json.dumps({"MoDeL": first, "CheckPoint": second, "lora:private": auxiliary})):
                with self.subTest(field=field, encoded=isinstance(value, str)):
                    cached = {"model": "private-main", "parameters": {field: value, "CheckpointHashes": [first, second],
                              "ModelHash": first, "checkpointSHA256": second},
                              "raw_metadata": {"invokeai_metadata": json.dumps(raw)}}
                    original = json.loads(json.dumps(cached))
                    with patch("builtins.open", side_effect=AssertionError("no original files")), \
                         patch.object(Path, "open", side_effect=AssertionError("no original paths")), \
                         patch.object(Path, "stat", side_effect=AssertionError("no original stat")), \
                         patch.object(Image, "open", side_effect=AssertionError("no original pixels")):
                        result = reclassify_indexed(cached)
                    self.assertEqual(model_hashes(result["parameters"]), [])
                    self.assertEqual(result["parameters"][field], {"lora:private": auxiliary})
                    self.assertEqual({item["hash"] for item in result["parameters"]["invokeai_model_hashes"]}, {first, second})
                    self.assertEqual(result["parameters"]["invokeai_hash_algorithm"], "unspecified")
                    self.assertEqual(cached, original)

    def test_invokeai_refiner_keeps_checkpoint_ancestry_and_independent_hashes(self):
        raw = {"model": {"key": "main", "name": "private-main", "base": "sdxl", "type": "main", "hash": "11" * 32},
               "refiner_model": {"key": "refiner", "name": "private-refiner", "base": "sdxl-refiner", "type": "main", "hash": "22" * 32}}
        result = extract_metadata(self.png(invokeai_metadata=raw))
        self.assertEqual((result["family"], result["parameters"]["model_variant"]), ("Stable Diffusion", "SDXL"))
        self.assertEqual(result["parameters"]["model_candidates"], ["private-main", "private-refiner"])
        ancestry = {(item["checkpoint"], item["value"]) for item in result["parameters"]["checkpoint_ancestry"]}
        self.assertEqual(ancestry, {("private-main", "sdxl"), ("private-refiner", "sdxl-refiner")})
        hashes = result["parameters"]["invokeai_model_hashes"]
        self.assertEqual({(item["checkpoint"], item["hash"]) for item in hashes}, {("private-main", "11" * 32), ("private-refiner", "22" * 32)})
        self.assertTrue(all(item["algorithm"] == "unspecified" and item["default_algorithm"] == "blake3" for item in hashes))
        self.assertNotIn("checkpoint_hashes", result["parameters"])
        self.assertEqual(result["parameters"]["invokeai_model_hash"], "11" * 32)
        raw["refiner_model"]["base"] = "unknown"
        unresolved = extract_metadata(self.png("refiner-unknown.png", invokeai_metadata=raw))
        self.assertEqual(unresolved["conflict_kind"], "unresolved_checkpoints")
        raw["refiner_model"]["base"] = "flux"
        conflicting = extract_metadata(self.png("refiner-conflict.png", invokeai_metadata=raw))
        self.assertEqual((conflicting["family"], conflicting["reason_code"]), ("未知", "conflict"))
        self.assertNotIn("conflict_kind", conflicting)

    def test_invokeai_auxiliary_refiner_not_used_but_malformed_refiner_abstains(self):
        raw = {"model": {"key": "main", "name": "private-main", "base": "sdxl", "type": "main"},
               "refiner_model": {"key": "aux", "name": "anima", "base": "anima", "type": "lora", "hash": "33" * 32}}
        result = extract_metadata(self.png(invokeai_metadata=raw))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["parameters"]["model_candidates"], ["private-main"])
        self.assertNotIn("invokeai_model_hashes", result["parameters"])
        raw["refiner_model"]["type"] = "untyped"
        result = extract_metadata(self.png("malformed-refiner.png", invokeai_metadata=raw))
        self.assertEqual((result["family"], result["reason_code"]), ("未知", "conflict"))
        self.assertTrue(result["parameters"]["model_provenance_incomplete"])

    def test_checkpoint_ancestry_preserves_distinct_repository_namespaces(self):
        from backend.metadata import _checkpoint_identity
        first, second = "official/private-model", "someone/private-model"
        self.assertNotEqual(_checkpoint_identity(first), _checkpoint_identity(second))
        self.assertEqual(_checkpoint_identity(r"OFFICIAL\private-model.safetensors"), _checkpoint_identity(first))
        result = classify_model(first + " | " + second, metadata={
            "model_candidates": [first, second], "checkpoint_ancestry": [
                {"checkpoint": first, "value": "SDXL", "kind": "base", "source": "synthetic official model"}]})
        self.assertEqual((result["family"], result["conflict_kind"]), ("未知", "unresolved_checkpoints"))

    def test_civitai_curated_resources_preserve_checkpoint_base_and_ignore_adapters(self):
        summary = {"prompt": "flowers", "negativePrompt": "blur", "seed": 7, "resources": [
            {"type": "Checkpoint", "modelName": "private-model", "baseModel": "SDXL 1.0", "hash": "f8" * 32},
            {"type": "LORA", "modelName": "anima", "baseModel": "Anima", "hash": "a9" * 32},
            {"type": "VAE", "modelName": "flux1-dev", "baseModel": "FLUX"},
            {"modelName": "krea2", "baseModel": "Krea 2", "modelVersionId": 123},
        ]}
        result = extract_metadata(self.png(extraMetadata=json.dumps(summary)))
        self.assertEqual((result["model"], result["family"]), ("private-model", "Stable Diffusion"))
        self.assertEqual(result["parameters"]["model_variant"], "SDXL")
        self.assertEqual(result["parameters"]["checkpoint_hashes"], ["f8" * 32])
        self.assertEqual(result["prompt"], "flowers")
        self.assertEqual(result["negative_prompt"], "blur")
        self.assertEqual(result["parameters"]["model_candidates"], ["private-model"])

    def test_civitai_a1111_json_resource_block_keeps_nested_commas_and_sampler(self):
        resources = [{"air": "urn:air:sdxl:checkpoint:civitai:123@456", "modelName": "private, model"},
                     {"air": "urn:air:anima:lora:civitai:999@111", "type": "LORA", "modelName": "anima"}]
        text = "flowers\nSteps: 20, Seed: 7, Sampler: Euler, Civitai resources: " + json.dumps(resources)
        result = extract_metadata(self.png(parameters=text))
        self.assertEqual((result["model"], result["family"]), ("private, model", "Stable Diffusion"))
        self.assertEqual(result["parameters"]["Sampler"], "Euler")
        self.assertEqual(result["parameters"]["model_candidates"], ["private, model"])
        self.assertTrue(any(".air.base_model" in item["source"] for item in result["parameters"]["model_evidence"]))

    def test_civitai_multiple_explicit_checkpoint_ancestries_are_checked_individually(self):
        resources = [{"type": "Checkpoint", "modelName": "private-a", "baseModel": "SDXL"},
                     {"type": "Checkpoint", "modelName": "private-b", "baseModel": "Illustrious"}]
        result = extract_metadata(self.png(extraMetadata={"resources": resources}))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(len(result["parameters"]["checkpoint_ancestry"]), 2)
        resources[1].pop("baseModel")
        unresolved = extract_metadata(self.png("unresolved.png", extraMetadata={"resources": resources}))
        self.assertEqual(unresolved["conflict_kind"], "unresolved_checkpoints")
        resources[1]["baseModel"] = "Anima"
        conflict = extract_metadata(self.png("conflict.png", extraMetadata={"resources": resources}))
        self.assertEqual(conflict["reason_code"], "conflict")
        self.assertNotIn("conflict_kind", conflict)

    def test_civitai_air_wrong_type_or_missing_identity_never_establishes_family(self):
        for index, air in enumerate(("urn:air:sdxl:lora:civitai:1@2", "urn:air:anima:vae:civitai:1@2",
                                     "sdxl:checkpoint:civitai:1@2", "urn:air:sdxl:checkpoint:civitai:")):
            result = extract_metadata(self.png(f"air-invalid-{index}.png", extraMetadata={"resources": [{"air": air}]}))
            self.assertEqual((result["model"], result["family"]), ("", "未知"))
        result = extract_metadata(self.png("air-conflict.png", extraMetadata={"resources": [
            {"type": "Checkpoint", "air": "urn:air:sdxl:lora:civitai:1@2", "modelName": "anima", "baseModel": "Anima"}]}))
        self.assertEqual(result["reason_code"], "conflict")
        self.assertNotIn("conflict_kind", result)

    def test_comfy_civitai_selector_uses_checkpoint_loader_output_slot(self):
        graph = self.comfy("unused-name")
        graph["1"]["inputs"]["ckpt_name"] = ["10", 3]
        graph["10"] = {"class_type": "CivitaiModelSelector", "inputs": {
            "air": "urn:air:anima:lora:civitai:1@2", "resources_json": json.dumps({"bySlot": {
                "0": "urn:air:flux1:checkpoint:civitai:3@4", "3": "urn:air:illustrious:checkpoint:civitai:5@6"}})}}
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual(result["family"], "Stable Diffusion")
        self.assertEqual(result["parameters"]["model_variant"], "SDXL")
        self.assertEqual(result["model"], "urn:air:illustrious:checkpoint:civitai:5@6")
        self.assertTrue(any("slot3" in item["source"] for item in result["parameters"]["model_evidence"]))
        graph["1"]["inputs"]["ckpt_name"] = ["10", 99]
        result = extract_metadata(self.png("slot-invalid.png", prompt=graph))
        self.assertEqual((result["model"], result["family"]), ("", "未知"))

    def test_comfy_curated_extra_metadata_is_not_skipped_with_graph(self):
        graph = self.comfy("private")
        graph["extraMetadata"] = json.dumps({"resources": [{"type": "Checkpoint", "modelName": "private", "baseModel": "Anima"}]})
        result = extract_metadata(self.png(prompt=graph))
        self.assertEqual(result["family"], "Anima")
        cached = reclassify_indexed({"raw_metadata": {"prompt": json.dumps(graph)}})
        self.assertEqual(cached["family"], "Anima")

    def test_swarmui_semantic_model_and_tensor_hash_stay_separate(self):
        digest = "0x" + "bc" * 32
        data = {"sui_image_params": {"prompt": "flowers", "negativeprompt": "blur", "model": "folder/private-model", "steps": 20, "cfgscale": 7},
                "sui_models": [{"name": "folder/private-model.safetensors", "param": "model", "hash": digest},
                               {"name": "anima.safetensors", "param": "loras", "hash": "0x" + "cd" * 32}]}
        result = extract_metadata(self.png(parameters=data))
        self.assertEqual((result["model"], result["family"]), ("folder/private-model", "未知"))
        self.assertEqual(result["negative_prompt"], "blur")
        self.assertEqual(result["parameters"]["cfg_scale"], 7)
        self.assertEqual(result["parameters"]["swarm_model_tensor_hashes"], [{"name": "folder/private-model.safetensors", "hash": digest, "algorithm": "sha256-tensor"}])
        self.assertNotIn("checkpoint_hashes", result["parameters"])
        self.assertNotIn("model_hash", result["parameters"])
        data["sui_models"][0]["name"] = "flux1-dev.safetensors"
        mismatch = extract_metadata(self.png("swarm-mismatch.png", parameters=data))
        self.assertEqual(mismatch["conflict_kind"], "unresolved_checkpoints")
        self.assertEqual(len(mismatch["parameters"]["model_candidates"]), 2)

    def test_explicit_ecosystem_enums_never_guess_from_checkpoint_or_prompt(self):
        for base in ("Pony", "Illustrious", "NoobAI", "SDXLDistilled"):
            explicit = classify_model("private", metadata={"baseModel": base})
            self.assertEqual((explicit["family"], explicit["model_variant"]), ("Stable Diffusion", "SDXL"))
            self.assertEqual(classify_model(base, prompt="SDXL")["family"], "未知")
        self.assertEqual(classify_model("private", metadata={"baseModel": "PonyV7"})["family"], "未知")
        self.assertEqual(classify_model("private", metadata={"baseModel": "MyPony"})["family"], "未知")

    def test_conflict_kind_distinguishes_unregistered_multi_models_from_explicit_conflicts(self):
        unresolved = classify_model("private-a | private-b", metadata={"model_candidates": ["private-a", "private-b"]})
        self.assertEqual((unresolved["family"], unresolved["reason_code"], unresolved["conflict_kind"]), ("未知", "conflict", "unresolved_checkpoints"))
        for candidates, metadata in ((["anima", "flux1-dev"], {}), (["SD1.5", "SDXL"], {}),
                                     (["private-a", "private-b"], {"base_model": "SDXL", "architecture": "FLUX"})):
            result = classify_model(" | ".join(candidates), metadata={**metadata, "model_candidates": candidates})
            self.assertEqual(result["reason_code"], "conflict")
            self.assertNotIn("conflict_kind", result)
        cached = reclassify_indexed({"model": "private-a | private-b", "parameters": {"model_candidates": ["private-a", "private-b"]}})
        self.assertEqual(cached["conflict_kind"], "unresolved_checkpoints")

    def test_civitai_resource_limits_abstain_instead_of_dropping_late_conflict(self):
        resources = [{"type": "Checkpoint", "modelName": "SDXL"}] * 64
        resources.append({"type": "Checkpoint", "modelName": "anima"})
        result = extract_metadata(self.png(extraMetadata={"resources": resources}))
        self.assertEqual((result["family"], result["reason_code"]), ("未知", "conflict"))
        self.assertTrue(result["parameters"]["model_provenance_incomplete"])
        self.assertNotIn("conflict_kind", result)

    def test_distinct_checkpoint_hashes_survive_multiple_provenance_sources(self):
        result = extract_metadata(self.png(metadata={"model": "private", "modelHash": "de" * 32,
                                                    "generation": {"checkpointHash": "ef" * 32}}))
        self.assertEqual(set(result["parameters"]["checkpoint_hashes"]), {"de" * 32, "ef" * 32})
        graph = self.comfy("private-main")
        graph["1"]["inputs"]["checkpoint_sha256"] = "fa" * 32
        graph["9"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "private-second", "checkpoint_sha256": "fb" * 32}}
        graph["10"] = {"class_type": "ModelMergeSimple", "inputs": {"model1": ["1", 0], "model2": ["9", 0]}}
        graph["5"]["inputs"]["model"] = ["10", 0]
        result = extract_metadata(self.png("comfy-hashes.png", prompt=graph))
        self.assertEqual(set(result["parameters"]["checkpoint_hashes"]), {"fa" * 32, "fb" * 32})


if __name__ == "__main__":
    unittest.main()
