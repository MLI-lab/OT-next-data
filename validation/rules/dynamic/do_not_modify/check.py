"""do_not_modify, dynamic part (on by default).

1. Extract protected files from the instruction (rules/static/do_not_modify/extract.py).
2. Check that they exist in the original agent environment (protected_paths.py, during stage 1).
3. A file that exists and that the verifier never mentions keeps the flag; mutations are skipped.
4. Otherwise, when a reference solution exists, mutate each protected file after the reference ran and
   check that the verifier then fails (mutations.py). Without a reference this step is not checked,
   never passed.
"""
import hashlib
from pathlib import Path

from validation.rules.static.do_not_modify.extract import extract


def protected_artifact_matches(instruction):
    return [dict(m, matched_text=m['keyword'], context=m['instruction_line'])
            for m in extract(instruction)['matches']]


def protected_task_digest(task):
    """Same content-addressed file digest as validation.contract.task_digest."""
    digest = hashlib.sha256()
    for path in sorted((p for p in task.rglob('*') if p.is_file()),
                       key=lambda p: p.relative_to(task).as_posix()):
        name = path.relative_to(task).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big')); digest.update(name)
        digest.update(len(content).to_bytes(8, 'big')); digest.update(content)
    return digest.hexdigest()


def check(task, original_environment=None):
    instruction=(task/"instruction.md").read_text(errors="replace") if (task/"instruction.md").exists() else ""
    selection = extract(instruction)
    protected = list(dict.fromkeys(m['path'] for m in selection['matches']))
    def selected_result(status, reason, evidence):
        return dict(status=status, reason=reason, evidence=evidence,
                    extraction_version='protected-phrases-v1',
                    protected_targets=selection['matches'],
                    unresolved_targets=selection['unresolved'],
                    enforcement_status=(selection['enforcement_status'] if not protected
                                        else 'incomplete' if status == 'incomplete'
                                        else 'skipped_missing_original_path' if status == 'not_evidenced'
                                        else 'mention_scan_only'))
    if not protected:
        reason = ('no concrete protected filename found; enforcement test skipped'
                  if selection['unresolved'] else 'no protected artifact constraint found')
        return selected_result('not_evidenced', reason, [])
    baseline = original_environment
    if not isinstance(baseline, dict):
        return selected_result("incomplete", "original-agent-environment path evidence required", protected)
    if (baseline.get('schema') != 'protected-paths-v1' or baseline.get('status') != 'completed'
            or baseline.get('observation_phase') != 'after-task-setup-and-healthcheck-before-agent'
            or baseline.get('environment') != 'agent'
            or baseline.get('task_sha256') != protected_task_digest(task)):
        return selected_result("incomplete", "missing, stale, failed, or non-original environment evidence", protected)
    observations = {f['candidate']: f for f in baseline.get('findings', [])}
    unresolved = [p for p in protected if p not in observations or observations[p].get('status') not in {'exists', 'missing', 'ambiguous', 'unsupported'}]
    if unresolved:
        return selected_result("incomplete", "original environment evidence does not cover all candidates", unresolved)
    confirmed = [p for p in protected if observations[p].get('status') == 'exists'
                 and len(observations[p].get('matches', [])) == 1
                 and observations[p]['matches'][0].startswith('/')]
    malformed = [p for p in protected if observations[p].get('status') == 'exists' and p not in confirmed]
    if malformed:
        return selected_result("incomplete", "invalid original-environment path evidence", malformed)
    if not confirmed:
        return selected_result("not_evidenced", "no protected candidate resolves to an existing original-environment path", protected)
    tests="\n".join(p.read_text(errors="replace") for p in (task/"tests").rglob("*") if p.is_file() and p.stat().st_size<5_000_000) if (task/"tests").exists() else ""
    missing=[p for p in confirmed if p not in tests and Path(p).name not in tests
             and observations[p]['matches'][0] not in tests]
    return selected_result("fail" if missing else "pass", "existing original-artifact verifier-mention scan (not proof of integrity enforcement)", missing)
