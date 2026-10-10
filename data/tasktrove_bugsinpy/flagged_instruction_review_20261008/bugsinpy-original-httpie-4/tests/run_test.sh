#!/bin/bash
set -e
pytest tests/test_regressions.py::test_Host_header_overwrite
