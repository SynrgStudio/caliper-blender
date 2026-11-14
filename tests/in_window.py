# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs inside Blender with its window (tests/run.py --window): Analyze and Repair as a click starts them,
in the background with the panel's progress, then screenshots of the panel and the drawn errors.
CALIPER_SHOTS is the folder for the screenshots."""
import os
import sys
import traceback

import bmesh
import bpy

failures = []
shots = os.environ["CALIPER_SHOTS"]


def check(condition, what):
    print(("ok   " if condition else "FAIL ") + what, flush=True)
    if not condition:
        failures.append(what)


def finish():
    print("RESULT", "OK" if not failures else "FAILED: %s" % failures, flush=True)
    os._exit(0 if not failures else 1)


def view3d():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return window, area, region
    raise RuntimeError("no 3D view")


def shot(name):
    window, area, region = view3d()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=os.path.join(shots, name))


def setup():
    package = "bl_ext.user_default.caliper_bridge"
    bpy.ops.preferences.addon_enable(module=package)
    from bl_ext.user_default.caliper_bridge import state
    # The test can't click the Caliper tab: show the panel under the tab that is open.
    from bl_ext.user_default import caliper_bridge
    panel = caliper_bridge.VIEW3D_PT_caliper
    bpy.utils.unregister_class(panel)
    panel.bl_category = "Item"
    panel.bl_order = -100
    bpy.utils.register_class(panel)
    prefs = bpy.context.preferences.addons[package].preferences
    prefs.cli_path = ""
    print("installed Caliper Nightly found:", state.cli, (state.cli_version or {}).get("version"), flush=True)
    if os.environ.get("CALIPER_CLI"):
        prefs.cli_path = os.environ["CALIPER_CLI"]
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    mesh = bpy.data.meshes.new("Statue")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=10)
    holes = [f for f in bm.faces if f.calc_center_median().z > 8.5 or (f.calc_center_median().x > 9 and abs(f.calc_center_median().z) < 1.5)]
    bmesh.ops.delete(bm, geom=holes, context="FACES_ONLY")
    flip = [f for f in bm.faces if f.calc_center_median().y < -9]
    bmesh.ops.reverse_faces(bm, faces=flip)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Statue", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    window, area, region = view3d()
    space = area.spaces.active
    space.show_region_ui = True
    space.shading.type = "SOLID"
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.view_selected()
    for r in area.regions:
        if r.type == "UI":
            with bpy.context.temp_override(window=window, area=area, region=r):
                try:
                    bpy.ops.wm.context_set_string(data_path="space_data.region_ui.active_panel_category", value="Caliper")
                except Exception:
                    pass
            try:
                r.active_panel_category = "Caliper"
            except Exception:
                pass
    return state


def main():
    state = setup()
    steps = iter(range(10**6))
    phase = {"name": "start", "ticks": 0}

    def tick():
        try:
            phase["ticks"] += 1
            if phase["ticks"] > 600:
                check(False, "timed out in %s" % phase["name"])
                finish()
            window, area, region = view3d()
            if phase["name"] == "start":
                with bpy.context.temp_override(window=window, area=area, region=region):
                    check(bpy.ops.caliper.analyze("INVOKE_DEFAULT") == {"RUNNING_MODAL"}, "Analyze runs in the background")
                phase["name"] = "analyzing"
            elif phase["name"] == "analyzing" and state.job is None:
                r = state.results[0]
                print("analysis:", r.get("entry", {}).get("defects"), flush=True)
                check(r.get("entry", {}).get("needs_repair"), "the statue needs repair")
                shot("1-analysis.png")
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.caliper.focus(step=1)
                phase["name"] = "focus"
                return 0.5
            elif phase["name"] == "focus":
                shot("2-focus.png")
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_selected()
                    check(bpy.ops.caliper.repair("INVOKE_DEFAULT") == {"RUNNING_MODAL"}, "Repair runs in the background")
                phase["name"] = "repairing"
                phase["seen_job"] = False
            elif phase["name"] == "repairing":
                if state.job and state.job.get("percent") is not None and not phase.get("progress_shot"):
                    shot("3-repairing.png")
                    phase["progress_shot"] = True
                if state.job is None:
                    check(bpy.data.objects.get("Statue_repaired") is not None, "the repaired copy came back as a new object")
                    check(bpy.data.objects.get("Statue") is not None, "the original is kept")
                    phase["name"] = "done"
                    return 0.5
            elif phase["name"] == "done":
                shot("4-repaired.png")
                finish()
        except Exception:
            traceback.print_exc()
            failures.append("exception")
            finish()
        return 0.2

    bpy.app.timers.register(tick, first_interval=1.0)


try:
    main()
except Exception:
    traceback.print_exc()
    failures.append("exception")
    finish()
