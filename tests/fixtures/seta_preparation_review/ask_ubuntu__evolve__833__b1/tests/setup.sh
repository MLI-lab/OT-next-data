#!/bin/bash
set -eo pipefail
apt-get update
apt-get install -y python3 python3-pip curl 2>&1
(set -o pipefail; curl --fail --retry 3 --retry-delay 2 --connect-timeout 30 --max-time 180 --retry-max-time 180 -LsSf https://astral.sh/uv/install.sh | sh 2>&1) || exit $?
export PATH="$HOME/.local/bin:$PATH"
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set."
    exit 1
fi
if ! command -v uvx >/dev/null 2>&1; then
    pip3 install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 2>&1
fi
if command -v uvx >/dev/null 2>&1; then
uvx \
      --with pytest==8.4.1 \
      --with pytest-json-ctrf==0.3.5 \
      pytest --version
fi
