#!/bin/bash
set -eo pipefail
if ! command -v uv &> /dev/null; then
    apt-get update && apt-get install -y curl
    (set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/0.10.11/install.sh | sh) || exit $?
fi
source $HOME/.local/bin/env
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set."
    exit 1
fi
uvx \
  -p 3.13 \
  -w pytest==8.4.1 \
  -w pytest-json-ctrf==0.3.5 \
  pytest --version
