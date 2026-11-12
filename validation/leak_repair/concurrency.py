"""Requested repair-validation parallelism; allocation resource caps still apply."""

MIN_CONCURRENCY = 32
DEFAULT_CONCURRENCY = 32


def requested(value=None, task_count=None):
    value = DEFAULT_CONCURRENCY if value is None else int(value)
    if value < 1:
        raise ValueError('repair validation concurrency must be positive')
    limit = max(MIN_CONCURRENCY, value)
    return min(limit, task_count) if task_count is not None and task_count > 0 else limit


def validation_args(arguments, task_count=None):
    """Forward one normalized concurrency flag to the actual validation runner."""
    args, out, value = list(arguments), [], None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--concurrency':
            i += 1
            if i == len(args):
                raise ValueError('--concurrency requires a value')
            value = args[i]
        elif arg.startswith('--concurrency='):
            value = arg.split('=', 1)[1]
        else:
            out.append(arg)
        i += 1
    return [*out, '--concurrency', str(requested(value, task_count))]
