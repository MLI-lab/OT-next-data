"""Explicit simulated mode restricts the experiment, not the task's online policy."""
import asyncio
from types import SimpleNamespace

import pytest

from validation.rules.dynamic.environment.check import inspect_network


@pytest.mark.parametrize('declared', [None, False, True])
@pytest.mark.parametrize('return_code,status', [(0, 'failed'), (1, 'passed'), (125, 'error')])
def test_simulated_mode_checks_blocked_egress_without_claiming_isolation(monkeypatch, declared, return_code, status):
    monkeypatch.setenv('OT_NET_ISOLATION', '0')
    monkeypatch.setenv('OT_NET_SIMULATED', '1')
    class Environment:
        async def exec(self, command, timeout_sec):
            return SimpleNamespace(return_code=return_code, stdout='', stderr='probe result')
    result = asyncio.run(inspect_network(Environment(), declared))
    assert result['status'] == status
    assert result['declared_allow_internet'] is declared
    assert result['expected_allow_internet'] is False
    assert result['expectation_source'] == 'explicit-simulated-run'
    if status != 'error':
        assert 'routing is not isolated' in result['reason']
