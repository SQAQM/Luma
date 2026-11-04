"""Checkpoint registry regressions use isolated synthetic catalogs only."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from backend.metadata import reclassify_indexed, extract_metadata
from backend.model_registry import ModelRegistry
from backend.model_catalog import catalog_entries, catalog_status
from desktop.service import LibraryService, ServiceError


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="luma-model-registry-")
        self.directory=Path(self.temporary.name)
        self.source=self.directory/"synthetic"
        self.source.mkdir()
        self.service=LibraryService(self.directory/"isolated-data")
        self.root_id=self.service.store.add_root(self.source)

    def tearDown(self):
        self.service.close()
        self.temporary.cleanup()

    def mapping(self,match="studio-v9",family="Anima",kind="name",enabled=True):
        return {"kind":kind,"match":match,"family":family,"enabled":enabled,"note":"synthetic fixture"}

    def cached(self,name="one.png",model="studio-v9",parameters=None,raw=None,root_id=None):
        path=self.source/name
        Image.new("RGB",(16,12),(40,100,160)).save(path)
        metadata={"width":16,"height":12,"format":"PNG","model":model,"family":"未知","prompt":"kept prompt, Anima, portrait","negative_prompt":"kept negative","tags":["kept","portrait"],"parameters":parameters or {},"raw_metadata":raw or {},"artist":"synthetic author"}
        image_id=self.service.store.upsert(path,root_id or self.root_id,path.stat(),metadata)
        return self.service.detail(image_id)

    def wait(self,job):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            result=self.service.job(job["id"])
            if result["status"] not in ("queued","running"):
                return result
            time.sleep(.01)
        self.fail("cached model job did not finish")

    def run_models(self,**scope):
        job=self.wait(self.service.reclassify_models(**scope))
        self.assertEqual(job["status"],"completed",job)
        return job

    def test_builtin_finetune_name_and_renamed_checkpoint_hash_use_cached_only(self):
        image=self.cached(model="Illustrious-XL-v0.1.safetensors")
        renamed=self.cached("renamed.png",model="private-renamed-checkpoint",parameters={"model_hash":"6681e8e4b134"})
        before={item["id"]:self.service.detail(item["id"]) for item in (image,renamed)}
        originals={item["id"]:Path(item["path"]).read_bytes() for item in (image,renamed)}
        with patch("builtins.open",side_effect=AssertionError("opened image")),patch("pathlib.Path.open",side_effect=AssertionError("opened image")),patch("pathlib.Path.stat",side_effect=AssertionError("statted image")),patch("os.scandir",side_effect=AssertionError("scanned image")),patch("PIL.Image.open",side_effect=AssertionError("decoded image")):
            self.run_models(scope="all")
        for item in (image,renamed):
            after=self.service.detail(item["id"])
            self.assertEqual(after["family"],"Stable Diffusion")
            self.assertEqual(after["parameters"]["model_variant"],"SDXL")
            self.assertEqual(after["parameters"]["model_reason_code"],"catalog")
            self.assertEqual(after["parameters"]["model_catalog_version"],catalog_status()["version"])
            self.assertTrue(after["parameters"]["model_catalog_sources"])
            for key in ("path","size","mtime_ns","indexed_at","prompt","negative_prompt","tags","prompt_tags","raw_metadata","artist","favorite","rating","content_tags"):
                self.assertEqual(after[key],before[item["id"]][key],key)
            self.assertEqual(Path(item["path"]).read_bytes(),originals[item["id"]])

    def test_builtin_name_does_not_guess_other_versions_prompts_or_wrong_hash(self):
        fixtures=[
            self.cached("version.png",model="Illustrious-XL-v9.999"),
            self.cached("prompt-only.png",model="",parameters={"prompt":"Illustrious-XL-v0.1","lora_hash":"6681e8e4b134"}),
            self.cached("wrong-hash.png",model="Illustrious-XL-v0.1",parameters={"model_hash":"f"*64}),
            self.cached("generic.png",model="diffusion_pytorch_model.safetensors"),
            self.cached("misleading.png",model="randomXL-v1"),
        ]
        self.run_models(scope="all")
        for item in fixtures:
            self.assertEqual(self.service.detail(item["id"])["family"],"未知",item["name"])

    def test_builtin_conflicts_with_user_or_explicit_architecture_and_auxiliary(self):
        self.service.put_model_mappings([self.mapping("Illustrious-XL-v0.1",family="FLUX")])
        user=self.cached("user-conflict.png",model="Illustrious-XL-v0.1")
        architecture=self.cached("architecture-conflict.png",model="NoobAI-XL-v1.1",parameters={"base_model":"FLUX"})
        adapter=self.cached("adapter.png",model="NoobAI-XL-v1.1",parameters={"type":"LoRA","model_hash":"6681e8e4b134"})
        variant=self.cached("variant-conflict.png",model="NoobAI-XL-v1.1",parameters={"base_model":"SD1.5"})
        self.run_models(scope="all")
        for item in (user,architecture,adapter,variant):
            result=self.service.detail(item["id"])
            self.assertEqual(result["family"],"未知",item["name"])
            self.assertEqual(result["parameters"]["model_reason_code"],"conflict")

    def test_model_catalog_exact_namespace_and_multi_checkpoint_completeness(self):
        repo=self.cached("repo.png",model="Lykon/DreamShaper-8")
        wrong_repo=self.cached("wrong-repo.png",model="private/DreamShaper-8")
        spoofed_repos=[self.cached(f"spoofed-{index}.png",model=value) for index,value in enumerate(("private/NoobAI-XL-v1.1","someone/animagine-xl-4.0","private/Illustrious-XL-v0.1"))]
        together=self.cached("two-known.png",model="Illustrious-XL-v0.1",parameters={"model_candidates":["Illustrious-XL-v0.1","NoobAI-XL-v1.1"]})
        missing=self.cached("one-missing.png",model="Illustrious-XL-v0.1",parameters={"model_candidates":["Illustrious-XL-v0.1","private-model"]})
        self.run_models(scope="all")
        self.assertEqual(self.service.detail(repo["id"])["family"],"Stable Diffusion")
        self.assertEqual(self.service.detail(repo["id"])["parameters"]["model_variant"],"SD1.5")
        self.assertEqual(self.service.detail(wrong_repo["id"])["family"],"未知")
        for item in spoofed_repos:
            self.assertEqual(self.service.detail(item["id"])["family"],"未知",item["model"])
        self.assertEqual(self.service.detail(together["id"])["family"],"Stable Diffusion")
        self.assertEqual(self.service.detail(missing["id"])["family"],"未知")

    def test_new_import_snapshots_catalog_and_recognizes_finetune(self):
        path=self.source/"only-synthetic.png"
        text=PngInfo()
        text.add_text("parameters","synthetic prompt\nSteps: 1, Model: NoobAI-XL-v1.1")
        Image.new("RGB",(16,12),(70,90,120)).save(path,pnginfo=text)
        original=path.read_bytes()
        job=self.wait(self.service.import_directory(str(self.source)))
        self.assertEqual(job["status"],"completed",job)
        self.assertEqual(job["payload"]["model_catalog"]["version"],catalog_status()["version"])
        self.assertTrue(job["payload"]["model_catalog"]["entries"])
        item=self.service.query()["items"][0]
        self.assertEqual(item["family"],"Stable Diffusion")
        self.assertEqual(self.service.detail(item["id"])["parameters"]["model_variant"],"SDXL")
        self.assertEqual(path.read_bytes(),original)

    def test_distinct_repository_namespaces_cannot_share_one_catalog_match(self):
        for index,names in enumerate((
            ["Lykon/DreamShaper-8","private/DreamShaper-8"],
            ["cagliostrolab/animagine-xl-4.0","private/animagine-xl-4.0"],
        )):
            image=self.cached(f"namespace-{index}.png",model=names[0],parameters={"model_candidates":names})
            self.run_models(ids=[image["id"]])
            self.assertEqual(self.service.detail(image["id"])["family"],"未知")

    def test_checkpoint_bound_ancestry_combines_with_registration_and_checks_conflicts(self):
        resources=[{"type":"Checkpoint","modelName":"private-a","baseModel":"SDXL"},
                   {"type":"Checkpoint","modelName":"private-b"}]
        image=self.cached("ancestry-and-mapping.png",model="private-a | private-b",
                          parameters={"model_candidates":["private-a","private-b"]},
                          raw={"extraMetadata":json.dumps({"resources":resources})})
        self.service.put_model_mappings([self.mapping("private-b","Stable Diffusion")])
        self.run_models(ids=[image["id"]])
        result=self.service.detail(image["id"])
        self.assertEqual(result["family"],"Stable Diffusion")
        self.assertEqual(result["parameters"]["model_variant"],"SDXL")
        self.service.put_model_mappings([self.mapping("private-b","Anima")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")
        self.service.put_model_mappings([self.mapping("private-a","Anima"),self.mapping("private-b","Anima")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["parameters"]["model_reason_code"],"conflict")

    def test_short_hash_collision_abstains_even_when_families_agree(self):
        prefix="0123456789"
        self.service.put_model_mappings([self.mapping(prefix+"a"*54,"Stable Diffusion","hash"),
                                         self.mapping(prefix+"b"*54,"Stable Diffusion","hash")])
        image=self.cached("same-family-collision.png",model="private",parameters={"model_hash":prefix})
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["parameters"]["model_reason_code"],"conflict")
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")
        full=self.cached("resolved-full-digest.png",model="private",parameters={"model_hash":prefix,"checkpoint_sha256":prefix+"a"*54})
        self.run_models(ids=[full["id"]])
        self.assertEqual(self.service.detail(full["id"])["family"],"Stable Diffusion")

    def test_snapshot_keeps_explicit_task_identity_rules_when_live_directory_changes(self):
        image=self.cached(model="NoobAI-XL-v1.1")
        actual_submit=self.service.manager.submit
        captured={}
        def submit(kind,payload,total=0):
            captured.update(payload)
            with patch("backend.model_registry.catalog_entries",return_value=()):
                return actual_submit(kind,payload,total)
        with patch.object(self.service.manager,"submit",side_effect=submit):
            self.run_models(ids=[image["id"]])
        self.assertTrue(captured["model_catalog"]["entries"])
        self.assertEqual(self.service.detail(image["id"])["family"],"Stable Diffusion")

    def test_mapping_normalization_validation_and_reopen_without_reclassification(self):
        image=self.cached()
        before=self.service.detail(image["id"])
        with patch("backend.metadata.extract_metadata",side_effect=AssertionError("saving read a file")),patch("backend.metadata.reclassify_indexed",side_effect=AssertionError("saving reclassified")):
            result=self.service.put_model_mappings([self.mapping(r"C:\checkpoints\STUDIO-V9.safetensors"),self.mapping("ABCDEF012345","FLUX","hash",False)])
        self.assertEqual(result[0]["match"],"studio-v9")
        self.assertEqual(result[1]["match"],"abcdef012345")
        self.assertFalse(result[1]["enabled"])
        self.assertEqual(self.service.detail(image["id"]),before)
        self.assertEqual(self.service.jobs(),[])
        for invalid in ([self.mapping(),self.mapping("STUDIO-V9.ckpt")],[self.mapping("abcd",kind="hash")],[self.mapping(family="guess")],[{**self.mapping(),"note":"x"*501}],[{**self.mapping(),"enabled":1}],[self.mapping()]*1001):
            with self.assertRaises(ServiceError):
                self.service.put_model_mappings(invalid)
        self.assertEqual(self.service.model_mappings(),result)
        self.service.close()
        self.service=LibraryService(self.directory/"isolated-data")
        self.assertEqual(self.service.model_mappings(),result)
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")

    def test_cached_only_reclassification_preserves_original_and_user_data(self):
        image=self.cached(parameters={"steps":30,"seed":71})
        path=Path(image["path"])
        original=path.read_bytes()
        self.service.patch_image(image["id"],{"favorite":True,"rating":5})
        definition=self.service.content_tags()[0]
        self.service.store.set_content_matches(image["id"],[{"tag_id":definition["id"],"score":.8}],source="manual",model_id="manual")
        before=self.service.detail(image["id"])
        self.service.put_model_mappings([self.mapping()])
        # The job must keep working when every original-image I/O entry point is
        # forbidden. SQLite is the only catalog read permitted in this test.
        with patch("builtins.open",side_effect=AssertionError("opened original")),patch("pathlib.Path.open",side_effect=AssertionError("opened path")),patch("pathlib.Path.stat",side_effect=AssertionError("statted original")),patch("pathlib.Path.lstat",side_effect=AssertionError("statted original")),patch("os.scandir",side_effect=AssertionError("scanned directory")),patch("PIL.Image.open",side_effect=AssertionError("decoded original")),patch("backend.metadata.extract_metadata",side_effect=AssertionError("extracted original")):
            job=self.run_models(ids=[image["id"]])
        after=self.service.detail(image["id"])
        self.assertEqual(after["family"],"Anima")
        self.assertEqual(after["parameters"]["model_reason_code"],"registered")
        for key in ("path","size","mtime_ns","indexed_at","created_at","prompt","negative_prompt","tags","prompt_tags","raw_metadata","artist","favorite","rating","content_tags"):
            self.assertEqual(after[key],before[key],key)
        self.assertEqual(after["parameters"]["seed"],71)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(job["processed"],1)

    def test_registry_requires_checkpoint_name_or_hash_and_preserves_conflicts(self):
        mappings=[self.mapping(),self.mapping("abcde0123456789",family="FLUX",kind="hash"),self.mapping("anima-model",family="FLUX")]
        self.service.put_model_mappings(mappings)
        exact=self.cached("exact.png",model=r"models\STUDIO-V9.ckpt")
        substring=self.cached("substring.png",model="studio-v90")
        prompt=self.cached("prompt.png",model="",parameters={"prompt":"studio-v9 Anima","loras":["studio-v9"]})
        wrong_hash=self.cached("imagehash.png",model="unknown",parameters={"image_hash":"abcde0123456789","hashes":{"lora:studio":"abcde0123456789"}})
        checkpoint=self.cached("hash.png",model="unknown",parameters={"Model hash":"abcde0123456789ffff"})
        explicit=self.cached("explicit.png",model="anima-model")
        graph=self.cached("graph.png",model="studio-v9",parameters={"model_candidates":["studio-v9","unknown"]})
        non_ai=self.cached("non-ai.png",model="",parameters={"ai":False})
        self.run_models(scope="all")
        self.assertEqual(self.service.detail(exact["id"])["family"],"Anima")
        self.assertEqual(self.service.detail(checkpoint["id"])["family"],"FLUX")
        for item in (substring,prompt,wrong_hash,explicit,graph):
            self.assertEqual(self.service.detail(item["id"])["family"],"未知",item["name"])
        for item in (explicit,graph):
            self.assertEqual(self.service.detail(item["id"])["parameters"]["model_reason_code"],"conflict")
        self.assertEqual(self.service.detail(non_ai["id"])["family"],"非 AI")

    def test_legacy_auxiliary_model_hash_cannot_be_registered_as_checkpoint(self):
        self.service.put_model_mappings([self.mapping("opaque_adapter",family="Stable Diffusion"),self.mapping("abcdef0123456789",family="Stable Diffusion",kind="hash")])
        fixtures=[]
        for field in ("model_type","type","resource_type"):
            for resource in ("LoRA","ControlNet","VAE","Upscaler","LyCORIS"):
                fixtures.append(self.cached(f"{field}-{resource}.png",model="opaque_adapter",parameters={field:resource,"base_model":"SDXL","model_hash":"abcdef0123456789"}))
        self.run_models(scope="all")
        for image in fixtures:
            result=self.service.detail(image["id"])
            self.assertEqual(result["family"],"未知",image["name"])
            self.assertEqual(result["parameters"]["model_reason_code"],"conflict",image["name"])
        # The registry also defends its public pure resolver if a caller supplies
        # an unsafe baseline from an older parser.
        wrong={"family":"Stable Diffusion","reason_code":"explicit"}
        result=ModelRegistry.resolve({"model":"opaque_adapter","parameters":{"model_type":"LoRA","model_hash":"abcdef0123456789"}},wrong,self.service.model_mappings())
        self.assertEqual(result["reason_code"],"conflict")
        self.assertEqual(result["family"],"未知")

    def test_legacy_non_ai_index_decision_is_not_overridden_by_registration(self):
        image=self.cached("legacy-non-ai.png")
        with self.service.store.connect() as db:
            db.execute("UPDATE images SET family='非 AI',reason='legacy manual non-AI decision' WHERE id=?",(image["id"],))
        before=self.service.detail(image["id"])
        self.service.put_model_mappings([self.mapping()])
        with patch("backend.metadata.reclassify_indexed",side_effect=AssertionError("non-AI record must remain unchanged")):
            result=self.run_models(ids=[image["id"]])
        self.assertEqual(result["processed"],1)
        self.assertEqual(self.service.detail(image["id"]),before)

    def test_ambiguous_hash_prefix_and_name_hash_disagreement_remain_unknown(self):
        self.service.put_model_mappings([self.mapping("0123456789",kind="hash"),self.mapping("0123456789abcdef",family="FLUX",kind="hash"),self.mapping("studio-v9"),self.mapping("fedcba9876543210",family="FLUX",kind="hash")])
        truncated=self.cached("truncated.png",model="opaque",parameters={"model_hash":"0123456789"})
        full=self.cached("full.png",model="opaque",parameters={"checkpoint_sha256":"0123456789abcdef"})
        disagree=self.cached("disagreement.png",parameters={"hashes":{"model":"fedcba9876543210"}})
        self.run_models(scope="all")
        for image in (truncated,full,disagree):
            result=self.service.detail(image["id"])
            self.assertEqual(result["family"],"未知")
            self.assertEqual(result["parameters"]["model_reason_code"],"conflict")

    def test_multiple_checkpoint_hashes_require_complete_consistent_registration(self):
        first="1111111111222222"
        second="3333333333444444"
        image=self.cached("different-hashes.png",model="",parameters={"model_hash":first,"checkpoint_hash":second})
        compatible=self.cached("same-hash.png",model="",parameters={"model_hash":first[:10],"model_sha256":first})
        self.service.put_model_mappings([self.mapping(first,kind="hash")])
        self.run_models(scope="all")
        result=self.service.detail(image["id"])
        self.assertEqual(result["family"],"未知")
        self.assertEqual(result["parameters"]["model_reason_code"],"conflict")
        self.assertEqual(self.service.detail(compatible["id"])["family"],"Anima")
        self.service.put_model_mappings([self.mapping(first,kind="hash"),self.mapping(second,kind="hash")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"Anima")
        self.service.put_model_mappings([self.mapping(first,kind="hash"),self.mapping(second,family="FLUX",kind="hash")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")

    def test_known_full_hash_collision_cannot_share_a_single_short_registration(self):
        prefix="abcdef0123"
        first=prefix+"1"*54
        second=prefix+"2"*54
        image=self.cached("known-collision.png",model="",parameters={"model_hash":first,"checkpoint_sha256":second})
        self.service.put_model_mappings([self.mapping(prefix,kind="hash")])
        self.run_models(ids=[image["id"]])
        result=self.service.detail(image["id"])
        self.assertEqual(result["family"],"未知")
        self.assertEqual(result["parameters"]["model_reason_code"],"conflict")
        self.service.put_model_mappings([self.mapping(prefix,kind="hash"),self.mapping(first,kind="hash")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")
        self.service.put_model_mappings([self.mapping(prefix,kind="hash"),self.mapping(first,kind="hash"),self.mapping(second,kind="hash")])
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"Anima")

    def test_name_mapping_does_not_ignore_multiple_unregistered_checkpoint_hashes(self):
        hashes={"model_hash":"1111111111222222","checkpoint_hash":"3333333333444444"}
        unknown=self.cached("name-only.png",parameters=hashes)
        explicit=self.cached("explicit-baseline.png",model="anima-model",parameters=hashes)
        self.service.put_model_mappings([self.mapping(),self.mapping("anima-model")])
        self.run_models(scope="all")
        result=self.service.detail(unknown["id"])
        self.assertEqual(result["family"],"未知")
        self.assertEqual(result["parameters"]["model_reason_code"],"conflict")
        result=self.service.detail(explicit["id"])
        self.assertEqual(result["family"],"Anima")
        self.assertEqual(result["parameters"]["model_reason_code"],"explicit")
        self.assertLess(result["confidence"],1)

    def test_cached_raw_metadata_adds_checkpoint_evidence_without_file_read(self):
        image=self.cached(model="",raw={"parameters":"synthetic prompt\nSteps: 20, Model: studio-v9, Model hash: abcde0123456789"})
        self.service.put_model_mappings([self.mapping()])
        with patch("backend.metadata.extract_metadata",side_effect=AssertionError("should use cached raw metadata")):
            self.run_models(ids=[image["id"]])
        result=self.service.detail(image["id"])
        self.assertEqual(result["family"],"Anima")
        self.assertEqual(result["model"],"studio-v9")
        self.assertEqual(result["prompt"],image["prompt"])

    def test_ready_previews_are_invalidated_only_for_affected_images(self):
        first=self.cached()
        second=self.cached("other.png",model="other-unknown")
        destination=self.directory/"organized"
        affected=self.service.preview("format",str(destination),ids=[first["id"]])
        unrelated=self.service.preview("format",str(destination),ids=[second["id"]])
        self.service.put_model_mappings([self.mapping()])
        self.run_models(ids=[first["id"]])
        with self.service.store.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM plans WHERE id=?",(affected["id"],)).fetchone()[0],"cancelled")
            self.assertEqual(db.execute("SELECT status FROM plans WHERE id=?",(unrelated["id"],)).fetchone()[0],"ready")
        with self.assertRaises(ValueError):
            self.service.execute(affected["id"])
        self.assertTrue(Path(first["path"]).exists())
        self.assertFalse(destination.exists())

    def test_rescan_race_is_skipped_but_favorite_rating_change_is_preserved(self):
        raced=self.cached("raced.png")
        edited=self.cached("edited.png")
        self.service.put_model_mappings([self.mapping()])
        def concurrent_change(image):
            if image["id"]==raced["id"]:
                with self.service.store.connect() as db:
                    db.execute("UPDATE images SET indexed_at=indexed_at+1,model='new-after-rescan' WHERE id=?",(image["id"],))
            else:
                self.service.patch(image["id"],{"favorite":True,"rating":4})
            return reclassify_indexed(image)
        with patch("backend.metadata.reclassify_indexed",side_effect=concurrent_change):
            result=self.run_models(scope="all")
        self.assertEqual(self.service.detail(raced["id"])["model"],"new-after-rescan")
        self.assertEqual(self.service.detail(raced["id"])["family"],"未知")
        self.assertEqual(self.service.detail(edited["id"])["family"],"Anima")
        self.assertTrue(self.service.detail(edited["id"])["favorite"])
        self.assertEqual(self.service.detail(edited["id"])["rating"],4)
        self.assertIn("跳过 1",result["message"])

    def test_root_scope_highwater_and_bounded_batches(self):
        for index in range(205):
            self.cached(f"synthetic-{index:04}.png")
        other_root=self.directory/"other-root"
        other_root.mkdir()
        root_id=self.service.store.add_root(other_root)
        excluded=self.cached("excluded.png",root_id=root_id)
        self.service.put_model_mappings([self.mapping()])
        seen=[]
        added=[]
        batch_progress=[]
        real_progress=self.service.manager.progress
        def progress(job_id,**values):
            if "processed" in values:
                batch_progress.append(values["processed"])
            return real_progress(job_id,**values)
        def observe(image):
            seen.append(image["id"])
            if not added:
                added.append(self.cached("late.png"))
            return reclassify_indexed(image)
        with patch("backend.metadata.reclassify_indexed",side_effect=observe),patch.object(self.service.store,"list_images",side_effect=AssertionError("materialized gallery")),patch.object(self.service.manager,"progress",side_effect=progress):
            result=self.run_models(root_id=self.root_id)
        self.assertEqual(result["processed"],205)
        self.assertEqual(len(seen),205)
        self.assertEqual(len(set(seen)),205)
        self.assertIn(100,batch_progress)
        self.assertIn(200,batch_progress)
        self.assertNotIn(excluded["id"],seen)
        self.assertEqual(self.service.detail(added[0]["id"])["family"],"未知")
        self.assertEqual(self.service.detail(excluded["id"])["family"],"未知")

    def test_future_import_uses_start_snapshot_and_busy_actions_do_not_mutate(self):
        metadata=PngInfo()
        metadata.add_text("parameters","synthetic prompt\nSteps: 20, Model: studio-v9")
        path=self.source/"import.png"
        Image.new("RGB",(18,14),(50,90,130)).save(path,pnginfo=metadata)
        self.service.put_model_mappings([self.mapping()])
        entered=threading.Event()
        release=threading.Event()
        def delayed_extract(value):
            entered.set()
            self.assertTrue(release.wait(10))
            return extract_metadata(value)
        with patch("backend.metadata.extract_metadata",side_effect=delayed_extract):
            job=self.service.import_directory(str(self.source))
            try:
                self.assertTrue(entered.wait(5))
                for action in (lambda:self.service.put_model_mappings([]),lambda:self.service.reclassify_models(scope="all")):
                    with self.assertRaises(ServiceError) as raised:
                        action()
                    self.assertEqual(raised.exception.code,"busy")
                # Simulate a separate client's registration after job submission;
                # the in-flight import must retain its persisted original snapshot.
                self.service.model_registry.replace([self.mapping(family="FLUX")])
            finally:
                release.set()
            result=self.wait(job)
        self.assertEqual(result["status"],"completed",result)
        image=self.service.query()["items"][0]
        self.assertEqual(image["family"],"Anima")
        self.assertEqual(result["payload"]["model_mappings"][0]["family"],"Anima")
        self.run_models(ids=[image["id"]])
        self.assertEqual(self.service.detail(image["id"])["family"],"FLUX")

    def test_reclassification_busy_scope_validation_and_cancellation(self):
        image=self.cached()
        self.service.put_model_mappings([self.mapping()])
        for kwargs in ({},{"ids":[]},{"ids":[True]},{"ids":[image["id"]],"scope":"all"},{"root_id":99999},{"scope":"implicit"}):
            with self.assertRaises(ServiceError):
                self.service.reclassify_models(**kwargs)
        entered=threading.Event()
        release=threading.Event()
        def delayed(image):
            entered.set()
            self.assertTrue(release.wait(10))
            return reclassify_indexed(image)
        with patch("backend.metadata.reclassify_indexed",side_effect=delayed):
            job=self.service.reclassify_models(scope="all")
            try:
                self.assertTrue(entered.wait(5))
                for action in (lambda:self.service.import_directory(str(self.source)),lambda:self.service.put_model_mappings([]),lambda:self.service.preview("format",str(self.directory/"organized"),ids=[image["id"]]),lambda:self.service.preview_remove_root(self.root_id),lambda:self.service.execute("nonexistent-plan"),lambda:self.service.undo("nonexistent-job"),lambda:self.service.remove_root(self.root_id,"none")):
                    with self.assertRaises(ServiceError) as raised:
                        action()
                    self.assertEqual(raised.exception.code,"busy")
                self.service.cancel(job["id"])
            finally:
                release.set()
            result=self.wait(job)
        self.assertEqual(result["status"],"cancelled")
        self.assertEqual(self.service.detail(image["id"])["family"],"未知")


if __name__=="__main__":
    unittest.main()
