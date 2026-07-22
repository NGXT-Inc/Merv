#!/bin/sh
set -eu

verify_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
verify_python=${MERV_VERIFY_PYTHON:-python}
catalog_expected=45e46fac9ea0a4d97fa12d1fc9b111e1088f862992288ffca01a735b70ee2420

cd "$verify_dir"
test ! -e src/merv/brain/mlflow/exhibit.py
"$verify_python" -m pytest tests/structure -q
"$verify_python" scripts/regen_tool_catalog.py --check
catalog_actual=$(shasum -a 256 src/merv/proxy/_tool_catalog.json | awk '{print $1}')
test "$catalog_actual" = "$catalog_expected"
MERV_REQUIRE_POSTGRES_TESTS=1 \
  "$verify_python" -m pytest tests/state/test_postgres_dialect.py -q
"$verify_python" -m pytest -q
