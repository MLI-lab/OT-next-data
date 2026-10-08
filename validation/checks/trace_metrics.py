"""Summarize recorded costs, tools, termination reasons and errors from agent trials."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re

from validation.checks.reward_metrics import group_of, trial_reward
from validation.checks.command_metrics import profile, mean_distribution

PARSE_ERROR = 'Previous response had parsing errors'
OUTPUT_CAP = 'NONE of the actions you just requested were performed'
# Heuristic: common failure messages in what a tool printed.
ERROR_TEXT = re.compile(r'command not found|No such file or directory|Permission denied|'
                        r'Traceback \(most recent call last\)|[Ss]yntax error|Segmentation fault|'
                        r'ModuleNotFoundError|cannot access|Killed')
TERMINATIONS = {
    'AgentTimeoutError': 'task_timeout', 'TrialTimeoutError': 'task_timeout',
    'TurnCapExhaustedError': 'turn_limit',
    'ContextLengthExceededError': 'context_limit', 'ContextBudgetExceededError': 'context_limit',
    'OutputLengthExceededError': 'output_cap',
    'TaskMemoryLimitError': 'task_memory_limit',
}
ERRORS = {
    'model_server': ('LLMRequestTimeoutError', 'OpenAITransportConnectTimeoutError', 'ModelAuthenticationError',
        'AuthenticationError', 'APIError', 'APIConnectionError', 'APITimeoutError', 'Timeout', 'RateLimitError',
        'InternalServerError', 'ServiceUnavailableError', 'BadGatewayError', 'ContextManagementInfrastructureError'),
    'environment': ('EnvironmentStartTimeoutError', 'AgentSetupTimeoutError', 'SandboxBuildFailedError',
        'HealthcheckError', 'BridgeOutageError', 'TmuxSessionEndedError', 'TmuxCommandError',
        'TmuxBatchProtocolError', 'AgentKilledBySignalError', 'NonZeroAgentExitCodeError'),
    'verifier': ('VerifierTimeoutError', 'VerifierOutputParseError', 'VerifierRuntimeError', 'RewardFileNotFoundError',
        'RewardFileEmptyError', 'AddTestsDirError', 'DownloadVerifierDirError', 'VerificationNotCompletedError',
        'TrialNotScoredError'),
}


def seconds(timing):
    try:
        start, end = (datetime.fromisoformat(timing[key].replace('Z', '+00:00')) for key in ('started_at', 'finished_at'))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return (end - start).total_seconds()


def distribution(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    # Nearest-rank percentiles: every reported value was actually observed.
    rank = lambda p: values[max(0, -(-p * len(values) // 100) - 1)]
    return {'n': len(values), 'mean': sum(values) / len(values), 'median': rank(50),
            'p90': rank(90), 'p95': rank(95), 'max': values[-1]}


def trial_metrics(trial, result, context_limit=None, key='reward'):
    agent = result.get('agent_result') or {}
    metadata = agent.get('metadata') or {}
    path = Path(trial) / 'agent/trajectory.json'
    try:
        steps = json.loads(path.read_text()).get('steps', [])
    except (OSError, ValueError):
        steps = None
    agent_steps = [s for s in steps or [] if s.get('source') == 'agent']
    tools, failures, judged = Counter(), Counter(), Counter()
    tool_steps = no_output = error_text = 0
    for step in agent_steps:
        failed = (step.get('extra') or {}).get('tool_result_is_error')
        if step.get('tool_calls'):
            output = ' '.join(str(r.get('content') or '') for r in (step.get('observation') or {}).get('results') or [])
            output = output.replace('New Terminal Output:', '').strip()
            tool_steps += 1
            no_output += not output
            error_text += bool(ERROR_TEXT.search(output))
        for call in step.get('tool_calls') or []:
            name = call.get('function_name', 'unknown')
            tools[name] += 1
            if failed is not None:
                judged[name] += 1
                failures[name] += bool(failed)
    text = [json.dumps(s.get('observation')) + json.dumps(s.get('message')) for s in steps or []]
    peak = max((m.get('prompt_tokens', 0) + m.get('completion_tokens', 0)
                for m in (s.get('metrics') or {} for s in agent_steps)), default=None)
    kwargs = ((result.get('config') or {}).get('agent') or {}).get('kwargs') or {}
    limit = (kwargs.get('model_info') or {}).get('max_input_tokens') or context_limit
    exceptions = [e.get('exception_type') for e in [result.get('exception_info'), *(result.get('secondary_exception_info') or [])] if e]
    stop = metadata.get('stop_reason')
    termination = next((TERMINATIONS[e] for e in exceptions if e in TERMINATIONS), None)
    if termination is None:
        termination = ('turn_limit' if stop == 'turn_cap_exhausted' else 'task_complete' if stop == 'task_complete'
                       else 'error' if exceptions else stop or 'not_recorded')
    errors = {kind: [e for e in exceptions if e in names] for kind, names in ERRORS.items()}
    known = set(TERMINATIONS) | {name for names in ERRORS.values() for name in names}
    errors['other'] = [e for e in exceptions if e not in known]
    reward = trial_reward(result, key)
    if reward is None and not exceptions:
        errors['verifier'].append('no reward and no recorded exception')
    return {
        'trial': Path(trial).parent.parent.name if Path(trial).parent.name == 'attempts' else Path(trial).name,
        'trial_path': str(Path(trial).resolve()),
        'reward': reward, 'trajectory_found': steps is not None,
        'turns': metadata.get('n_episodes') or (len(agent_steps) if steps is not None else None),
        'input_tokens': agent.get('n_input_tokens'), 'output_tokens': agent.get('n_output_tokens'),
        'cached_tokens': agent.get('n_cache_tokens'),
        'peak_context_tokens': peak, 'context_limit': limit,
        'peak_context_fraction': peak / limit if peak and limit else None,
        'context_summarizations': metadata.get('summarization_count'),
        'oom_recoveries': metadata.get('oom_recoveries'),
        'model_call_seconds': [ms / 1000 for ms in metadata.get('api_request_times_msec') or []] or None,
        'agent_seconds': seconds(result.get('agent_execution')), 'verifier_seconds': seconds(result.get('verifier')),
        'trial_seconds': seconds(result),
        'tool_steps': tool_steps, 'tool_steps_without_output': no_output, 'tool_steps_with_error_text': error_text,
        'tool_calls': dict(tools), 'tool_failures': dict(failures), 'tool_calls_with_known_outcome': dict(judged),
        'command_profile': profile(steps),
        'termination': termination,
        'output_cap_events': sum(OUTPUT_CAP in t for t in text) if steps is not None else None,
        'malformed_tool_calls': sum(PARSE_ERROR in t for t in text) if steps is not None else None,
        'errors': errors,
    }


def aggregate(rows, wall_hours=None):
    def total(key):
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) if values else None
    tools, failures, judged = Counter(), Counter(), Counter()
    for row in rows:
        tools.update(row['tool_calls']); failures.update(row['tool_failures']); judged.update(row['tool_calls_with_known_outcome'])
    solved = sum(r['reward'] is not None and r['reward'] >= 1 for r in rows)
    graded = sum(r['reward'] is not None for r in rows)
    result = {
        'trajectories': len(rows), 'graded': graded, 'solved': solved,
        'turns': distribution(r['turns'] for r in rows),
        'input_tokens': distribution(r['input_tokens'] for r in rows), 'input_tokens_total': total('input_tokens'),
        'output_tokens': distribution(r['output_tokens'] for r in rows), 'output_tokens_total': total('output_tokens'),
        'peak_context_tokens': distribution(r['peak_context_tokens'] for r in rows),
        'peak_context_fraction': distribution(r['peak_context_fraction'] for r in rows),
        'trajectories_above_90_percent_context': sum((r['peak_context_fraction'] or 0) > 0.9 for r in rows),
        'latency_seconds': {
            'model_call': distribution(v for r in rows for v in r['model_call_seconds'] or []),
            'tool_call': None,  # not recorded in result.json or ATIF by the pinned agents
            'agent': distribution(r['agent_seconds'] for r in rows),
            'verifier': distribution(r['verifier_seconds'] for r in rows),
            'trial': distribution(r['trial_seconds'] for r in rows)},
        'tools': {name: {'calls': count, 'calls_per_trajectory': count / len(rows),
                         'failed': failures[name] if judged[name] else None,
                         'failure_rate': failures[name] / judged[name] if judged[name] else None}
                  for name, count in sorted(tools.items())},
        'tool_steps': total('tool_steps'), 'tool_steps_without_output': total('tool_steps_without_output'),
        'tool_steps_with_error_text': total('tool_steps_with_error_text'),
        'tool_steps_with_error_text_rate': total('tool_steps_with_error_text') / total('tool_steps') if total('tool_steps') else None,
        'terminations': dict(sorted(Counter(r['termination'] for r in rows).items())),
        'command_distribution': mean_distribution(r.get('command_profile') for r in rows),
        'output_cap_events': total('output_cap_events'),
        'oom_recoveries': sum(r.get('oom_recoveries') or 0 for r in rows),
        'trajectories_with_oom_recovery': sum(bool(r.get('oom_recoveries')) for r in rows),
        'malformed_tool_calls': total('malformed_tool_calls'),
        'trajectories_with_malformed_tool_calls': sum(bool(r['malformed_tool_calls']) for r in rows),
        'errors': {kind: {'trajectories': sum(bool(r['errors'][kind]) for r in rows),
                          'types': dict(sorted(Counter(e for r in rows for e in r['errors'][kind]).items()))}
                   for kind in (*ERRORS, 'other')},
    }
    if wall_hours:
        result['throughput'] = {'wall_clock_hours': wall_hours, 'trajectories_per_hour': graded / wall_hours,
                                'solved_trajectories_per_hour': solved / wall_hours}
    return result


def gpu_peaks(log):
    """Peaks from `nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu`."""
    peaks = {}
    try:
        lines = Path(log).read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            index, used, total, util = (float(v) for v in line.split(','))
        except ValueError:
            continue
        gpu = peaks.setdefault(str(int(index)), {'peak_memory_mib': 0, 'memory_total_mib': total, 'peak_utilization_percent': 0,
                                                 'samples': 0, 'memory': 0, 'utilization': 0})
        gpu.update(peak_memory_mib=max(gpu['peak_memory_mib'], used), memory=gpu['memory'] + used,
                   peak_utilization_percent=max(gpu['peak_utilization_percent'], util),
                   utilization=gpu['utilization'] + util, samples=gpu['samples'] + 1)
    for gpu in peaks.values():
        gpu['mean_memory_mib'] = gpu.pop('memory') / gpu['samples']
        gpu['mean_utilization_percent'] = gpu.pop('utilization') / gpu['samples']
    return peaks or None


def server_requests(log):
    """HTTP status of every model request in the local server's access log, including retried ones."""
    try:
        lines = Path(log).read_text(errors='replace').splitlines()
    except OSError:
        return None
    status = Counter(m.group(1) for m in (re.search(r'"POST /v1/(?:chat/)?(?:completions|messages|responses)[^"]*" (\d{3})', line)
                                          for line in lines) if m)
    if not status:
        return None
    failed = sum(count for code, count in status.items() if code >= '400')
    return {'requests': sum(status.values()), 'failed': failed, 'failure_rate': failed / sum(status.values()),
            'by_status': dict(sorted(status.items()))}


def summarize(results, task_of, context_limit=None, key='reward', gpu_log=None, server_log=None):
    """`results` are Harbor (trial directory, result.json) pairs; `task_of` names each trial's task."""
    rows = []
    for trial, result in results:
        row = trial_metrics(trial, result, context_limit, key)
        row['task'] = task_of(trial, result)
        rows.append(row)
    if not rows:
        return None
    def parse(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
    starts = [t for t in (parse(r.get('started_at')) for _, r in results) if t]
    ends = [t for t in (parse(r.get('finished_at')) for _, r in results) if t]
    hours = (max(ends) - min(starts)).total_seconds() / 3600 if starts and ends else None
    tasks, families = {}, {}
    for row in rows:
        tasks.setdefault(row['task'], []).append(row)
        families.setdefault(group_of(row['task']), []).append(row)
    summary = {'all_tasks': aggregate(rows, hours),
               'families': {name: aggregate(group) for name, group in sorted(families.items())},
               'tasks': {name: aggregate(group) for name, group in sorted(tasks.items())}}
    # Saved per-trajectory rows keep a latency summary instead of every call time.
    for row in rows:
        row['model_call_seconds'] = distribution(row['model_call_seconds'] or [])
    return {**summary, 'trajectories': rows, 'reward_key': key,
            'model_server_requests': server_requests(server_log) if server_log else None,
            'inference_resources': {'gpus': gpu_peaks(gpu_log) if gpu_log else None,
                'scope': 'GPU memory and utilization sampled every 10 s while the local model was served, idle time included'}}
