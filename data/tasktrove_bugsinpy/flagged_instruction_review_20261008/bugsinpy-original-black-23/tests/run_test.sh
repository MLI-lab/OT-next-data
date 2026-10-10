#!/bin/bash
set -e
python -m unittest -q tests.test_black.BlackTestCase.test_python2
