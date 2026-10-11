"""Keep SDSS #69's release-date regression test before DR19 becomes public."""
import datetime
import sys
from types import SimpleNamespace


class VerifierDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2025, 4, 1, tzinfo=tz)


def pytest_collection_modifyitems(session, config, items):
    # Tests import this module during collection. Replace only its datetime
    # reference, leaving the process clock and other dependencies untouched.
    module = sys.modules.get('sdss_access.path.path')
    if module is None:
        raise RuntimeError('SDSS verifier did not import sdss_access.path.path')
    clock = SimpleNamespace(**{name: getattr(datetime, name)
                              for name in dir(datetime) if not name.startswith('__')})
    clock.datetime = VerifierDatetime
    module.datetime = clock
