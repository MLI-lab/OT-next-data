"""Backport vLLM PRs 57477 and 57317 into a job-local 0.30 runtime, never a shared venv."""
import hashlib
import os
from pathlib import Path
import sysconfig


def patched_source(source):
    begin = source.index('def _kpool_tail_seed_kernel(')
    end = source.index('# Update each request', begin)
    block = source[begin:end]
    if 'base = blk * TAIL_BLOCK_ELEMS + (t % KPOOL) * HEAD_DIM' in block:
        assert 'TAIL_BLOCK_ELEMS=tail_kv_cache.stride(0)' in block
        assert 'KPOOL_HEAD=tail_kv_cache.stride(1)' in block
        return source
    edits = [
        ('    n_tokens,\n    HEAD_DIM: tl.constexpr,', '    n_tokens,\n    TAIL_BLOCK_ELEMS: tl.constexpr,\n    KPOOL_HEAD: tl.constexpr,\n    HEAD_DIM: tl.constexpr,'),
        ('    base = (blk * 2 * KPOOL + t % KPOOL) * HEAD_DIM', '    base = blk * TAIL_BLOCK_ELEMS + (t % KPOOL) * HEAD_DIM'),
        ('    tl.store(tail_ptr + base + KPOOL * HEAD_DIM + offs, s, mask=m)', '    tl.store(tail_ptr + base + KPOOL_HEAD + offs, s, mask=m)'),
        ('    assert tail_kv_cache.dtype == torch.bfloat16', '    assert tail_kv_cache.dtype == torch.bfloat16\n    assert tail_kv_cache.ndim == 4 and tail_kv_cache.shape[1] == 2\n    assert tail_kv_cache.stride(3) == 1 and tail_kv_cache.stride(2) == head_dim'),
        ('        n,\n        HEAD_DIM=head_dim,', '        n,\n        TAIL_BLOCK_ELEMS=tail_kv_cache.stride(0),\n        KPOOL_HEAD=tail_kv_cache.stride(1),\n        HEAD_DIM=head_dim,'),
    ]
    for old, new in edits:
        if block.count(old) != 1:
            raise RuntimeError('Unexpected vLLM kpool source; refusing an unverified backport')
        block = block.replace(old, new)
    return source[:begin] + block + source[end:]


def apply():
    path = Path(sysconfig.get_paths()['purelib']) / 'vllm/models/glm5next/nvidia/ops/kpool_compress.py'
    scratch = Path(os.environ['TMPDIR']).resolve()
    if not path.resolve().is_relative_to(scratch):
        raise RuntimeError(f'Refusing to modify non-job-local runtime: {path}')
    original = path.read_text()
    fixed = patched_source(original)
    if fixed != original:
        backup = path.with_suffix('.py.before-57477')
        if backup.exists() and backup.read_text() != original:
            raise RuntimeError('Conflicting kpool backup')
        backup.write_text(original)
        pending = path.with_suffix('.repair-tmp')
        pending.write_text(fixed)
        pending.replace(path)
    slot_path = Path(sysconfig.get_paths()['purelib']) / 'vllm/v1/kv_cache_interface.py'
    if not slot_path.resolve().is_relative_to(scratch):
        raise RuntimeError(f'Refusing to modify non-job-local runtime: {slot_path}')
    slot_original = slot_path.read_text()
    slot_fixed = patched_slot_source(slot_original)
    if slot_fixed != slot_original:
        backup = slot_path.with_suffix('.py.before-57317')
        if backup.exists() and backup.read_text() != slot_original:
            raise RuntimeError('Conflicting slot-mapping backup')
        backup.write_text(slot_original)
        pending = slot_path.with_suffix('.repair-tmp')
        pending.write_text(slot_fixed)
        pending.replace(slot_path)
    return {'patch': 'https://github.com/vllm-project/vllm/pull/57477', 'path': str(path),
            'slot_mapping_fix': {'patch': 'https://github.com/vllm-project/vllm/pull/57317',
                'path': str(slot_path), 'changed': slot_fixed != slot_original,
                'before_sha256': hashlib.sha256(slot_original.encode()).hexdigest(),
                'after_sha256': hashlib.sha256(slot_fixed.encode()).hexdigest()},
            'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(fixed.encode()).hexdigest(), 'changed': original != fixed}


def patched_slot_source(source):
    begin = source.index('class KpoolTailSpec(')
    end = source.index('@dataclass(frozen=True)\nclass MambaSpec(', begin)
    block = source[begin:end]
    new = '    @property\n    def uses_slot_mapping(self) -> bool:\n        return False\n'
    if 'def uses_slot_mapping(' in block:
        if new not in block:
            raise RuntimeError('Unexpected tail slot-mapping policy; refusing backport')
        return source
    assert 'def prefix_cacheable(self) -> bool:' in block
    block = block.rstrip() + '\n\n' + new + '\n\n'
    return source[:begin] + block + source[end:]
