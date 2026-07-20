#!/bin/sh
set -eu

verify_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
verify_python=${MERV_VERIFY_PYTHON:-python3}
catalog_expected=45e46fac9ea0a4d97fa12d1fc9b111e1088f862992288ffca01a735b70ee2420
brain_loc_baseline=39924
tracking_slice_baseline=40850
brain_loc_max=41000
surface_orchestration_max=400

cd "$verify_dir"
brain_loc=$(find src/merv/brain -name '*.py' | xargs wc -l | tail -1 | awk '{print $1}')
test "$((brain_loc_max - brain_loc_baseline))" -eq 1076
test "$((brain_loc_max - tracking_slice_baseline))" -eq 150
test "$brain_loc" -le "$brain_loc_max"
test "$(wc -l < src/merv/brain/mlflow/exhibit.py)" -le 15
test "$(wc -l < src/merv/brain/surface/tools/tool_handlers.py)" \
  -le "$surface_orchestration_max"
"$verify_python" -m pytest tests/structure -q
"$verify_python" scripts/regen_tool_catalog.py --check
catalog_actual=$(shasum -a 256 src/merv/proxy/_tool_catalog.json | awk '{print $1}')
test "$catalog_actual" = "$catalog_expected"
MERV_REQUIRE_POSTGRES_TESTS=1 \
  "$verify_python" -m pytest tests/state/test_postgres_dialect.py -q
"$verify_python" -m pytest -q
