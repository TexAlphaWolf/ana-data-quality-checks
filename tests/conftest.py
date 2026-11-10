from __future__ import annotations

import os
from pathlib import Path

import pytest

from dqcheck import gen

ROWS = int(os.environ.get("DQCHECK_TEST_ROWS", "4000"))  # other sizes and seeds: a wider check
SEED = int(os.environ.get("DQCHECK_TEST_SEED", "7"))


@pytest.fixture(scope="session")
def generated() -> gen.Generated:
    return gen.generate(ROWS, SEED, 0.01)


@pytest.fixture(scope="session", params=["parquet", "csv"])
def data_dir(
    request: pytest.FixtureRequest, generated: gen.Generated, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    folder = tmp_path_factory.mktemp(f"data_{request.param}")
    gen.write(generated, folder, request.param)
    return folder
