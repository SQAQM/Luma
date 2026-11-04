import uuid
from worker.test_frozen import FrozenEngineTests
from worker.test_engine import request
from worker.protocol import read_frame

class FrozenManagementTests(FrozenEngineTests):
    def rpc(self, child, method, payload):
        self.send(child,request(method,payload,rid=uuid.uuid4().hex))
        return read_frame(child.stdout)
    def test_real_packaged_runtime_without_weights(self):
        child=self.start();self.hello(child)
        reply=self.rpc(child,'manage.read',{'section':'runtime','args':{}})
        self.assertEqual(reply['kind'],'result');self.assertTrue(all(reply['payload'][name] for name in ('numpy','onnxruntime','tokenizers')))
    def test_missing_classifier_has_specific_safe_error(self):
        child=self.start();self.hello(child)
        reply=self.rpc(child,'manage.write',{'operationId':uuid.uuid4().hex,'action':'vision.classify','args':{'ids':[1]}})
        self.assertEqual(reply['kind'],'error');self.assertEqual(reply['payload'],{'code':'not_ready'})
        jobs=self.rpc(child,'manage.read',{'section':'jobs','args':{}});self.assertEqual(jobs['payload'],[])
