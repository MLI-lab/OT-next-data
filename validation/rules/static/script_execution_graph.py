"""Read supplied import/subprocess helpers without importing or executing them.

This is an over-approximation: scanning a function body does not prove it runs.
Only /tests and literal Docker COPY mappings establish runtime file identity.
"""
from pathlib import Path, PurePosixPath
import shlex
import sys

from validation.rules.dynamic.verifier_commands import supplied_helper


class Sources:
    def __init__(self, task, initial, problems):
        self.task = task
        self.queue = list(initial)
        self.seen = {source for source, _, _ in initial}
        self.problems = problems
        self.edges = []
        self.executions = []
        self.runtime = {}
        self.search_roots = ['/tests']
        dockerfile = task / 'environment/Dockerfile'
        if dockerfile.is_file():
            for line in dockerfile.read_text().splitlines():
                words = line.split()
                if len(words) == 2 and words[0].upper() == 'WORKDIR' and words[1].startswith('/') and '$' not in words[1]:
                    self.search_roots.append(words[1])
        for source, _, _ in initial:
            if source.startswith('tests/'):
                self.runtime[source] = '/' + source

    def follow_path(self, path, source, line, cwd=None):
        local = supplied_helper(path, self.task / 'tests', cwd)
        if local is None:
            return None
        label = local.relative_to(self.task.resolve()).as_posix()
        runtime = str(PurePosixPath(cwd) / path) if cwd and not path.startswith('/') else path
        self.runtime[label] = runtime
        self.edges.append(dict(source=source, line=line, target=label, runtime_path=runtime))
        if label not in self.seen:
            self.seen.add(label)
            if len(self.seen) > 256:
                self.problems.append('supplied-helper traversal limit reached')
            elif local.suffix == '.py':
                self.queue.append((label, local.read_text(errors='replace'), 0))
            elif local.suffix in ('.sh', '.bash'):
                # Follow literal interpreter/direct script commands in supplied
                # wrappers. Complex shell semantics remain unknown.
                self.problems.append(f'{label}: shell wrapper may contain unresolved execution')
                for number, statement in enumerate(local.read_text(errors='replace').splitlines(), 1):
                    try:
                        words = shlex.split(statement, comments=True)
                    except ValueError:
                        continue
                    if not words:
                        continue
                    command = words[0]
                    if command in ('bash', 'sh', 'python', 'python3') and len(words) > 1:
                        command = words[1]
                    if command.endswith(('.py', '.sh', '.bash')) and '$' not in command:
                        working = cwd or (self.search_roots[-1] if len(self.search_roots) > 1 else None)
                        target = str(PurePosixPath(working) / command) if working else command
                        self.executions.append(dict(path=target, kind='execution', file=label, line=number))
                        self.follow_path(command, label, number, working)
            else:
                self.problems.append(f'{label}: unsupported helper language')
        return runtime

    def follow_module(self, module, source, line, level=0):
        if not module and not level:
            return None
        current = self.runtime.get(source)
        roots = list(self.search_roots)
        if current:
            parent = PurePosixPath(current).parent
            if level:
                for _ in range(level - 1):
                    parent = parent.parent
                roots = [str(parent)]
            else:
                roots.insert(0, str(parent))
        relative = module.replace('.', '/')
        candidates = []
        for root in dict.fromkeys(roots):
            base = str(PurePosixPath(root) / relative)
            for path in (base + '.py', base + '/__init__.py'):
                local = supplied_helper(path, self.task / 'tests')
                if local is not None:
                    candidates.append(path)
        # Ambiguous resolutions are not proof that a particular file ran.
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) == 1:
            path = candidates[0]
            # Importing pkg.child also executes supplied package initializers.
            root = path[:-(len(relative) + (12 if path.endswith('/__init__.py') else 3))] if relative else ''
            for i in range(1, len(module.split('.'))):
                package = str(PurePosixPath(root) / '/'.join(module.split('.')[:i]) / '__init__.py')
                self.follow_path(package, source, line)
            return self.follow_path(path, source, line)
        if len(candidates) > 1:
            self.problems.append(f'{source}:{line}: ambiguous module {module}')
        elif module.split('.')[0] not in sys.stdlib_module_names | {'pytest', 'requests', '__future__'}:
            self.problems.append(f'{source}:{line}: import chain unavailable for {module}')
        return None
