import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from alpha.app import create_app


class CancelTests(unittest.TestCase):
    def test_cancellation_is_not_blocked_by_run_lock_and_keeps_exact_version(self):
        started = threading.Event()
        class SlowExecution:
            supports_cancellation = True
            def status(self): return {'ready': True}
            def run(self, language, source, stdin, *, cancel_event=None):
                started.set()
                if cancel_event is None or not cancel_event.wait(3):
                    return {'status': 'timeout', 'executed': True, 'stdout':'', 'stderr':''}
                return {'status':'cancelled', 'executed':True, 'cleaned':True, 'stdout':'', 'stderr':''}
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, execution=SlowExecution())) as client:
            task = client.post('/api/tasks',json={'title':'取消测试','language':'c','code':'int main(){}'}).json()
            path = '/api/tasks/' + task['id']
            with ThreadPoolExecutor() as pool:
                future = pool.submit(client.post, path+'/run', json={
                    'version_id':task['current_version_id'],'request_id':'cancel-one','stdin':''})
                self.assertTrue(started.wait(2))
                other = client.post('/api/tasks',json={'title':'其他','language':'cpp','code':'int main(){}'}).json()
                self.assertEqual(client.post('/api/tasks/'+other['id']+'/run/cancel-one/cancel').status_code,409)
                changed = client.put(path,json={'title':'取消测试','language':'c','code':'int main(){return 1;}',
                                               'revision':task['revision']}).json()
                response = client.post(path+'/run/cancel-one/cancel')
                self.assertEqual(response.status_code,200,response.text)
                evidence = future.result(timeout=6).json()
            self.assertEqual(evidence['result']['status'],'cancelled')
            self.assertEqual(evidence['version_id'],task['current_version_id'])
            self.assertNotEqual(evidence['version_id'],changed['current_version_id'])
            replay = client.post(path+'/run',json={'version_id':task['current_version_id'],
                                                   'request_id':'cancel-one','stdin':''})
            self.assertEqual(replay.json()['id'],evidence['id'])
            self.assertEqual(client.post(path+'/run/cancel-one/cancel').status_code,409)
