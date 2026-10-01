"""Launch a service script with startup logging and on-demand stack traces."""
import faulthandler
import runpy
import signal
import sys
from pathlib import Path


if __name__ == '__main__':
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR2, all_threads=True)
    sys.argv = sys.argv[1:]
    # Match `python target.py`: do not let this launcher's adjacent harbor.py
    # shadow the installed Harbor package imported by the bridge worker.
    sys.path[0] = str(Path(sys.argv[0]).resolve().parent)
    print(f'Starting service script: {sys.argv[0]}', flush=True)
    runpy.run_path(sys.argv[0], run_name='__main__')
