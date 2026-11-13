import asyncio
import json
from pathlib import Path
import shlex
import shutil
import subprocess
from types import SimpleNamespace

from validation import probe_python


def test_private_python_with_clean_environment_and_cleanup():
    class Local:
        async def exec(self, command, timeout_sec):
            r = subprocess.run(['bash', '-c', command], capture_output=True, text=True, timeout=timeout_sec,
                               env={'PATH': '/usr/bin:/bin', 'PYTHONHOME': '/does/not/exist'})
            return SimpleNamespace(return_code=r.returncode, stdout=r.stdout, stderr=r.stderr)
        async def upload_file(self, source_path, target_path):
            shutil.copyfile(source_path, target_path)
    async def exercise():
        async with probe_python.staged(Local()) as (command, digest):
            code = 'import json,sys,tarfile,hashlib; print(json.dumps({"prefix":sys.prefix,"ok":True}))'
            result = await Local().exec(command + ' -c ' + shlex.quote(code), 20)
            assert result.return_code == 0, result.stderr
            data = json.loads(result.stdout)
            assert data['ok'] and '/.ot-python-probe-' in data['prefix']
            prefix = Path(data['prefix'])
            assert len(digest) == 64 and prefix.is_dir()
        assert not prefix.exists()
    asyncio.run(exercise())
