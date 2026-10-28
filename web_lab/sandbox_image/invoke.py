"""Runs only inside a task container. stdout is the judged program output."""
import json
import runpy
import sys

n = json.loads(sys.stdin.read(64))
namespace = runpy.run_path('/tmp/student.py', run_name='student')
value = namespace['solve'](n)
# Extra student printing remains in stdout and can cause a comparison failure.
print(json.dumps(value, ensure_ascii=False, allow_nan=False))
