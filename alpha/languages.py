"""One reviewed, immutable-at-runtime registry shared with the container image."""
import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).with_name('native_image') / 'languages.json'
LANGUAGE_CONFIG = json.loads(REGISTRY_PATH.read_text(encoding='utf8'))
LANGUAGES = tuple(LANGUAGE_CONFIG)


def public_metadata(language):
    value = LANGUAGE_CONFIG[language]
    return {key: value[key] for key in ('display_name', 'runtime_version', 'entry_note', 'filename')}
