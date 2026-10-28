import io
import json
import unittest
from unittest.mock import patch
from alpha import launch


class LauncherTests(unittest.TestCase):
    def status(self, phase=None):
        result = {'product': 'Alpha', 'execution': {'generation': 3},
                  'data_dir': str(launch.ROOT / 'data' / 'alpha')}
        if phase is not None:
            result['integration_phase'] = phase
        return result

    def test_previous_generation_three_service_cannot_mask_round_four(self):
        with patch('sys.argv', ['alpha.launch', '--no-browser']), \
             patch('alpha.launch.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(self.status()).encode())), \
             patch('alpha.launch.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(SystemExit, '第 4 轮'):
                launch.main()
            spawn.assert_not_called()

    def test_current_integrated_service_reused_without_spawning(self):
        with patch('sys.argv', ['alpha.launch', '--no-browser']), \
             patch('alpha.launch.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(self.status(1)).encode())), \
             patch('alpha.launch.subprocess.Popen') as spawn:
            launch.main()
            spawn.assert_not_called()
