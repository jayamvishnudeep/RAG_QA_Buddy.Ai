import dataclasses
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from qabuddy.loaders import LoadContext  # noqa: E402
from qabuddy.settings import get_settings  # noqa: E402
from qabuddy.sources import Chunking, Source  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=FIXTURES, storage_dir=tmp_path / "storage")


@pytest.fixture
def make_ctx(settings):
    """Build a LoadContext for a fixture file, as ingestion would."""

    def build(rel_path: str, loader: str, max_tokens: int = 600, overlap: int = 60, key: str = "test", min_tokens: int = 0):
        path = FIXTURES / rel_path
        source = Source(key, key.title(), path.parent, loader, 1, Chunking(max_tokens, overlap, min_tokens))
        chunkings = {"documents": Chunking(600, 90, 60)}
        return LoadContext(source, path, rel_path, path.name, settings, None, chunkings)

    return build
