"""Blender render adapter (industrial design visualisation).

MetaForge code in this package never imports ``bpy``. Everything that runs
inside Blender is in ``bpy_scripts/`` under GPL-3.0-or-later; the adapter
starts Blender as a separate program and exchanges files with it.
"""
