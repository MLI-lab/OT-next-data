"""Bound only the optimizer test module's fixture worker pool."""
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import multiprocessing


def pytest_collection_modifyitems(session, config, items):
    modules = {item.module for item in items}
    matched = False
    for module in modules:
        if module.__name__ == 'tests.test_optimizer':
            if module.ProcessPoolExecutor is not ProcessPoolExecutor:
                raise RuntimeError('Unexpected optimizer fixture process pool')
            module.ProcessPoolExecutor = partial(
                ProcessPoolExecutor, max_workers=2,
                mp_context=multiprocessing.get_context('spawn'))
            matched = True
    if not matched:
        raise RuntimeError('Optimizer verifier tests were not collected')
