import importlib.util
from pathlib import Path
import unittest

class DeliveryPackagingTests(unittest.TestCase):
    def test_stable_application_version_and_matching_executable(self):
        self.assertEqual(self.api.application_version({'version':'1.0.0','fileVersion':'1.0.0.0'}),'1.0.0')
        for info in ({'version':'1.0.0-rc.1','fileVersion':'1.0.0.0'}, {'version':'1.0.0','fileVersion':'0.9.6.0'}):
            with self.assertRaises(ValueError):self.api.application_version(info)
    def setUp(self):
        path=Path(__file__).with_name('package_native_delivery.py')
        spec=importlib.util.spec_from_file_location('delivery_under_test',path)
        self.api=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
    def test_manifest_paths_cannot_inject_or_escape(self):
        for name in ('../original.png','C:/Users/private.png','folder/../../x','x$INSTDIR.dll','a\\b.dll','Uninstall.exe','luma-install.identity'):
            with self.subTest(name=name),self.assertRaises(ValueError):self.api.relative_file(name)
        self.assertEqual(self.api.relative_file('engine/numpy/core.dll'),'engine/numpy/core.dll')
    def test_uninstall_only_names_known_files_and_empty_directories(self):
        result=self.api.file_macros({'a.dll':'E:/owned/a.dll','engine/child.dll':'E:/owned/child.dll'})
        remove=result[result.index('!macro RemovePayload'):]
        self.assertIn('Push "$INSTDIR\\a.dll"',remove)
        self.assertIn('Push "$INSTDIR\\engine\\child.dll"',remove)
        self.assertEqual(remove.count('Call un.DeletePayloadFile'),2)
        self.assertIn('RMDir "$INSTDIR\\engine"',result)
        self.assertNotIn('RMDir /r',result)
        self.assertNotIn('Delete "$INSTDIR\\*',result)
        self.assertNotIn('LOCALAPPDATA',result)
    def test_parent_directory_reparse_checks_precede_removal(self):
        result=self.api.file_macros({'engine/runtime/a.dll':'E:/owned/a.dll'})
        check=result.index('!macro CheckPayloadDirectories')
        remove=result.index('!macro RemovePayload')
        self.assertLess(check,remove)
        self.assertIn('engine\\runtime',result[check:remove])
        self.assertNotIn('File /r',result)

    def test_installer_metadata_must_use_application_version(self):
        from types import SimpleNamespace as Box
        def sample(label):
            return Box(VS_FIXEDFILEINFO=[Box(FileVersionMS=8,FileVersionLS=2<<16)],FileInfo=[[Box(StringTable=[Box(entries={b'FileVersion':label})])]])
        self.api.validate_installer_version(sample(b'0.8.2'),'0.8.2','0.8.2.0')
        with self.assertRaises(ValueError):self.api.validate_installer_version(sample(b'v3.13'),'0.8.2','0.8.2.0')

if __name__=='__main__':unittest.main()
