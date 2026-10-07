# 0147: the test's unfilled marker is never passed to `scatter`

The intended rule is: an unfilled marker gets its edge color from `c`; a filled marker can keep the supplied `edgecolors`.

The test's `marker_obj` is an unused local variable. [Scatter](setup_files/solution.py) also uses the name `marker_obj`, but that is a **different local variable** inside `scatter`; sharing a name does not transfer the object. `scatter` creates its own `Marker()`, which defaults to `filled=True`. The call has `c='red'` and `edgecolors='blue'`. Under the intended rule, this filled marker's edge should stay blue. The test expects red, as though it had passed the unused unfilled marker. The filled-marker test also creates a local marker without passing it.

An agent could pass the current tests by special-casing their color inputs—for example, returning red for the red/blue call while retaining orange for the green/orange call. That would satisfy the assertions without determining whether a marker is filled. To test the intended fix, the tests would need to pass their marker objects to `scatter`, and `scatter` would need to accept and use them.
