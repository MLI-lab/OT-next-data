from pathlib import Path
p = Path('/testbed/sympy/stats/crv_types.py')
s = p.read_text()
start = s.index('class ArcsinDistribution(')
end = s.index('def Arcsin(', start)
part = s[start:end]
assert part.count('(S.Zero, x < a)') == 1
part = part.replace('(S.Zero, x < a)', '(S.Zero, x <= a)')
p.write_text(s[:start] + part + s[end:])
