#!/bin/sh
set -eu
cd /work
export PYTHONPATH=/work
mkdir -p /work/.pod
exec python -m pytest -q -p no:cacheprovider -o addopts="" --rootdir=/work --timeout="${POD_TEST_TIMEOUT:-10}" --timeout-method=signal --junitxml=/work/.pod/junit.xml -o junit_family=xunit1 "$@"
