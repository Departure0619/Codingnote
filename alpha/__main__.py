"""python -m alpha: single process, loopback only, independent learner storage."""
import argparse
from pathlib import Path
import threading
import time
import urllib.request
import webbrowser
import uvicorn
from .locking import lock_data
from .app import create_app
from .runner import Execution

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description='AI 编程错题学习工作台 Alpha')
    parser.add_argument('--port', type=int, default=8027)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data' / 'alpha')
    parser.add_argument('--runtime-dir', type=Path, help='可选的独立执行环境目录；与学习目录分别加锁')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    root = args.data_dir.resolve()
    handle = lock_data(root)
    runtime_root = (args.runtime_dir.resolve() if args.runtime_dir else
                    ROOT / 'data' / 'alpha-runtime-round35' if root == (ROOT / 'data' / 'alpha').resolve() else root)
    runtime_handle = None
    url = f'http://127.0.0.1:{args.port}'
    if not args.no_browser:
        def open_ready():
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + '/api/status', timeout=1):
                        webbrowser.open(url)
                        return
                except OSError:
                    time.sleep(.3)
        threading.Thread(target=open_ready, daemon=True).start()
    print(f'Alpha: {url}\nData: {root}', flush=True)
    try:
        if runtime_root != root:
            runtime_handle = lock_data(runtime_root)
        execution = Execution(runtime_root)
        # Only recover after acquiring exclusive ownership of this data directory.
        execution.runtime.recover()
        uvicorn.run(create_app(root, execution=execution), host='127.0.0.1', port=args.port, workers=1, log_level='info')
    finally:
        if runtime_handle is not None:
            runtime_handle.close()
        handle.close()


if __name__ == '__main__':
    main()
