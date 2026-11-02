"""Terminus sessions: terminal recovery for every run; time limit and verdict checks for judges."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from harbor_patches import terminus_session as patch


@pytest.fixture
def harbor(monkeypatch):
    pytest.importorskip('harbor')
    from harbor.agents.terminus_2.tmux_session import TmuxSession
    from harbor.agents.terminus_2.terminus_2 import Terminus2
    for cls, name in ((TmuxSession, 'send_keys_and_capture'), (TmuxSession, 'is_session_alive'),
                      (Terminus2, '_handle_llm_interaction'), (Terminus2, 'run')):
        monkeypatch.setattr(cls, name, getattr(cls, name))
    monkeypatch.setattr(Terminus2, '_ot_terminus_session', False, raising=False)
    return TmuxSession, Terminus2


class Environment:
    def __init__(self, answers=()):
        self.commands, self.answers = [], list(answers)

    async def exec(self, command, user=None, timeout_sec=None):
        self.commands.append(command)
        rc, out = self.answers.pop(0) if self.answers else (0, '')
        return SimpleNamespace(return_code=rc, stdout=out, stderr='')


def session(environment):
    return SimpleNamespace(environment=environment, _user=None, _tmux_start_session='START',
                           _session_name='terminus-2', _batch_exec_timeout_margin_sec=180.0,
                           _tmux_capture_pane=lambda capture_entire=False: 'CAPTURE', _previous_buffer='old')


def batch(keys='ls\n', duration=10.0):
    from harbor.agents.terminus_2.tmux_session import TmuxKeystrokeBatch
    return [TmuxKeystrokeBatch(keys, duration)]


def in_review(coroutine_function, *args, policy=None):
    state = patch.new_state(policy or patch.Policy(review_sec=1800, grace_sec=600))
    async def call():
        token = patch.STATE.set(state)
        try:
            return await coroutine_function(*args)
        finally:
            patch.STATE.reset(token)
    return asyncio.run(call()), state


def test_lost_terminal_restarts_in_same_container_with_note(harbor):
    TmuxSession, _ = harbor
    async def capture(self, batches):
        return 'output\n', False
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    environment = Environment()
    (output, alive), state = in_review(TmuxSession.send_keys_and_capture, session(environment), batch('exit\n'))
    assert alive and environment.commands == ['START']
    assert output == 'output\n\n' + patch.TERMINAL_RESTARTED + '\n'
    assert state['terminal_restarts'] == 1


def test_terminal_recovery_stops_after_three_consecutive_losses(harbor):
    TmuxSession, _ = harbor
    async def capture(self, batches):
        return '', False
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    target = session(Environment())
    async def four():
        return [await TmuxSession.send_keys_and_capture(target, batch()) for _ in range(4)]
    results, state = in_review(four)
    assert [alive for _, alive in results] == [True, True, True, False]
    assert state['events'][-1]['kind'] == 'terminal_recovery_exhausted'


def test_successful_command_resets_the_consecutive_count(harbor):
    TmuxSession, _ = harbor
    answers = iter([False, True, False, False, False])
    async def capture(self, batches):
        return '', next(answers)
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    target = session(Environment())
    async def five():
        return [await TmuxSession.send_keys_and_capture(target, batch()) for _ in range(5)]
    results, state = in_review(five)
    assert all(alive for _, alive in results) and state['terminal_restarts'] == 4


def test_unresponsive_terminal_is_read_again_without_interrupting(harbor):
    TmuxSession, _ = harbor
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    async def capture(self, batches):
        raise TmuxBatchProtocolError("x: batched tmux call returned no recognized status marker: "
                                     "return_code=-1, stderr='Command timed out after 190s', stdout=''")
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    environment = Environment([(0, ''), (0, 'screen')])
    (output, alive), _ = in_review(TmuxSession.send_keys_and_capture, session(environment), batch())
    assert alive and output == 'screen'
    assert environment.commands == ['tmux has-session -t terminus-2', 'CAPTURE']
    assert not any('C-c' in command for command in environment.commands)


def test_apptainer_deadline_counts_as_unresponsive_terminal(harbor):
    """Seen on 2026-11-02: Apptainer itself gave up with 255 'FATAL: context deadline exceeded'."""
    TmuxSession, _ = harbor
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    async def capture(self, batches):
        raise TmuxBatchProtocolError("x: batched tmux call returned no recognized status marker: "
                                     "return_code=255, stderr='FATAL:   context deadline exceeded\\n', stdout=''")
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    environment = Environment([(0, ''), (0, 'screen')])
    (output, alive), _ = in_review(TmuxSession.send_keys_and_capture, session(environment), batch())
    assert alive and output == 'screen'


def test_lost_unresponsive_terminal_restarts_with_its_own_note(harbor):
    TmuxSession, _ = harbor
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    async def capture(self, batches):
        raise TmuxBatchProtocolError("x: batched tmux call returned no recognized status marker: "
                                     "return_code=-1, stderr='Command timed out after 190s', stdout=''")
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    environment = Environment([(1, '')])
    (output, alive), state = in_review(TmuxSession.send_keys_and_capture, session(environment), batch())
    assert alive and environment.commands == ['tmux has-session -t terminus-2', 'START']
    assert output == ('NOTE: The terminal stopped responding for more than 190 s while your last command was '
                      'running and has been closed. A new terminal has been started in the same container. '
                      'Files written so far are kept. The working directory, environment variables, shell '
                      'options and any processes started from the old shell are gone.\n')
    assert state['terminal_restarts'] == 1


def test_other_protocol_errors_stay_fatal(harbor):
    TmuxSession, _ = harbor
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    async def capture(self, batches):
        raise TmuxBatchProtocolError('omitted its full-capture marker: return_code=0')
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    with pytest.raises(TmuxBatchProtocolError):
        in_review(TmuxSession.send_keys_and_capture, session(Environment()), batch())


def test_outside_a_terminus_run_harbor_behaviour_is_unchanged(harbor):
    TmuxSession, _ = harbor
    async def capture(self, batches):
        return 'output', False
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    environment = Environment()
    assert asyncio.run(TmuxSession.send_keys_and_capture(session(environment), batch())) == ('output', False)
    assert environment.commands == []


def interaction(complete):
    from harbor.agents.terminus_2.terminus_2 import LLMInteractionResult
    return LLMInteractionResult(commands=[], is_task_complete=complete, feedback='', analysis='', plan='',
                                response=None)


def test_time_limit_message_is_added_once_after_review_time(harbor):
    _, Terminus2 = harbor
    prompts = []
    async def interact(self, chat, prompt, *args, **kwargs):
        prompts.append(prompt)
        return interaction(False)
    Terminus2._handle_llm_interaction = interact
    patch.install()
    agent = SimpleNamespace(_pending_completion=False)
    async def turns():
        await Terminus2._handle_llm_interaction(agent, None, 'first')
        patch.STATE.get()['started'] -= 1801
        await Terminus2._handle_llm_interaction(agent, None, 'second')
        await Terminus2._handle_llm_interaction(agent, None, 'third')
    _, state = in_review(turns)
    message = patch.TIME_LIMIT.format(artifact='/app/verdicts.json', minutes='10')
    assert message == ('The review time limit has been reached. Do not investigate further. Write '
                       '/app/verdicts.json now from the evidence you have. You have 10 minutes.')
    assert prompts == ['first', 'second\n\n' + message, 'third'] and state['grace_sent']


def test_invalid_verdict_refuses_completion_once_in_the_same_session(harbor):
    _, Terminus2 = harbor
    from validation.rubrics.utils.review import verdict_validator
    async def interact(self, chat, prompt, *args, **kwargs):
        return interaction(True)
    Terminus2._handle_llm_interaction = interact
    patch.install()
    rubric = '[[criteria]]\nname = "task_quality"\ndescription = "d"\nguidance = "g"\n'
    policy = patch.Policy(review_sec=1800, grace_sec=600, validate=verdict_validator(rubric))
    wrong = json.dumps({'checks': {'anti_cheat': {'outcome': 'pass', 'explanation': 'x'}}})
    environment = Environment([(0, wrong), (0, wrong)])
    agent = SimpleNamespace(_pending_completion=True, _session=session(environment))
    async def turns():
        return [await Terminus2._handle_llm_interaction(agent, None, 'p') for _ in range(2)]
    (first, second), state = in_review(turns, policy=policy)
    assert not first.is_task_complete
    assert first.feedback == ('WARNINGS: /app/verdicts.json is not valid, so the review cannot be accepted yet: '
                              'Missing or unexpected rubric criteria: missing task_quality; unexpected anti_cheat. '
                              'Correct the file, then mark the task complete again.')
    assert second.is_task_complete and state['rejections'] == 1


def test_first_completion_request_is_not_checked(harbor):
    _, Terminus2 = harbor
    async def interact(self, chat, prompt, *args, **kwargs):
        return interaction(True)
    Terminus2._handle_llm_interaction = interact
    patch.install()
    def fail(text):
        raise ValueError('invalid')
    environment = Environment()
    agent = SimpleNamespace(_pending_completion=False, _session=session(environment))
    policy = patch.Policy(review_sec=1800, grace_sec=600, validate=fail)
    result, _ = in_review(Terminus2._handle_llm_interaction, agent, None, 'p', policy=policy)
    assert result.is_task_complete and environment.commands == []


def test_valid_verdict_is_kept_after_harbor_time_limit(tmp_path):
    from validation.rubrics.utils.review import verdict_validator
    from validation.stages.runner import accepted_after_timeout
    rubric = '[[criteria]]\nname = "task_quality"\ndescription = "d"\nguidance = "g"\n'
    path = tmp_path / 'verdicts.json'
    path.write_text(json.dumps({'checks': {'task_quality': {'outcome': 'fail', 'explanation': 'x'}}}))
    timeout = {'exception_info': {'exception_type': 'AgentTimeoutError'}}
    validate = verdict_validator(rubric)
    assert accepted_after_timeout(timeout, path, validate)
    assert not accepted_after_timeout({'exception_info': {'exception_type': 'RuntimeError'}}, path, validate)
    path.write_text('{"checks": ')
    assert not accepted_after_timeout(timeout, path, validate)


def test_solver_runs_get_terminal_recovery_but_no_judge_messages(harbor):
    """Without a review policy (solver, pass@k) the terminal is recovered; prompts are untouched."""
    TmuxSession, Terminus2 = harbor
    async def capture(self, batches):
        return 'output\n', False
    prompts = []
    async def interact(self, chat, prompt, *args, **kwargs):
        prompts.append(prompt)
        return interaction(True)
    TmuxSession.send_keys_and_capture = capture
    Terminus2._handle_llm_interaction = interact
    patch.install()
    target = session(Environment())
    agent = SimpleNamespace(_pending_completion=True, _session=target)
    async def turns():
        patch.STATE.get()['started'] -= 7200
        output, alive = await TmuxSession.send_keys_and_capture(target, batch('exit\n'))
        result = await Terminus2._handle_llm_interaction(agent, None, 'next')
        return output, alive, result
    state = patch.new_state(None)
    async def call():
        token = patch.STATE.set(state)
        try:
            return await turns()
        finally:
            patch.STATE.reset(token)
    output, alive, result = asyncio.run(call())
    assert alive and patch.TERMINAL_RESTARTED in output
    assert prompts == ['next'] and result.is_task_complete and not state['grace_sent']


@pytest.fixture
def timed_out_capture(harbor):
    TmuxSession, _ = harbor
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    async def capture(self, batches):
        raise TmuxBatchProtocolError("return_code=-1, stderr='Command timed out after 190s'")
    TmuxSession.send_keys_and_capture = capture
    patch.install()
    return TmuxSession.send_keys_and_capture


class ScriptedEnvironment(Environment):
    async def exec(self, command, user=None, timeout_sec=None):
        self.commands.append(command)
        assert timeout_sec == 60
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        rc, out, err = answer
        return SimpleNamespace(return_code=rc, stdout=out, stderr=err)


def test_unresponsive_container_and_screen_are_polled(timed_out_capture, monkeypatch):
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(patch.asyncio, 'sleep', sleep)
    environment = ScriptedEnvironment([
        (-1, '', 'Command timed out after 60s'), TimeoutError(),
        (0, '', ''), (-1, '', 'Command timed out after 60s'),
        (0, '', ''), (0, 'recovered', ''),
    ])
    (output, alive), state = in_review(timed_out_capture, session(environment), batch())
    assert (output, alive) == ('recovered', True)
    assert sleeps == [30, 30, 30]
    assert environment.commands == ['tmux has-session -t terminus-2'] * 3 + [
        'CAPTURE', 'tmux has-session -t terminus-2', 'CAPTURE']
    assert state['consecutive'] == state['terminal_restarts'] == 0
    assert state['events'][-1]['kind'] == 'screen_read_again'


def test_screen_recovery_has_no_consecutive_cap(timed_out_capture):
    environment = Environment([(0, ''), (0, 'screen')] * 5)
    async def recover():
        patch.STATE.get()['consecutive'] = patch.MAX_CONSECUTIVE
        return [await timed_out_capture(session(environment), batch()) for _ in range(5)]
    results, state = in_review(recover)
    assert results == [('screen', True)] * 5
    assert state['consecutive'] == patch.MAX_CONSECUTIVE
    assert state['terminal_restarts'] == 0


def test_missing_session_after_wait_reports_elapsed_time(timed_out_capture, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(patch.time, 'monotonic', lambda: now[0])
    async def sleep(seconds):
        now[0] += seconds
    monkeypatch.setattr(patch.asyncio, 'sleep', sleep)
    environment = ScriptedEnvironment([TimeoutError(), (1, '', 'no server running'), (0, '', '')])
    (output, alive), state = in_review(timed_out_capture, session(environment), batch())
    assert alive and 'more than 220 s' in output
    assert environment.commands[-1] == 'START' and state['terminal_restarts'] == 1


@pytest.mark.parametrize('during', ['exec', 'sleep'])
def test_hard_agent_cancellation_propagates(timed_out_capture, monkeypatch, during):
    async def sleep(seconds):
        raise asyncio.CancelledError()
    monkeypatch.setattr(patch.asyncio, 'sleep', sleep)
    environment = ScriptedEnvironment([
        asyncio.CancelledError() if during == 'exec' else TimeoutError()
    ])
    with pytest.raises(asyncio.CancelledError):
        in_review(timed_out_capture, session(environment), batch())
    assert environment.commands == ['tmux has-session -t terminus-2']


def test_unexpected_probe_error_does_not_restart(timed_out_capture):
    from harbor.agents.terminus_2.tmux_session import TmuxBatchProtocolError
    environment = ScriptedEnvironment([(2, '', 'permission denied')])
    with pytest.raises(TmuxBatchProtocolError):
        in_review(timed_out_capture, session(environment), batch())
    assert environment.commands == ['tmux has-session -t terminus-2']
