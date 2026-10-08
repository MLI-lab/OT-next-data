"""Early-stop registry, shared with dedicated task executors."""
from pathlib import Path

def test_a_stop_that_arrives_before_the_start_finishes_is_not_lost():
    """The worker forgets a stop for a container it does not know yet; the registry remembers it."""
    import importlib.util, threading, time
    spec = importlib.util.spec_from_file_location('trial_step_registry', Path(__file__).resolve().parents[1] / 'harbor_patches/trial_step.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    registry = module.InstanceRegistry()
    stopped = threading.Event()
    class Instance:
        def stop(self, payload):
            stopped.set()
    # Normal order: registered, then stopped by the worker itself.
    registry['env-a'] = Instance()
    assert registry.pop('env-a', None) is not None and not registry.abandoned
    # The trial gave up first: the stop finds nothing, the late start must not stay registered.
    assert registry.pop('env-b', None) is None and 'env-b' in registry.abandoned
    registry['env-b'] = Instance()
    assert stopped.wait(5) and 'env-b' not in registry and 'env-b' not in registry.abandoned
    for n in range(registry.KEEP + 5):
        registry.pop(f'gone-{n}', None)
    assert len(registry.abandoned) == registry.KEEP
