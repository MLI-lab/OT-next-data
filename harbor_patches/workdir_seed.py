"""Include baked deferred layers when seeding a writable task workdir."""
from pathlib import Path
import re


def workdir_seed_command(command):
    if not (isinstance(command, list) and command[1:2] == ['exec']
            and any(str(x).endswith(':/_workdir_init:rw') for x in command)):
        return command
    image = next((Path(x) for x in command if str(x).endswith('.sif')), None)
    if image is None:
        return command
    overlay = image.with_suffix('.overlay.img')
    if not image.with_suffix('.deferred.json').exists() or not overlay.is_file():
        return command
    # Upstream's cp reads only the base SIF and silently ignores copy errors.
    # Use the effective image and preserve links/modes without foreign owners/ACLs.
    script = re.sub(r'cp -a (\S+)/\. /_workdir_init/ 2>/dev/null \|\| true;',
                    r'tar -C \1 -cf - . | tar --no-same-owner -xpf - -C /_workdir_init;', command[-1])
    return [*command[:2], '--containall', '--no-home', '--no-mount',
            'hostfs,bind-paths,cwd', '--writable-tmpfs', '--overlay', str(overlay) + ':ro',
            *command[2:-3], 'bash', '-o', 'pipefail', '-ec', script]
