"""Cache identity for explicit Dockerfile-only build contexts."""
import hashlib
import re
from pathlib import Path


def dockerfile_only_context(dockerfile, ignore):
    rules = [line.strip() for line in ignore.decode().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    return rules == ['*', '!Dockerfile'] and not re.search(
        rb'^\s*(?:ONBUILD\s+)?(?:COPY|ADD)\s|--mount\b', dockerfile, re.I | re.M)


def environment_dir_hash(env_dir):
    from harbor.utils.container_cache import environment_dir_hash as full_context_hash
    env_dir = Path(env_dir)
    ignore = env_dir / '.dockerignore'
    # This deliberately supports only the exact, explicit Dockerfile-only form.
    # Other contexts retain the existing conservative hash of every input file.
    if not ignore.is_file() or not (env_dir / 'Dockerfile').is_file():
        return full_context_hash(env_dir)
    content = (env_dir / 'Dockerfile').read_bytes()
    # The Apptainer COPY adapter does not implement Docker ignore matching.
    # Preserve full-context identity whenever a recipe can consume local files.
    if not dockerfile_only_context(content, ignore.read_bytes()):
        return full_context_hash(env_dir)
    name = b'Dockerfile'
    return hashlib.sha256(len(name).to_bytes(4, 'big') + name
                          + len(content).to_bytes(4, 'big') + content).hexdigest()


def environment_dir_hash_truncated(env_dir, truncate=12):
    return environment_dir_hash(env_dir)[:truncate]
