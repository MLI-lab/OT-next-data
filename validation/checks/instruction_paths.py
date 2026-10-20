"""Automatic, evidence-backed instruction edits before contract hashing."""
import re

from data.utils.resolve_absolute_paths import collect_reports, apply_reviewed_replacements, preceding_absolute_paths


def automatic(instruction, diagnostic, oracle_passed):
    baseline = diagnostic.get('agent_baseline', {})
    phases = [('after-setup', baseline)]
    phases += [('after-reference', r) for r in diagnostic.get('observations', [])
               if r.get('observation_phase') == 'after-reference-solution-before-verifier']
    candidates = {}
    observed_matches = {}
    for phase, observation in phases:
        if observation.get('status') != 'completed':
            continue
        for finding in observation.get('findings', []):
            relative = finding['relative_path']
            hints = preceding_absolute_paths(instruction, [relative])
            if not set(hints).issubset(observation.get('instruction_path_hints', [])):
                continue  # Older evidence did not search the instruction's hints.
            observed_matches.setdefault(relative, set()).update(finding.get('matches', []))
            if finding.get('status') == 'unique' and len(finding.get('matches', [])) == 1:
                # ./ explicitly refers to the working directory. A unique copy
                # elsewhere may be an old build artifact, not the program the
                # agent is asked to build. Do not redirect execution to it.
                if relative.startswith('./') and not finding.get('resolves_from_start', False):
                    continue
                candidates.setdefault(finding['relative_path'], (phase, finding['matches'][0]))
    # Keep fenced code unchanged; prose examples use the same unique-match
    # rule as other prose.
    excluded = [(m.start(), m.end()) for m in re.finditer(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*', instruction)]
    decisions = []
    for relative, (phase, absolute) in candidates.items():
        if observed_matches[relative] != {absolute}:
            continue
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
        # A discovery run on its own prepared copy inspects the task after the other
        # automatic fixes (timeout sentence, repository repair). Its evidence is just as
        # current; the caller records that prepared hash here.
        self.prepared = {}
        self.report = {'enabled': enabled, 'evidence_reports': list(map(str, reports)), 'tasks': []}

    def apply(self, task_id, instruction, prepared_instruction=None):
        """Edit `instruction`, or `prepared_instruction` (the text with the other automatic
        fixes) when the evidence was gathered on the prepared copy."""
        entry = self.evidence.get(task_id)
        if not entry:
            return instruction
        selected = entry['selected']
        diagnostic = selected['diagnostic']
        record = {'task': task_id, 'oracle_status': selected['status'], 'edits': []}
        self.report['tasks'].append(record)
        seen = diagnostic.get('agent_baseline', {}).get('task_sha256')
        if seen != self.hashes[task_id]:
            if seen != self.prepared.get(task_id) or prepared_instruction is None:
                record['warning'] = 'stale-or-missing-task-evidence'
                return instruction
            instruction = prepared_instruction
        try:
            normalized, edits = automatic(instruction.decode(), diagnostic, selected['status'] == 'passed')
        except ValueError as exc:
            record['warning'] = str(exc)
            return instruction
        record['edits'] = edits
        return normalized.encode()
