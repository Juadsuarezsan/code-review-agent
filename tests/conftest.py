import random
import pytest

SEED = 20260516


@pytest.fixture(autouse=True)
def _seed():
    random.seed(SEED)
