from sympy import symbols, S, asin, sqrt, pi, Piecewise, simplify
from sympy.stats import Arcsin, cdf
x,a,b = symbols('x a b', real=True)
formula = 2*asin(sqrt((x-a)/(b-a)))/pi
expected = Piecewise((0,x<a),(formula,x<=b),(1,True))
actual = cdf(Arcsin('A',a,b))(x)
print('actual:',actual)
print('test expects:',expected)
print('structural equality:',actual==expected)
print('formula at the only changed boundary x=a:',simplify(formula.subs(x,a)))
assert simplify(formula.subs(x,a)) == 0
# The branches differ only at x=a, where both return exactly zero for a<b.
for lo,hi in [(-5,9),(0,3),(-2,-1)]:
 for point in [lo-1,lo,S(lo+hi)/2,hi,hi+1]:
  av=actual.subs({a:lo,b:hi}).subs(x,point)
  ev=expected.subs({a:lo,b:hi}).subs(x,point)
  assert simplify(av-ev)==0, (lo,hi,point,av,ev)
print('Boundary, interior and exterior checks: PASS')
