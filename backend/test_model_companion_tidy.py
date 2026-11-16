"""Consolidate the reported five-file layout, using synthetic local files only."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from backend import test_model_sidecar_cleanup as fixtures
from backend import model_sidecar_cleanup as cleanup


class ModelCompanionTidyTests(fixtures.ModelSidecarCleanupTests):
    def layout(self):
        model = self.register('fixture-Krea-Mix.safetensors')
        keep = model.with_suffix('.civitai.info')
        keep.write_text(json.dumps({'id':31,'modelId':17,'modelName':'Fixture',
            'baseModel':'Krea 2','model':{'name':'Fixture','type':'LORA'}}),encoding='utf-8')
        matrix = model.with_suffix('.cm-info.json')
        matrix.write_text(json.dumps({'ModelId':17,'VersionId':31,'ModelName':'Fixture',
            'ModelType':'LORA','BaseModel':'Krea 2','ModelDescription':'matrix notes'}),encoding='utf-8')
        web = model.with_suffix('.json')
        web.write_text(json.dumps({'description':'webui notes','modelVersionId':31,'baseModel':'Krea 2'}),encoding='utf-8')
        preview = model.with_suffix('.preview.jpg'); Image.new('RGB',(48,64),'navy').save(preview)
        extra = model.with_suffix('.png'); Image.new('RGB',(48,64),'navy').save(extra)
        return model,keep,preview,[matrix,web,extra]

    def test_reported_layout_reviews_two_extra_metadata_files_and_one_cover(self):
        model,keep,preview,extras = self.layout()
        original = {p:p.read_bytes() for p in [keep,preview,*extras]}
        original_open = Path.open
        def no_weights(path,*args,**kwargs):
            if path.suffix=='.safetensors':raise AssertionError('weight must never be read')
            return original_open(path,*args,**kwargs)
        with patch.object(Path,'open',no_weights):plan = cleanup.preview(self.library)
        self.assertEqual({i['name'] for i in plan['items']},{p.name for p in extras})
        self.assertEqual(plan['count'],3)
        self.assertEqual({p:p.read_bytes() for p in original},original,'preview must not mutate files')

    @unittest.skipUnless(os.name=='nt','Windows handle moves')
    def test_confirmed_tidy_leaves_one_metadata_and_cover_and_undo_restores_exact_bytes(self):
        model,keep,preview,extras = self.layout(); raw = {p:p.read_bytes() for p in extras}
        plan = cleanup.preview(self.library); self.execute(plan)
        self.assertEqual(self.manager.values['errors'],0)
        self.assertEqual(set(self.models.iterdir()),{model,keep,preview})
        self.assertEqual(cleanup.preview(self.library)['count'],0,'repeat must do no work')
        self.restore(plan)
        self.assertEqual({p:p.read_bytes() for p in extras},raw)

    def test_foreign_model_version_and_unknown_json_remain_outside_review(self):
        model,keep,preview,extras = self.layout()
        extras[0].write_text(json.dumps({'ModelName':'Other','ModelId':17,'VersionId':99}))
        extras[1].write_text(json.dumps({'training_configuration':{'learning_rate':0.01}}))
        self.assertEqual({i['name'] for i in cleanup.preview(self.library)['items']},{extras[2].name})

    def test_without_valid_canonical_info_foreign_metadata_is_not_archived(self):
        model,keep,preview,extras = self.layout(); keep.write_bytes(b'{broken')
        self.assertEqual({i['name'] for i in cleanup.preview(self.library)['items']},{extras[2].name})

    @unittest.skipUnless(os.name=='nt','Windows handle moves')
    def test_changed_or_removed_survivor_keeps_its_alternatives(self):
        model,keep,preview,extras = self.layout(); plan = cleanup.preview(self.library)
        keep.write_bytes(b'changed'); preview.unlink(); self.execute(plan)
        self.assertEqual(self.manager.values['errors'],3)
        self.assertTrue(all(p.exists() for p in extras))

    def test_invalid_preferred_cover_does_not_displace_a_valid_cover(self):
        model,keep,preview,extras = self.layout(); preview.write_bytes(b'not an image')
        names = {i['name'] for i in cleanup.preview(self.library)['items']}
        self.assertNotIn(extras[2].name,names,'valid PNG must survive invalid preferred JPEG')

    def test_truncated_jpeg_scan_does_not_displace_decodable_png(self):
        model,keep,preview,extras = self.layout(); preview.write_bytes(preview.read_bytes()[:-20])
        with Image.open(preview) as image:
            with self.assertRaises(OSError):image.load()
        self.assertNotIn(extras[2].name,{i['name'] for i in cleanup.preview(self.library)['items']})

    @unittest.skipUnless(os.name=='nt','Windows handle moves')
    def test_registering_a_shared_companion_owner_after_preview_preserves_all_files(self):
        model,keep,preview,extras = self.layout(); plan=cleanup.preview(self.library)
        self.register(model.stem.upper()+'.ckpt'); self.execute(plan)
        self.assertEqual(self.manager.values['errors'],3)
        self.assertTrue(all(p.exists() for p in extras))

    def test_foreign_preview_and_double_extension_model_companions_are_not_archived(self):
        model,keep,preview,extras = self.layout()
        other=self.register(model.stem+'.preview.safetensors')
        Image.new('RGB',(48,64),'green').save(other.with_suffix('.preview.jpg'))
        double=self.register(model.name+'.safetensors')
        canonical=double.with_suffix('.civitai.info');canonical.write_bytes(keep.read_bytes())
        paths={i['path'] for i in cleanup.preview(self.library)['items']}
        self.assertNotIn(str(preview),paths,'cover shared with original model must survive')
        self.assertNotIn(str(canonical),paths,'canonical of double-extension model must survive')

    def test_malformed_foreign_field_does_not_abort_other_valid_companions(self):
        model,keep,preview,extras = self.layout()
        bad=model.with_name(model.name+'.rgthree-info.json');bad.write_text(json.dumps({'name':'Fixture','trainedWords':None}))
        plan=cleanup.preview(self.library)
        self.assertEqual({i['name'] for i in plan['items']},{p.name for p in extras})
        self.assertTrue(bad.exists())

    def test_compound_metadata_suffix_also_protects_another_models_plain_json(self):
        model,keep,preview,extras = self.layout();shared=[]
        for stem in ('.cm-info','.rgthree-info'):
            self.register(model.stem+stem+'.safetensors')
            path=model.with_name(model.stem+stem+'.json')
            path.write_text(json.dumps({'ModelName':'Fixture','name':'Fixture','description':'Other model notes','trainedWords':[]}))
            shared.append(str(path))
        paths={i['path'] for i in cleanup.preview(self.library)['items']}
        self.assertFalse(paths.intersection(shared),'compound suffix must not hide a registered plain-JSON owner')

    def test_same_basename_model_collision_does_not_review_shared_foreign_companions(self):
        model,keep,preview,extras = self.layout(); self.register(model.stem+'.ckpt')
        self.assertEqual(cleanup.preview(self.library)['count'],0)


if __name__=='__main__':unittest.main()
