"""Bound Rope's autoimport cache workers without changing indexed inputs."""
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import multiprocessing


def pytest_configure(config):
    from rope.contrib.autoimport import sqlite

    if sqlite.ProcessPoolExecutor is not ProcessPoolExecutor:
        raise RuntimeError('Unexpected Rope autoimport process pool')
    sqlite.ProcessPoolExecutor = partial(
        ProcessPoolExecutor, max_workers=2,
        mp_context=multiprocessing.get_context('spawn'))
