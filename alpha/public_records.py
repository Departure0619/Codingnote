"""Remove guessable private fingerprints without mutating historical evidence."""


def public_records(value):
    if isinstance(value, list):
        return [public_records(item) for item in value]
    if isinstance(value, dict):
        if value.get('hidden'):
            allowed={'hidden','status','executed','matches_expected','compile_status','cleaned',
                     'elapsed_seconds','case_id','submission_id','output_truncated','language'}
            return {key:public_records(item) for key,item in value.items() if key in allowed}
        result={key:public_records(item) for key,item in value.items()}
        if isinstance(value.get('result'),dict) and value['result'].get('hidden'):
            if 'stdin' in result: result['stdin']=''
            if 'expected_output' in result: result['expected_output']=None
        return result
    return value
