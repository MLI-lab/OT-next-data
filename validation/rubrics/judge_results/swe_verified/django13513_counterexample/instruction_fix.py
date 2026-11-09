from pathlib import Path
p = Path('/testbed/django/views/debug.py')
s = p.read_text(encoding="utf-8")
old = """            explicit = getattr(exc_value, '__cause__', None)
            suppress_context = getattr(exc_value, '__suppress_context__', None)
            implicit = getattr(exc_value, '__context__', None)
            return explicit or (None if suppress_context else implicit)"""
new = """            return (
                exc_value.__cause__ or
                (None if exc_value.__suppress_context__ else
                 exc_value.__context__)
            )"""
assert s.count(old) == 1
p.write_text(s.replace(old, new), encoding="utf-8")
