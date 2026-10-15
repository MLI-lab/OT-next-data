"""Restore the exception assertion export omitted by legacy SymPy pytest mode."""


def pytest_configure(config):
    import pytest
    from sympy.utilities import pytest as helper

    if not hasattr(helper, 'raises'):
        helper.raises = pytest.raises
