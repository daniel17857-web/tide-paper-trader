import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


@pytest.fixture
def fixtures_dir() -> str:
    return FIXTURES
