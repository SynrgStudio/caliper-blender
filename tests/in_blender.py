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


def box(name, center, size, collection):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = center
    return obj


def cli_json(*args):
    import json
    import subprocess
    out = subprocess.run([os.environ["CALIPER_CLI"], *args], capture_output=True)
    return json.loads(out.stdout.decode())


def export_tests():
    """The four ways to export, each through Caliper; the files checked with the command line."""
    import tempfile
    from bl_ext.user_default.caliper_bridge import state
    package = "bl_ext.user_default.caliper_bridge"
    prefs = bpy.context.preferences.addons[package].preferences
    prefs.cli_path = os.environ["CALIPER_CLI"]
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    scene = bpy.context.scene
    def collection(name, parent=None):
        c = bpy.data.collections.new(name)
        (parent or scene.collection).children.link(c)
        return c
    body, heads, weapon = collection("Body"), collection("Heads"), collection("Weapon")
    head_c = collection("HeadC", heads)
    torso = box("Torso", (0, 0, 0), 20, body)
    arm = box("Arm", (12, 0, 0), 8, body)        # crosses the torso: Repair joins them
    box("HeadA", (0, 0, 12), 8, heads)
    head_b = box("HeadB", (0, 0, 13), 6, heads)
    box("Top", (0, 0, 12), 8, head_c)
    box("Horn", (0, 0, 17), 3, head_c)          # HeadC: two parts, one alternative
    box("GunA", (17, 0, 0), 5, weapon)
    box("SwordA", (17, 0, 4), 4, weapon)
    head_b.hide_set(True)                       # hidden alternatives are exported too
    bpy.context.view_layer.update()

    settings = scene.caliper_export
    folder = tempfile.mkdtemp(prefix="caliper-export-test-")
    settings.folder = folder
    def files():
        return sorted(f for f in os.listdir(folder) if f.endswith(".stl"))
    def clean_out():
        for f in os.listdir(folder):
            os.remove(os.path.join(folder, f))

    # 4. Variants: Body always, one head, a weapon or none: 1 x 3 x 3.
    body.caliper_role, heads.caliper_role, weapon.caliper_role = "CORE", "PICK", "OPTIONAL"
    settings.mode, settings.action = "VARIANTS", "REPAIR"
    check(bpy.ops.caliper.export() == {"FINISHED"}, "variants export runs")
    expected = sorted("Body_%s%s.stl" % (h, w) for h in ("HeadA", "HeadB", "HeadC") for w in ("_GunA", "_SwordA", ""))
    check(files() == expected, "variants: the 9 combinations, named by their parts: %s" % files())
    rows = state.export["rows"]
    check(len(rows) == 9 and all(r["status"] in ("repaired", "clean") for r in rows), "every variant repaired or clean: %s" % sorted({r["status"] for r in rows}))
    doc = cli_json("analyze", os.path.join(folder, "Body_HeadC_GunA.stl"))
    f = doc["files"][0]
    check(not f["needs_repair"] and f["bodies"] == 1, "a variant comes out as one closed body")
    info = cli_json("info", os.path.join(folder, "Body_HeadA.stl"))["files"][0]
    check(abs(info["size_mm"][2] - 26) < 1e-3, "Body_HeadA has the head on top (height %s)" % info["size_mm"][2])

    # 1. Combine the selected objects.
    clean_out()
    settings.mode, settings.combined_name = "COMBINE", "Torso and arm"
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name in ("Torso", "Arm"))
    bpy.ops.caliper.export()
    check(files() == ["Torso and arm.stl"], "combine: one file named as asked: %s" % files())
    f = cli_json("analyze", os.path.join(folder, "Torso and arm.stl"))["files"][0]
    check(not f["needs_repair"] and f["bodies"] == 1 and state.export["rows"][0]["status"] == "repaired", "combine: repaired into one body")

    # 2. Each selected object.
    clean_out()
    settings.mode = "OBJECTS"
    bpy.ops.caliper.export()
    check(files() == ["Arm.stl", "Torso.stl"], "each object: one file per object: %s" % files())
    check(all(r["status"] == "clean" for r in state.export["rows"]), "each object: closed cubes come out clean")

    # 3. Each ticked collection.
    clean_out()
    settings.mode = "COLLECTIONS"
    body.caliper_batch = weapon.caliper_batch = True
    bpy.ops.caliper.export()
    check(files() == ["Body.stl", "Weapon.stl"], "collections: one file per ticked collection: %s" % files())
    f = cli_json("info", os.path.join(folder, "Body.stl"))["files"][0]
    check(f["triangles"] == 24 or state.export["rows"][0]["status"] == "repaired", "collections: Body holds torso and arm")

    # Check only, Export only, scale and skipping existing files.
    clean_out()
    open_box = box("Open", (60, 0, 0), 10, scene.collection)
    bm = bmesh.new(); bm.from_mesh(open_box.data); bm.faces.ensure_lookup_table(); bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY"); bm.to_mesh(open_box.data); bm.free()
    for o in bpy.context.view_layer.objects:
        o.select_set(o.name == "Open")
    settings.mode, settings.action = "OBJECTS", "CHECK"
    bpy.ops.caliper.export()
    row = state.export["rows"][0]
    check(row["status"] == "needs repair" and "open edges" in row["detail"] and files() == ["Open.stl"], "check: written as it is, said to need repair (%s)" % row["detail"])
    settings.overwrite = False
    bpy.ops.caliper.export()
    check(state.export["rows"][0]["status"] == "skipped", "an existing file is skipped when replacing is off")
    settings.overwrite, settings.action, settings.scale = True, "EXPORT", 2.0
    bpy.ops.caliper.export()
    f = cli_json("info", os.path.join(folder, "Open.stl"))["files"][0]
    check(state.export["rows"][0]["status"] == "exported" and abs(f["size_mm"][0] - 20) < 1e-3, "export only, scale 2 doubles the size (%s)" % f["size_mm"])
    settings.scale = 1.0
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


try:
    main()
    export_tests()
except Exception:
    traceback.print_exc()
    failures.append("exception")
print("RESULT", "OK" if not failures else "FAILED: %s" % failures)
sys.stdout.flush()
os._exit(0 if not failures else 1)
