"""Keep the reviewed apt repair scoped and preserve non-Dockerfile payloads."""
import io
import tarfile

import pytest

from data.calibforge import patch
from validation.rules.static.terminal_bench import dockerfile_sanity_text


DOCKERFILE = r"""FROM ubuntu:24.04
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential=12.10ubuntu1 \
    gdb=15.0.50.20240403-0ubuntu1 \
    valgrind=1:3.22.0-0ubuntu3 \
    && rm -rf /var/lib/apt/lists/*
"""


def test_reviewed_tools_unpinned_without_changing_task_payload():
    payload = {'environment/Dockerfile': DOCKERFILE.encode(),
               'instruction.md': b'Debug process_log.c with make.\n',
               'environment/files/process_log.c': b'int main(void) { return 1; }\n'}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in payload.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    repaired, changes, modified = patch.patch_task(patch.APT_TOOL_PIN_TASK, stream.getvalue())
    assert modified
    with tarfile.open(fileobj=io.BytesIO(repaired)) as archive:
        result = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}
    assert 'R18' in changes
    for name in payload.keys() - {'environment/Dockerfile'}:
        assert result[name] == payload[name]
    assert 'pinned-package' not in dockerfile_sanity_text(result['environment/Dockerfile'].decode())
    new, pins = patch.unpin_reviewed_apt_tools(patch.APT_TOOL_PIN_TASK, DOCKERFILE)
    assert len(pins) == 3
    assert patch.unpin_reviewed_apt_tools(patch.APT_TOOL_PIN_TASK, new) == (new, [])


def test_reviewed_apt_repair_rejects_drift_and_leaves_other_tasks_alone():
    assert patch.unpin_reviewed_apt_tools('other', DOCKERFILE) == (DOCKERFILE, [])
    with pytest.raises(ValueError, match='stanza changed'):
        patch.unpin_reviewed_apt_tools(patch.APT_TOOL_PIN_TASK, 'RUN apt-get install gdb=99\n')
