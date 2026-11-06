from django.db.backends.ddl_references import IndexColumns
actual = str(IndexColumns('t', ['headline'], lambda x: '"%s"' % x, col_suffixes=[''], opclasses=['text_pattern_ops']))
expected = '"headline" text_pattern_ops'
print('actual:', repr(actual), 'expected:', repr(expected), 'bug_remains:', actual != expected)
assert actual != expected
