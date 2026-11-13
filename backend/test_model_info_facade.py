"""Model metadata tidy uses a persisted review through the native RPC facade."""
import tempfile,threading,time,unittest,uuid
from pathlib import Path
from desktop.service import LibraryService
from backend import model_sidecars
from worker.management import read,write

class TidyFacadeTests(unittest.TestCase):
    def test_review_confirmation_apply_and_undo(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);models=base/'models';models.mkdir();weight=models/'fixture.safetensors';weight.write_bytes(b'inert')
            model_sidecars.write(weight,{'model_id':1,'version_id':2,'model_name':'Fixture','base_model':'Anima','files':[]},'a'*64,compatibility=True)
            service=LibraryService(base/'index',automatic_recovery=False);event=threading.Event()
            def action(name,args):return write(service,name,args,uuid.uuid4().hex,event)
            def settled(task):
                end=time.monotonic()+10
                while time.monotonic()<end:
                    result=service.job(task['id'])
                    if result['status'] not in ('running','queued'):return result
                    time.sleep(.01)
                self.fail('Own tidy task timed out')
            try:
                root=action('models.add',{'path':str(models)});settled(action('models.scan',{'rootId':root['id']}))
                plan=read(service,'modelInfoCleanupPreview',{'rootId':root['id']},event)
                self.assertEqual(plan['count'],2)
                with self.assertRaises(ValueError):action('models.cleanupInfo',{'planId':plan['id'],'confirmed':False})
                self.assertEqual(settled(action('models.cleanupInfo',{'planId':plan['id'],'confirmed':True}))['errors'],0)
                self.assertFalse(weight.with_suffix('.json').exists());self.assertTrue(weight.with_suffix('.civitai.info').exists())
                self.assertEqual(settled(action('models.undoInfoCleanup',{'planId':plan['id'],'confirmed':True}))['errors'],0)
                self.assertTrue(weight.with_suffix('.json').exists());self.assertEqual(weight.read_bytes(),b'inert')
            finally:service.close()

if __name__=='__main__':unittest.main()
