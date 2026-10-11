"""Keep PyTrakt's unrelated current-date formatting test deterministic."""
import datetime
import sys


class VerifierDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        # PyTrakt 0.16.0 pads months using `month > 10`; October therefore
        # becomes "010". November avoids that unrelated boundary while keeping
        # the task's existing two-digit date-format assertion meaningful.
        return cls(2026, 11, 5, tzinfo=tz)


def pytest_collection_modifyitems(session, config, items):
    module = sys.modules.get('trakt.utils')
    if module is None:
        raise RuntimeError('PyTrakt did not import trakt.utils')
    module.datetime = VerifierDatetime
