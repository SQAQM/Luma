"""Inert exports in hidden local folders; no user content."""
import ctypes,json,os,unittest
from unittest.mock import patch
from backend import test_model_interop as helpers
from backend import model_sidecars

class SidecarWriteTests(unittest.TestCase):
    setUp=helpers.ModelInteropTests.setUp
    def record(self):return {'model_id':17,'version_id':31,'model_name':'Fixture','description':'Remote intro','type':'Checkpoint','base_model':'Anima','trained_words':[]}
    def export(self):return model_sidecars.write(self.weight,self.record(),self.digest,self.cover)
    def test_bad_first_sidecar_does_not_block_other_metadata_or_cover(self):
        target=self.weight.with_suffix('.civitai.info');target.write_bytes(b'{broken')
        with self.assertRaises(model_sidecars.SidecarError) as error:self.export()
        self.assertEqual(getattr(error.exception,'code',None),'sidecar_format')
        self.assertEqual(target.read_bytes(),b'{broken')
        self.assertTrue(self.weight.with_suffix('.json').exists())
        self.assertTrue(self.weight.with_name(self.weight.name+'.rgthree-info.json').exists())
        self.assertEqual(self.weight.with_suffix('.png').read_bytes(),self.cover)
    def test_empty_placeholder_is_filled_with_valid_metadata(self):
        target=self.weight.with_suffix('.json');target.write_bytes(b' \r\n')
        self.export()
        self.assertEqual(json.loads(target.read_text())['description'],'Remote intro')
    def test_conflicting_directory_does_not_block_other_exports(self):
        target=self.weight.with_suffix('.civitai.info');target.mkdir()
        with self.assertRaises(model_sidecars.SidecarError) as error:self.export()
        self.assertEqual(error.exception.code,'sidecar_conflict')
        self.assertTrue(target.is_dir());self.assertTrue(self.weight.with_suffix('.json').exists())
        self.assertTrue(self.weight.with_name(self.weight.name+'.rgthree-info.json').exists())
        self.assertEqual(self.weight.with_suffix('.png').read_bytes(),self.cover)
    @unittest.skipUnless(os.name=='nt','Windows attributes')
    def test_unchanged_readonly_exports_need_no_write_access(self):
        self.export();target=self.weight.with_suffix('.json');before=target.read_bytes()
        ctypes.windll.kernel32.SetFileAttributesW(str(target),1)
        try:self.export();self.assertEqual(target.read_bytes(),before)
        finally:ctypes.windll.kernel32.SetFileAttributesW(str(target),128)
    @unittest.skipUnless(os.name=='nt','Windows hidden attributes')
    def test_hidden_directory_and_sidecar_are_supported(self):
        self.export();target=self.weight.with_suffix('.json')
        ctypes.windll.kernel32.SetFileAttributesW(str(self.models),2)
        ctypes.windll.kernel32.SetFileAttributesW(str(target),2)
        try:self.export();self.assertTrue(self.weight.with_suffix('.png').exists())
        finally:ctypes.windll.kernel32.SetFileAttributesW(str(target),128);ctypes.windll.kernel32.SetFileAttributesW(str(self.models),16)
    def test_directory_permission_failure_has_specific_fixed_reason(self):
        with patch.object(model_sidecars,'_json',side_effect=PermissionError(13,'PRIVATE PATH')):
            with self.assertRaises(model_sidecars.SidecarError) as error:self.export()
        self.assertEqual(getattr(error.exception,'code',None),'sidecar_permission')
        self.assertNotIn('PRIVATE',str(error.exception))
    def test_transient_sharing_violation_retries_existing_file(self):
        self.export();original=model_sidecars._exclusive_json;attempts=[]
        def busy_once(path):
            attempts.append(1)
            if len(attempts)==1:
                error=OSError('synthetic busy');error.winerror=32;raise error
            return original(path)
        with patch.object(model_sidecars,'_exclusive_json',side_effect=busy_once):
            model_sidecars.write(self.weight,{**self.record(),'description':'Updated'},self.digest)
        self.assertGreaterEqual(len(attempts),2)
        self.assertEqual(json.loads(self.weight.with_suffix('.json').read_text())['description'],'Updated')

if __name__=='__main__':unittest.main()
