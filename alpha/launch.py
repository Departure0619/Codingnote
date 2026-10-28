"""Double-click friendly launcher; only manages this Alpha's own child process."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8027)
    args = parser.parse_args()
    root = ROOT / 'data' / 'alpha'
    url = f'http://127.0.0.1:{args.port}'

    def ready():
        try:
            with urllib.request.urlopen(url + '/api/status', timeout=2) as r:
                s = json.load(r)
                matched = s.get('product') == 'Alpha' and Path(s.get('data_dir', '')).resolve() == root.resolve()
                if matched and (s.get('execution', {}).get('generation') != 3 or s.get('integration_phase') != 1):
                    raise SystemExit('上一轮 Alpha 服务仍在运行，请先关闭该服务再启动第 4 轮正式整合版。未自动停止其他进程。')
                return matched
        except (OSError, ValueError):
            return False

    if not ready():
        root.mkdir(parents=True, exist_ok=True)
        log = root / 'server.log'
        with log.open('ab') as output:
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'alpha', '--port', str(args.port), '--no-browser'],
                cwd=ROOT, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(80):
            if ready():
                break
            if process.poll() is not None:
                raise SystemExit(f'Alpha 启动失败，请查看 {log}；未停止其他服务。')
            time.sleep(.25)
        else:
            raise SystemExit(f'Alpha 尚未就绪，请查看 {log}。')
    print('Alpha 已启动：' + url)
    if not args.no_browser:
        webbrowser.open(url)


if __name__ == '__main__':
    main()
