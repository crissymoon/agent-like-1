"""A portable reading of the agent benchmark.

The benchmark, its task suite and its scoring were written once, for one
runtime. This package is the same suite and the same arithmetic in a form that
runs anywhere, so the same numbers can be produced on a laptop and on a hosted
notebook and set beside each other.

Nothing here knows what hardware it is on, and nothing here names a machine, a
memory limit or a weight file. It measures a solver against a suite, and the
suite is data.
"""
