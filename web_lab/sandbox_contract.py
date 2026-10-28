"""Server-only projection of the already reviewed SUM contract, no new tests."""
import hashlib
import json
from experiments.feature_validation.F04_self_modification import catalog


def contract(experiment):
    if experiment['feature']!='DEMO' or experiment['case_id'] not in {'C01','C03'}:
        raise ValueError('本轮沙箱仅支持 V2 已审核 SUM 练习')
    reviewed=catalog.load()['SUM']
    evidence={tuple(e['args']):e['expected'] for e in experiment['case_snapshot']['evidence'] if e['source_version']=='original'}
    tests=[{'input':args[0],'expected_output':json.dumps(evidence[tuple(args)])+'\n','visible':True}
           for args in reviewed['approved_inputs']]
    value={'id':'SUM-reviewed-v1','tests':tests,'input_min':0,'input_max':8,
           'review_origin_sha256':reviewed['review_origin_sha256']}
    value['sha256']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return value
