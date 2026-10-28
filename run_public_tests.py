"""Deterministic public regression; no original question/real-AI fixtures."""
import ipaddress,json,os,socket,sys,unittest
from pathlib import Path

def suite():
    root=Path(__file__).resolve().parent
    result=unittest.defaultTestLoader.discover(str(root/'tests'),top_level_dir=str(root))
    for name in ['test_runner','test_phase_runtime','test_acceptance_gate','test_language_expansion','test_run_cancel','test_alpha_launcher','test_execution']:
        result.addTests(unittest.defaultTestLoader.loadTestsFromName('alpha.tests.'+name))
    return result

if __name__=='__main__':
    for name in list(os.environ):
        if any(k in name.upper() for k in ('TOKEN','SECRET','PASSWORD','API_KEY','PROXY')) or name.upper().startswith(('AIC_','ALPHA_','DEEPSEEK_','OPENAI_')):os.environ.pop(name,None)
    os.environ['AIC_AI_PROVIDER']='disabled'
    blocked=[];original=socket.socket.connect;dns=socket.getaddrinfo
    def permitted(host):
        if host=='localhost':return
        try:ok=ipaddress.ip_address(host).is_loopback
        except ValueError:ok=False
        if not ok:blocked.append('external attempt denied');raise OSError('Public tests deny external requests')
    def connect(sock,address):
        if isinstance(address,tuple):permitted(address[0])
        return original(sock,address)
    def resolve(host,*args,**kwargs):permitted(host);return dns(host,*args,**kwargs)
    socket.socket.connect=connect;socket.getaddrinfo=resolve
    result=unittest.TextTestRunner(verbosity=2).run(suite())
    summary={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'external_attempts_denied':len(blocked),'real_ai_requests':0,'successful':result.wasSuccessful() and not blocked}
    print(json.dumps(summary))
    raise SystemExit(not summary['successful'])
