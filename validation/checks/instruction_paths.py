"""Automatic, evidence-backed instruction edits before contract hashing."""
import re

from data.utils.resolve_absolute_paths import collect_reports, apply_reviewed_replacements


def automatic(instruction, diagnostic, oracle_passed):
    baseline = diagnostic.get('agent_baseline', {})
    phases = [('after-setup', baseline)]
    phases += [('after-reference', r) for r in diagnostic.get('observations', [])
               if r.get('observation_phase') == 'after-reference-solution-before-verifier']
    candidates = {}
    for phase, observation in phases:
        if observation.get('status') != 'completed':
            continue
        for finding in observation.get('findings', []):
            if finding.get('status') == 'unique' and len(finding.get('matches', [])) == 1:
                candidates.setdefault(finding['relative_path'], (phase, finding['matches'][0]))
    # Keep fenced code unchanged; prose examples use the same unique-match
    # rule as other prose.
    excluded = [(m.start(), m.end()) for m in re.finditer(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*', instruction)]
    decisions = []
    for relative, (phase, absolute) in candidates.items():
        occurrences = []
        for match in re.finditer(r'(?<![A-Za-z0-9_./+~\-])' + re.escape(relative) + r'(?![A-Za-z0-9_./+~\-])', instruction):
            start, end = match.span()
            if any(a <= start < b for a, b in excluded):
                continue
            occurrences.append({'start': start, 'end': end})
        if occurrences:
            decisions.append({'action': 'replace', 'reason': 'automatic unique observed path',
                              'phase': phase, 'relative_path': relative, 'absolute_path': absolute,
                              'occurrences': occurrences})
    return apply_reviewed_replacements(instruction, diagnostic, decisions, oracle_passed=oracle_passed,
                                       require_passing_oracle=False)


class Normalizer:
    def __init__(self, args, records):
        enabled = getattr(args, 'fix_instruction_paths', True)
        reports = getattr(args, 'path_resolution_report', []) if enabled else []
        self.evidence = collect_reports(reports)
        self.hashes = {r['task_id']: r['sha256'] for r in records}
        self.report = {'enabled': enabled, 'evidence_reports': list(map(str, reports)), 'tasks': []}

    def apply(self, task_id, instruction):
        entry = self.evidence.get(task_id)
        if not entry:
            return instruction
        selected = entry['selected']
        diagnostic = selected['diagnostic']
        record = {'task': task_id, 'oracle_status': selected['status'], 'edits': []}
        self.report['tasks'].append(record)
        if diagnostic.get('agent_baseline', {}).get('task_sha256') != self.hashes[task_id]:
            record['warning'] = 'stale-or-missing-task-evidence'
            return instruction
        try:
            normalized, edits = automatic(instruction.decode(), diagnostic, selected['status'] == 'passed')
        except ValueError as exc:
            record['warning'] = str(exc)
            return instruction
        record['edits'] = edits
        return normalized.encode()
