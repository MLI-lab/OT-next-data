"""Multi-SWE's exact-image Go verifier toolchain preparation and activation."""
import json
import os
from pathlib import Path
import re

from data.utils.verifier_runtime import (
    GO_ACTIVATION, preparation_command, toolchain_recipe_comment, toolchain_reference,
)
from harbor_patches.verifier_bundle import cache_root
from hpc.offline_prep.image_toolchain import build, canonical


def adapt(files, record):
    spec = canonical(record['spec'])
    result = {**files, **toolchain_reference(spec, record)}
    setup = files['tests/setup.sh'].decode()
    match = re.search(r'^# OT_RUNTIME_RECIPE: (.+)$', setup, re.M)
    if match is None:
        raise ValueError('Missing Multi-SWE Python runtime recipe')
    python_spec = json.loads(match[1])
    before = preparation_command(python_spec, phase='verifier')
    if setup.count(before) != 1 or '# OT_TOOLCHAIN_RECIPE:' in setup:
        raise ValueError('Unreviewed Multi-SWE verifier preparation')
    after = preparation_command(python_spec, phase='verifier', exit_after=False)
    payload = json.dumps(spec, sort_keys=True, separators=(',', ':'))
    after += ('\n' + toolchain_recipe_comment(spec) + '\n'
              'if [ "${1:-}" = "--prepare" ]; then\n'
              '  python3 -m data.multiswe.native_runtime --reference '
              '"$(dirname -- "$0")/ot-verifier-toolchain.json" <<\'OT_MULTISWE_GO_JSON\'\n'
              + payload + '\nOT_MULTISWE_GO_JSON\n  exit $?\nfi')
    result['tests/setup.sh'] = setup.replace(before, after, 1).encode()
    test = files['tests/test.sh'].decode()
    paths = re.findall(r'^export PATH=.+$', test, re.M)
    if len(paths) != 1 or 'OT_VERIFIER_GOROOT' in test:
        raise ValueError('Unreviewed Multi-SWE verifier PATH')
    result['tests/test.sh'] = test.replace(paths[0], paths[0] + '\n' + GO_ACTIVATION, 1).encode()
    return result


def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    args = parser.parse_args()
    spec = canonical(json.load(sys.stdin))
    record = build(spec, os.environ.get('OT_RUNTIME_PREPARE_IMAGE'), cache_root())
    args.reference.write_bytes(toolchain_reference(spec, record)['tests/ot-verifier-toolchain.json'])
    print(json.dumps(record))


if __name__ == '__main__':
    main()
