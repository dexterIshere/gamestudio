"""Visual effects: a spec written in a computation language, rendered as a sheet.

`spec` reads the YAML, `expr` evaluates the layers' code, `noise` provides the
noises, `sim` runs fluids and particles, `render` composites and assembles.
No material is hard-coded here: fire, water or magic are specs (skill `vfx`).
"""
