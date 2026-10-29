"""Outcome-separated token measurements from saved ATIF trajectories."""
from collections import defaultdict
import json
import math
from pathlib import Path

from validation.metrics.trace_metrics import distribution

OUTCOMES = ('Successful', 'Unsuccessful (graded)', 'No verifier score')
LABELS = {
    'step_output_tokens': 'Output tokens per recorded agent step',
    'step_prompt_tokens': 'Prompt tokens per recorded agent step',
    'step_context_tokens': 'Prompt + output tokens per recorded agent step',
    'output_tokens': 'Total generated output per attempt',
    'input_plus_output_tokens': 'Cumulative input + output usage per attempt',
    'context_summarizations': 'Summarizations per attempt',
}


def valid(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def decode(value):
    return json.loads(value) if isinstance(value, str) else value or {}


class OutcomeStats:
    def __init__(self):
        self.context_groups = defaultdict(lambda: defaultdict(list))
        self.groups = defaultdict(lambda: {name: {'attempts': 0, 'values': defaultdict(list),
                                                 'missing': defaultdict(int)} for name in OUTCOMES})

    def add(self, row):
        result = decode(row.get('result'))
        reward = row.get('verifier_reward')
        if reward is None:
            reward = ((result.get('verifier_result') or {}).get('rewards') or {}).get('reward')
        known = type(reward) in (int, float) and math.isfinite(reward)
        outcome = OUTCOMES[0] if known and reward >= 1 else OUTCOMES[1] if known else OUTCOMES[2]
        agent = result.get('agent_result') or {}
        values = defaultdict(list)
        missing = defaultdict(int)
        for step in decode(row.get('trajectory')).get('steps', []):
            if step.get('source') != 'agent':
                continue
            metrics = step.get('metrics') or {}
            prompt, output = metrics.get('prompt_tokens'), metrics.get('completion_tokens')
            for key, value in [('step_output_tokens', output), ('step_prompt_tokens', prompt),
                               ('step_context_tokens', prompt + output if valid(prompt) and valid(output) else None)]:
                if valid(value):
                    values[key].append(value)
                else:
                    missing[key] += 1
        prompt, output = agent.get('n_input_tokens'), agent.get('n_output_tokens')
        for key, value in [('output_tokens', output),
                           ('input_plus_output_tokens', prompt + output if valid(prompt) and valid(output) else None),
                           ('context_summarizations', (agent.get('metadata') or {}).get('summarization_count'))]:
            if valid(value):
                values[key].append(value)
            else:
                missing[key] += 1
        for model in ('all-models', row['model']):
            count = (agent.get('metadata') or {}).get('summarization_count')
            bucket = 'Unknown summary count' if not valid(count) else 'With summarization' if count > 0 else 'Without summarization'
            peak = max(values['step_context_tokens'], default=None)
            self.context_groups[model][bucket].append(peak)
            group = self.groups[model][outcome]
            group['attempts'] += 1
            for key in LABELS:
                group['values'][key].extend(values[key])
                group['missing'][key] += missing[key]

    def write(self, out):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        from validation.publishing.trace_stats import slug
        out = Path(out)
        (out / 'data').mkdir(parents=True, exist_ok=True)
        (out / 'plots').mkdir(exist_ok=True)
        summary = {'scope': 'All deduplicated published attempts, including kept and excluded tasks.',
                   'outcome_definition': 'Raw verifier reward >= 1 is successful; lower finite scores are graded unsuccessful; missing/nonfinite scores are ungraded.',
                   'measurement_notes': 'Step distributions weight each recorded agent step equally. ATIF step usage may aggregate multiple requests, so these are not guaranteed per-call measurements. Context means recorded prompt plus completion tokens. Attempt output includes reported auxiliary generation. Cumulative input counts repeated history again and is not unique rollout length. Missing measurements are excluded, never replaced by zero. Summary counts are recorded events, not a measure of their effectiveness.',
                   'models': {}}
        for model, groups in self.groups.items():
            stats = {}
            fig, axes = plt.subplots(1, 3, figsize=(16, 4.8), layout='constrained')
            summary_counts = [v for group in groups.values() for v in group['values']['context_summarizations']]
            panels = [
                ('Output tokens per recorded agent step',
                 {name: group['values']['step_output_tokens'] for name, group in groups.items()}),
                ('Summarizations per trace', {'All traces with recorded counts': summary_counts}),
                ('Peak recorded context per trace', self.context_groups[model]),
            ]
            for i, (ax, (label, series)) in enumerate(zip(axes, panels)):
                for (name, values), color in zip(series.items(), ('#167d8d', '#b34b30', '#777777')):
                    v = np.sort([x for x in values if x is not None])
                    if len(v):
                        ax.step(v, 100 * np.arange(1, len(v) + 1) / len(v), where='post', color=color,
                                label=f'{name} (n={len(v):,})')
                ax.set_title(label, fontsize=10)
                if i != 1:
                    ax.set_xscale('symlog', linthresh=100)
                ax.set_xlabel('Summary count' if i == 1 else 'Tokens (log scale above 100)')
                ax.set_ylabel('Measurements at or below value (%)')
                ax.set_ylim(0, 101)
                ax.grid(alpha=.2)
                ax.legend(fontsize=7, loc='lower right')
            for name, group in groups.items():
                stats[name] = {'attempts': group['attempts'],
                               'metrics': {key: distribution(group['values'][key]) for key in LABELS},
                               'missing_measurements': dict(group['missing']),
                               'attempts_with_summaries': sum(v > 0 for v in group['values']['context_summarizations'])}
            stats['all_traces_summary_counts'] = distribution(summary_counts)
            stats['peak_context_by_summarization'] = {
                name: {'distribution': distribution(values), 'missing': sum(v is None for v in values)}
                for name, values in self.context_groups[model].items()}
            summary['models'][model] = stats
            fig.suptitle(f'Output, summarization, and context: {model}')
            for ext in ('png', 'pdf'):
                fig.savefig(out / 'plots' / f'{slug(model)}-tokens-by-outcome.{ext}', dpi=150)
            plt.close(fig)
        (out / 'data/tokens-by-outcome.json').write_text(json.dumps(summary, indent=2) + '\n')
        return summary


def readme_section():
    return '''## Token usage and summarization

All published traces are included, covering kept and excluded tasks and every verifier outcome. One trace means one task attempt.

![Output, summarization, and context distributions](plots/all-models-tokens-by-outcome.png)

- **Output per recorded agent step:** successful, graded unsuccessful, and ungraded attempts shown separately. Each step contributes once.
- **Summarizations per trace:** one combined distribution across all traces with a recorded count, including zero.
- **Peak context per trace:** the largest recorded step prompt + output count in each trace, separated into traces with and without summarization. Unknown summary counts are kept separate when present. Each trace contributes once, regardless of its length or outcome.

[Detailed statistics](data/tokens-by-outcome.json) retain sample counts and missing measurements. Missing summary counts are not zero. ATIF steps can aggregate multiple requests, so recorded context does not establish an exact per-call context limit.
'''
