"""Keep literal task environment values out of Apptainer's CLI map parser."""
import shlex
from pathlib import Path


def environment_arguments(cmd, kwargs):
    if (not isinstance(cmd, list) or len(cmd) < 3 or cmd[1] != 'exec'
            or Path(cmd[0]).name not in ('apptainer', 'singularity')):
        return cmd, kwargs
    boundary = next((i for i, arg in enumerate(cmd) if str(arg).startswith('instance://')), None)
    if boundary is None:
        return cmd, kwargs
    result, assignments, index = [], [], 0
    while index < boundary:
        arg = cmd[index]
        if arg == '--env' and index + 1 < boundary:
            key, separator, value = cmd[index + 1].partition('=')
            # Harbor serializes exec payload values with shlex.quote. Apptainer
            # would split them at commas and keep the quotes of quoted values
            # (e.g. values with spaces). Undo exactly that encoding, then use
            # argv-based env inside the container. No CLI CSV parsing or extra
            # shell evaluation. Harbor's built-in PS1 and PROMPT_COMMAND
            # arguments are raw, unlike task payload values. Leave those raw
            # arguments unchanged; special characters alone do not imply quoting.
            quoted = separator and value.startswith("'")
            if separator and (',' in value or quoted):
                try:
                    decoded = shlex.split(value)
                except ValueError:
                    decoded = []
                if len(decoded) == 1 and shlex.quote(decoded[0]) == value:
                    assignments.append(key + '=' + decoded[0])
                    index += 2
                    continue
                if ',' in value:
                    raise ValueError('Unexpected Harbor environment value encoding')
        result.append(arg)
        index += 1
    if not assignments:
        return cmd, kwargs
    return [*result, cmd[boundary], '/usr/bin/env', '--', *assignments, *cmd[boundary + 1:]], kwargs
