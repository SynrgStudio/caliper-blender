# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs inside Blender (tests/run.py starts it): analyze and repair through the extension's operators.
CALIPER_CLI names the command line to use; CALIPER_OLD_CLI, if set, one without --markers."""
import os
import sys
import traceback

import bmesh
import bpy
import numpy as np

failures = []


def check(condition, what):
    print(("ok   " if condition else "FAIL ") + what)
    if not condition:
        failures.append(what)


def cube(name, location, scale=(1, 1, 1), rotation=(0, 0, 0), open_top=False):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=20.0)
    if open_top:
        top = [f for f in bm.faces if f.calc_center_median().z > 9]
        bmesh.ops.delete(bm, geom=top, context="FACES_ONLY")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location, obj.scale, obj.rotation_euler = location, scale, rotation
    return obj


def main():
    package = "bl_ext.user_default.caliper_bridge"
    bpy.ops.preferences.addon_enable(module=package)
    from bl_ext.user_default.caliper_bridge import state, mesh_io
    prefs = bpy.context.preferences.addons[package].preferences
    prefs.cli_path = os.environ["CALIPER_CLI"]
    check(state.cli and state.cli_version and state.cli_version.get("schema") == 1, "finds the command line: %s" % state.cli)

    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    open_cube = cube("Open", (40, 5, 3), rotation=(0.3, 0.2, 0.5), open_top=True)
    mirrored = cube("Mirrored", (-40, 0, 0), scale=(-1, 1, 1))
    bpy.context.view_layer.update()

    # Analyze, each selected object on its own.
    for o in bpy.context.view_layer.objects:
        o.select_set(True)
    bpy.context.scene.caliper.scope = "SELECTED"
    check(bpy.ops.caliper.analyze() == {"FINISHED"}, "analyze runs")
    by_label = {r["label"]: r for r in state.results}
    check(set(by_label) == {"Open", "Mirrored"}, "one result per object: %s" % list(by_label))
    o = by_label["Open"]["entry"]
    check(o["defects"]["open_edges"] == 4 and o["needs_repair"], "the open cube has 4 open edges")
    check(not by_label["Mirrored"]["entry"]["needs_repair"], "a mirrored closed cube is clean (faces kept outward)")
    group = state.markers.get("Open")
    check(group is not None and len(group["kinds"]) == 4 and set(group["kinds"]) == {"boundary"}, "4 open-edge markers on Open")
    # The markers sit on the open cube's top edges, in world space.
    world = np.array(open_cube.matrix_world)
    top = [world @ np.array([x, y, 10.0, 1.0]) for x in (-10, 10) for y in (-10, 10)]
    ends = np.concatenate([group["a"], group["b"]])
    close = all(min(np.linalg.norm(e - t[:3]) for t in top) < 1e-3 for e in ends)
    check(close, "markers are at the top corners, in world space")

    bpy.ops.caliper.select_faces(object="Open")
    selected = sum(1 for p in open_cube.data.polygons if p.select)
    bpy.ops.object.mode_set(mode="OBJECT")
    selected = sum(1 for p in open_cube.data.polygons if p.select)
    check(selected == 4, "Select faces picks the 4 faces around the hole (%d)" % selected)

    # The whole scene as one plate: one result, its markers back on the object they belong to.
    bpy.context.scene.caliper.scope = "SCENE"
    bpy.ops.caliper.analyze()
    check([r["label"] for r in state.results] == ["Whole scene"], "whole scene gives one result")
    check(state.results[0]["entry"]["defects"]["open_edges"] == 4 and set(state.markers) == {"Open"}, "scene markers go back to Open")

    # Repair: a new object beside the original, which stays (hidden).
    bpy.context.scene.caliper.scope = "SELECTED"
    for ob in bpy.context.view_layer.objects:
        ob.select_set(ob.name == "Open")
    check(bpy.ops.caliper.repair() == {"FINISHED"}, "repair runs")
    repaired = bpy.data.objects.get("Open_repaired")
    check(repaired is not None, "the repaired copy is imported as Open_repaired")
    check(bpy.data.objects.get("Open") is not None and open_cube.hide_get(), "the original is kept, hidden")
    if repaired:
        check(np.allclose(np.array(repaired.matrix_world), np.array(open_cube.matrix_world)), "same transform as the original")
        bm = bmesh.new()
        bm.from_mesh(repaired.data)
        check(all(e.is_manifold for e in bm.edges) and len(bm.faces) > 0, "the repaired copy is closed")
        bm.free()
        result = state.results[-1]
        check(result.get("repair", {}).get("action") == "repaired" and not result["repair"]["after"]["needs_repair"], "the panel's result says repaired, no errors left")
        # Analyzing the copy finds nothing.
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob.name == "Open_repaired")
        bpy.ops.caliper.analyze()
        check(not state.results[0]["entry"]["needs_repair"], "the repaired copy analyzes clean")

    # A clean object: nothing to repair, nothing imported.
    count = len(bpy.data.objects)
    for ob in bpy.context.view_layer.objects:
        ob.select_set(ob.name == "Mirrored")
    bpy.ops.caliper.repair()
    check(len(bpy.data.objects) == count and state.results[-1].get("clean"), "a clean object is left alone")

    old = os.environ.get("CALIPER_OLD_CLI")
    if old:
        prefs.cli_path = old
        open_cube.hide_set(False)
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob.name == "Open")
        bpy.ops.caliper.analyze()
        r = state.results[0]
        check(r.get("entry", {}).get("defects", {}).get("open_edges") == 4 and not r["markers"], "an older Nightly still gives the counts (%s)" % state.cli_version.get("version"))

    prefs.cli_path = os.path.join(os.environ["TEMP"], "no-caliper-here")
    check(state.cli is None, "a wrong folder means not found")


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception")
print("RESULT", "OK" if not failures else "FAILED: %s" % failures)
sys.stdout.flush()
os._exit(0 if not failures else 1)
