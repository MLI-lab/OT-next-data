#!/bin/bash
set -eo pipefail
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-pip
pip3 install --break-system-packages --quiet \
    pytest==8.4.1 \
    pytest-json-ctrf==0.3.5
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set."
    exit 1
fi
