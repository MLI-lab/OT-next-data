"""Reviewed packages that are both the task subject and a pytest dependency."""
SUBJECTS = {
    'agronholm/anyio': ('anyio', '/testbed/src', {
        '72baca901c599e7fdf22c2777b9b6a304ddd975d', '99ec00e67c533d4b12d2a2aff8cb6aa2b0f0344f',
        'b429c1a3202dd3eff2eaad0124b327b3cad15b01', '6c70118450b97b6088046d5e71f6a90836ddbc04',
        '4b3de9737672df67b691f38543427e4869639f45', '9f5f14b3eb57f6965fc2c16879df93263bb020ea',
        'bc962eff735025481f40c475928ea25a96c53275', '93a57466c3b226ef2bc6fb2f0d361ed9ce8f707c'}),
    'jaraco/zipp': ('zipp', '/testbed', {
        '20ef7ddda408cee9822fa7d68954963a799e7a50', '4715556d5cad2cc106dd808eb5ce66fbada07434'}),
    'pytest-dev/pytest-xdist': ('xdist', '/testbed/src', {'7f2426b11d2c7890e5637fdaa3aa1c75b4f31758'}),
}


def reviewed(metadata):
    entry = SUBJECTS.get(metadata.get('repo'))
    if not entry:
        return None
    module, directory, commits = entry
    if metadata.get('base_commit') not in commits:
        raise ValueError('Unreviewed grading-dependency task revision')
    return {'module': module, 'directory': directory,
            'reason': 'Intentional task edits must remain visible, including this package\'s own pytest plugin.'}


def bootstrap(entry):
    """Expose only the subject namespace; other grading tools remain clean."""
    return ('import importlib.machinery\n'
            'class _TaskSubjectFinder:\n'
            '    def find_spec(self, fullname, path=None, target=None):\n'
            '        if fullname != ' + repr(entry['module']) + ': return None\n'
            '        spec = importlib.machinery.PathFinder.find_spec(fullname, [' + repr(entry['directory']) + '])\n'
            '        if spec is None: raise ImportError("Task subject is missing: " + fullname)\n'
            '        return spec\n'
            '# Replace only the intentionally editable subject, including in private child interpreters.\n'
            'for name in list(sys.modules):\n'
            '    if name == ' + repr(entry['module']) + ' or name.startswith(' + repr(entry['module'] + '.') + '):\n'
            '        del sys.modules[name]\n'
            'sys.meta_path.insert(0, _TaskSubjectFinder())\n')
