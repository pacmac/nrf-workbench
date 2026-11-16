"""guards: the safety core every hardware-touching path goes through. See docs/specs/programmer.md.

Each check returns None when it passes, or a refused Result naming what it found. A refusal means
nothing was attempted. The guards hold the rules once, so USB flashing and SWD cannot diverge.
"""
