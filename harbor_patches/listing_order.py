"""Shuffle Python listings during extra reference runs, without changing task files."""
from pathlib import Path
import shlex
from uuid import uuid4

from harbor_patches.verifier_setup import SHUFFLE_LISTING


class ShuffledEnvironment:
    """Delegate environment operations; inject the Python hook into oracle commands."""
    def __init__(self, environment, hook):
        self.environment, self.hook = environment, hook

    def __getattr__(self, name):
        return getattr(self.environment, name)

    async def exec(self, command, *args, **kwargs):
        prefix = ('export OT_SHUFFLE_LISTING=1; export PYTHONPATH=' + shlex.quote(self.hook)
                  + '${PYTHONPATH:+:$PYTHONPATH}; ')
        return await self.environment.exec(prefix + command, *args, **kwargs)


def install():
    from harbor.agents.oracle import OracleAgent
    if getattr(OracleAgent, '_ot_listing_shuffle', False):
        return
    original = OracleAgent.run

    async def run(self, instruction, environment, context):
        if not SHUFFLE_LISTING.get():
            return await original(self, instruction, environment, context)
        hook = '/tmp/ot-reference-listing-' + uuid4().hex
        result = await environment.exec('mkdir -p ' + shlex.quote(hook), user='root')
        if result.return_code:
            raise RuntimeError('Could not prepare reference listing-order hook')
        try:
            await environment.upload_file(source_path=Path(__file__).with_name('sitecustomize.py'),
                                          target_path=hook + '/sitecustomize.py')
            result = await environment.exec('chmod -R a+rX ' + shlex.quote(hook), user='root')
            if result.return_code:
                raise RuntimeError('Could not make reference listing-order hook readable')
            return await original(self, instruction, ShuffledEnvironment(environment, hook), context)
        finally:
            await environment.exec('rm -rf -- ' + shlex.quote(hook), user='root')

    OracleAgent.run = run
    OracleAgent._ot_listing_shuffle = True
