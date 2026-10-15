# SPDX-License-Identifier: GPL-3.0-or-later
#
# MetaForge Blender render script.
# Copyright (C) 2026 MetaForge contributors
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See LICENSE in this directory.
#
# This file runs INSIDE Blender (it imports bpy), so it is licensed under the
# GPL like Blender's Python API requires. Nothing else in MetaForge imports
# it: the adapter starts Blender as a separate program and exchanges files
# with it (a job JSON in; PNG renders, a .blend file and a result JSON out).
"""Render a mesh model in Blender, headless, from a job file.

Usage (the adapter builds this command):

    blender --background --factory-startup --python-exit-code 1 \
        --python render_scene.py -- /path/to/job.json

Job fields: ``input`` (a .glb/.gltf/.stl/.obj/.ply path), ``out_dir``,
``views`` (names below, or ``"turntable"`` with ``turntable_frames``),
``resolution`` [w, h], ``samples``, ``projection`` (perspective or
orthographic), ``transparent`` (bool), ``background`` [r, g, b],
``colours`` ({object-name substring: [r, g, b, a]}), ``save_blend`` (bool).
A ``result.json`` in ``out_dir`` lists what was written.
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector

VIEWS = {
    "iso": (1.0, -1.0, 0.8),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
}


def load_job():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("render_scene.py: pass the job file after --")
    with open(argv[argv.index("--") + 1], encoding="utf-8") as handle:
        return json.load(handle)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    # CAD exports are Z-up; glTF is Y-up by its spec and its importer
    # converts. The mesh formats default to Y-up, so say Z-up explicitly.
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path, forward_axis="Y", up_axis="Z")
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path, forward_axis="Y", up_axis="Z")
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path, forward_axis="Y", up_axis="Z")
    else:
        raise SystemExit(f"render_scene.py: cannot import {ext} files")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("render_scene.py: the model has no mesh objects")
    return meshes


def bounds(meshes):
    lo = Vector((math.inf, math.inf, math.inf))
    hi = Vector((-math.inf, -math.inf, -math.inf))
    for obj in meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            lo = Vector((min(lo.x, world.x), min(lo.y, world.y), min(lo.z, world.z)))
            hi = Vector((max(hi.x, world.x), max(hi.y, world.y), max(hi.z, world.z)))
    return lo, hi


def apply_colours(meshes, colours):
    applied = []
    for obj in meshes:
        for key, rgba in colours.items():
            if key.lower() in obj.name.lower():
                mat = bpy.data.materials.new(name=f"metaforge_{key}")
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf is not None:
                    bsdf.inputs["Base Color"].default_value = tuple(rgba)
                obj.data.materials.clear()
                obj.data.materials.append(mat)
                applied.append(obj.name)
                break
    return applied


def setup_world(background):
    world = bpy.data.worlds.new("metaforge_world")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (*background, 1.0)
        # Mostly a backdrop: a strong world washes colours out.
        bg.inputs["Strength"].default_value = 0.35
    bpy.context.scene.world = world


def add_lights(centre, radius):
    key = bpy.data.lights.new("metaforge_key", type="AREA")
    key.energy = 250.0 * radius * radius
    key.size = radius * 2.0
    key_obj = bpy.data.objects.new("metaforge_key", key)
    key_obj.location = centre + Vector((radius * 2.0, -radius * 2.5, radius * 3.0))
    bpy.context.scene.collection.objects.link(key_obj)
    aim(key_obj, centre)
    sun = bpy.data.lights.new("metaforge_sun", type="SUN")
    sun.energy = 2.0
    sun_obj = bpy.data.objects.new("metaforge_sun", sun)
    sun_obj.rotation_euler = (math.radians(50), 0.0, math.radians(30))
    bpy.context.scene.collection.objects.link(sun_obj)


def aim(obj, target):
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def make_camera(projection, radius):
    cam = bpy.data.cameras.new("metaforge_camera")
    if projection == "orthographic":
        cam.type = "ORTHO"
        cam.ortho_scale = radius * 2.4
    else:
        cam.lens = 50.0
    cam.clip_end = radius * 100.0
    cam_obj = bpy.data.objects.new("metaforge_camera", cam)
    bpy.context.scene.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    return cam_obj


def place(cam_obj, direction, centre, radius):
    v = Vector(direction).normalized()
    # Far enough that the bounding sphere fits a 50 mm lens's view.
    distance = radius / math.tan(math.radians(39.6) / 2.0) * 1.25
    cam_obj.location = centre + v * distance
    if abs(v.z) > 0.999:
        # Straight up or down: aim with a fixed roll so the image is stable.
        cam_obj.rotation_euler = (0.0 if v.z > 0 else math.pi, 0.0, 0.0)
    else:
        aim(cam_obj, centre)


def configure_render(job):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(job.get("samples", 32))
    scene.cycles.use_denoising = True
    width, height = job.get("resolution", [1280, 960])
    scene.render.resolution_x = int(width)
    scene.render.resolution_y = int(height)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(job.get("transparent", False))
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    # Colour review wants the specified RGB, not a filmic look: "Standard"
    # shows sRGB colours as given (AgX, the default, desaturates them).
    scene.view_settings.view_transform = "Standard"


def view_list(job):
    views = list(job.get("views") or ["iso"])
    out = []
    for name in views:
        if name == "turntable":
            frames = max(1, int(job.get("turntable_frames", 12)))
            for i in range(frames):
                angle = 2.0 * math.pi * i / frames
                out.append((f"turntable_{i:03d}", (math.sin(angle), -math.cos(angle), 0.6)))
        elif name in VIEWS:
            out.append((name, VIEWS[name]))
        else:
            raise SystemExit(f"render_scene.py: unknown view {name!r}")
    return out


def main():
    job = load_job()
    out_dir = job["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    clear_scene()
    meshes = import_model(job["input"])
    applied = apply_colours(meshes, job.get("colours") or {})
    lo, hi = bounds(meshes)
    centre = (lo + hi) / 2.0
    radius = max((hi - lo).length / 2.0, 1e-6)
    setup_world(tuple(job.get("background", [0.92, 0.92, 0.92])))
    add_lights(centre, radius)
    cam_obj = make_camera(job.get("projection", "perspective"), radius)
    configure_render(job)
    images = []
    for name, direction in view_list(job):
        place(cam_obj, direction, centre, radius)
        path = os.path.join(out_dir, f"{name}.png")
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        images.append({"view": name, "file": f"{name}.png"})
    blend = None
    if job.get("save_blend"):
        blend = "scene.blend"
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, blend), copy=True)
    result = {
        "images": images,
        "blend": blend,
        "mesh_objects": len(meshes),
        "coloured_objects": applied,
        "bounds_min": list(lo),
        "bounds_max": list(hi),
        "blender_version": bpy.app.version_string,
    }
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as handle:
        json.dump(result, handle)


main()
