#!/bin/sh
set -eu

verify_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
verify_python=${MERV_VERIFY_PYTHON:-python}

cd "$verify_dir"
test ! -e src/merv/brain/mlflow/exhibit.py
"$verify_python" -m pytest tests/structure -q
MERV_REQUIRE_POSTGRES_TESTS=1 \
  "$verify_python" -m pytest tests/state/test_postgres_dialect.py -q
"$verify_python" -m pytest -q
