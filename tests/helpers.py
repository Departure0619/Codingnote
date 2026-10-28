"""Offline test double; never executes submitted programs."""
class OfflineExecution:
    def status(self):return {'ready':False,'reason':'Docker unavailable in isolated unit test','languages':{}}
    def run(self,*args,**kwargs):return {'status':'unavailable','executed':False,'stdout':'','stderr':'','error':'Docker unavailable in isolated unit test'}
