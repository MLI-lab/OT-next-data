"""Stage-1 path lookup: where the relative paths of instruction.md really are.

The task-absolute-path check flags relative paths in stage 0; stage 1 records
the observed locations in its own container, and the repair uses them.
"""


async def capture_build_paths(environment, task, args, entry):
    """Collect path evidence in the prepared stage-1 container.

    A failed lookup belongs to the task-absolute-path check, not to the build verdict.
    """
    roots = getattr(args, 'resolve_path_root', None)
    automatic = getattr(args, '_collect_stage1_paths', False)
    if not roots and not automatic:
        return
    try:
        from validation.rules.static.task_absolute_path.repair import flagged_paths, resolve_in_environment
        from validation.rules.dynamic.do_not_modify.check import protected_artifact_matches
        protected = protected_artifact_matches((task / 'instruction.md').read_text(errors='replace'))
        if automatic and not roots and not flagged_paths(task)[0] and not protected:
            return
        entry['instruction_paths'] = await resolve_in_environment(environment, task, roots or ['auto'])
        entry['instruction_paths']['reference_unavailable'] = not (task / 'solution/solve.sh').is_file()
        if protected:
            if entry['instruction_paths'].get('healthcheck', {}).get('status') in {'passed', 'not-configured'}:
                from validation.rules.dynamic.do_not_modify.protected_paths import observe_original_paths
                entry['protected_paths'] = await observe_original_paths(environment, task)
            else:
                entry['protected_paths'] = {'status': 'error', 'error': 'original agent healthcheck not completed'}
    except Exception as exc:
        entry['instruction_paths'] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
