# SPDX-License-Identifier: GPL-3.0-or-later
"""Caliper Bridge: analyze and repair Blender objects with Caliper Nightly, from the N panel.

Analyze exports the objects as they print (modifiers applied, world space) to a temporary STL, runs
`caliper-nightly-cli analyze --markers` and draws each defect where Caliper found it. Repair runs
`caliper-nightly-cli repair` on each object and imports the result as a new object; the original stays."""
import os
import shutil
import tempfile
import threading

import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from . import caliper, mesh_io, overlay, state

PRINTABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


# ---------------------------------------------------------------- preferences and settings

def _prefs(context=None):
    return (context or bpy.context).preferences.addons[__package__].preferences


def detect():
    """Finds the command line and asks its version. Fast: run on the main thread."""
    try:
        override = _prefs().cli_path
    except (KeyError, AttributeError):
        override = ""
    state.cli = caliper.find_cli(override)
    state.cli_version = caliper.version(state.cli) if state.cli else None
    if state.cli and state.cli_version is None:
        state.cli = None
    return None


def _changed_path(self, context):
    detect()


class CaliperPreferences(AddonPreferences):
    bl_idname = __package__

    cli_path: StringProperty(
        name="Caliper Nightly",
        description="Folder of Caliper Nightly or its caliper-nightly-cli. Empty: the installed Caliper Nightly",
        subtype="FILE_PATH", update=_changed_path)
    hide_original: BoolProperty(
        name="Hide the original after a repair", default=True,
        description="The repaired copy is added beside the original; hide the original so they don't overlap")
    keep_cavities: BoolProperty(
        name="Keep sealed cavities", default=False,
        description="Repair keeps the sealed cavities Caliper certified, as Keep all does in the app. "
                    "Off: they are removed, as the app does by default")

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "cli_path")
        if state.cli:
            layout.label(text="Using " + state.cli, icon="CHECKMARK")
        else:
            layout.label(text="Caliper Nightly was not found. Install it, or name its folder above.", icon="ERROR")
        layout.prop(self, "hide_original")
        layout.prop(self, "keep_cavities")


def _redraw(self, context):
    overlay.redraw()


class CaliperSettings(PropertyGroup):
    scope: EnumProperty(
        name="Check", items=[
            ("SELECTED", "Selected", "Each selected object on its own"),
            ("SCENE", "Whole scene", "Every visible object together, as one print plate"),
        ], default="SELECTED")
    show_markers: BoolProperty(name="Show errors", default=True, update=_redraw,
                               description="Draw where Caliper found each error in the viewport")
    markers_through: BoolProperty(name="See through", default=True, update=_redraw,
                                  description="Draw the errors in front of the model, even where it hides them")


# ---------------------------------------------------------------- what to send

def _targets(context, scope):
    if scope == "SCENE":
        objects = [o for o in context.view_layer.objects if o.type in PRINTABLE and o.visible_get()]
    else:
        objects = [o for o in context.selected_objects if o.type in PRINTABLE]
        if not objects and context.active_object and context.active_object.type in PRINTABLE:
            objects = [context.active_object]
    return objects


def _export(context, objects, folder, together):
    """Writes the STL files. Returns one target per file: label, objects, the triangle each starts at."""
    depsgraph = context.evaluated_depsgraph_get()
    pieces = []
    for obj in objects:
        tris, polygons, same = mesh_io.world_triangles(obj, depsgraph)
        if len(tris):
            pieces.append((obj, tris, polygons, same))
    groups = [pieces] if together and pieces else [[p] for p in pieces]
    targets = []
    for i, group in enumerate(groups):
        path = os.path.join(folder, "%d.stl" % i)
        mesh_io.write_stl(path, np.concatenate([p[1] for p in group]))
        offsets = np.cumsum([0] + [len(p[1]) for p in group])
        targets.append({
            "label": "Whole scene" if together else group[0][0].name,
            "file": path,
            "objects": [p[0].name for p in group],
            "offsets": offsets,
            "polygons": [p[2] for p in group],
            "same": [p[3] for p in group],
            "matrices": [np.array(p[0].matrix_world, np.float64) for p in group],
        })
    return targets


def _store_markers(target, entry):
    found = {}
    for m in entry.get("markers", []):
        j = int(np.searchsorted(target["offsets"], m["face"], side="right")) - 1
        local = m["face"] - int(target["offsets"][j])
        group = found.setdefault(j, {"kinds": [], "a": [], "b": [], "center": [], "triangles": []})
        group["kinds"].append(m["kind"])
        group["a"].append(m["a"]); group["b"].append(m["b"]); group["center"].append(m["center"])
        group["triangles"].append(local)
    for j, group in found.items():
        name = target["objects"][j]
        state.markers[name] = {
            "inverse": np.linalg.pinv(target["matrices"][j]),
            "kinds": group["kinds"],
            "a": np.array(group["a"], np.float64),
            "b": np.array(group["b"], np.float64),
            "center": np.array(group["center"], np.float64),
            "triangles": np.array(group["triangles"], np.int64),
            "polygons": target["polygons"][j],
            "same_polygons": target["same"][j],
        }


# ---------------------------------------------------------------- running in the background

class _Work:
    """The command line in a thread, so Blender keeps responding; the modal operator polls it."""

    def __init__(self, title, function):
        self.stop = False
        self.done = False
        self.value = None
        self.error = None
        self.function = function
        state.job = {"title": title, "percent": None, "work": self}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.value = self.function(self)
        except Exception as e:  # shown in the panel; the thread must not die silently
            self.error = str(e)
        self.done = True


class _Modal:
    """Shared by Analyze and Repair: start() in invoke, finish() when the work is done."""
    _timer = None

    def invoke(self, context, event):
        if state.job:
            return {"CANCELLED"}
        if not state.cli:
            detect()
        if not state.cli:
            self.report({"ERROR"}, "Caliper Nightly was not found. Install it, or name its folder in the extension's preferences.")
            return {"CANCELLED"}
        self.folder = tempfile.mkdtemp(prefix="caliper-blender-")
        try:
            if not self.start(context):
                shutil.rmtree(self.folder, ignore_errors=True)
                return {"CANCELLED"}
        except Exception:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        self.step(context)
        _redraw_panels(context)
        if not self.work.done:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        try:
            self.finish(context)
        finally:
            state.job = None
            shutil.rmtree(self.folder, ignore_errors=True)
            _redraw_panels(context)
            overlay.redraw()
        return {"FINISHED"}

    def step(self, context):
        pass

    def execute(self, context):
        """Without a window (scripts, background): the same work, waiting for it."""
        if not state.cli:
            detect()
        if not state.cli:
            self.report({"ERROR"}, "Caliper Nightly was not found.")
            return {"CANCELLED"}
        self.folder = tempfile.mkdtemp(prefix="caliper-blender-")
        try:
            if not self.start(context):
                return {"CANCELLED"}
            self.work.thread.join()
            self.step(context)
            self.finish(context)
        finally:
            state.job = None
            shutil.rmtree(self.folder, ignore_errors=True)
        return {"FINISHED"}


def _redraw_panels(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()


class CALIPER_OT_analyze(_Modal, Operator):
    bl_idname = "caliper.analyze"
    bl_label = "Analyze"
    bl_description = "Analyze in Caliper Nightly and show its errors in the viewport"

    @classmethod
    def poll(cls, context):
        return state.job is None

    def start(self, context):
        settings = context.scene.caliper
        objects = _targets(context, settings.scope)
        if not objects:
            self.report({"WARNING"}, "Select an object to analyze." if settings.scope == "SELECTED" else "Nothing visible to analyze.")
            return False
        self.targets = _export(context, objects, self.folder, settings.scope == "SCENE")
        if not self.targets:
            self.report({"WARNING"}, "The objects have no faces.")
            return False
        files = [t["file"] for t in self.targets]
        self.work = _Work("Analyzing", lambda work: caliper.analyze(state.cli, files, lambda: work.stop))
        return True

    def finish(self, context):
        if self.work.stop:
            self.report({"INFO"}, "Analysis cancelled.")
            return
        if self.work.error:
            self.report({"ERROR"}, self.work.error)
            return
        code, doc, err, with_markers = self.work.value
        if code not in (0, 1, 3) or not doc:
            message = caliper.failure(code, err)
            state.clear()
            state.results.append({"label": "Analysis", "objects": [], "error": message})
            self.report({"ERROR"}, message)
            return
        state.clear()
        for target, entry in zip(self.targets, doc["files"]):
            result = {"label": target["label"], "objects": target["objects"], "entry": entry, "markers": with_markers}
            if "error" in entry:
                result["error"] = entry["error"]
            state.results.append(result)
            _store_markers(target, entry)
        needing = sum(1 for r in state.results if r.get("entry", {}).get("needs_repair"))
        self.report({"INFO"}, "Caliper: %d of %d need repair." % (needing, len(state.results)) if needing else "Caliper: no errors found.")


class CALIPER_OT_repair(_Modal, Operator):
    bl_idname = "caliper.repair"
    bl_label = "Repair"
    bl_description = ("Repair in Caliper Nightly. Each object is repaired on its own and comes back as a new object; "
                      "the original is kept")

    @classmethod
    def poll(cls, context):
        return state.job is None

    def start(self, context):
        settings = context.scene.caliper
        objects = _targets(context, settings.scope)
        if not objects:
            self.report({"WARNING"}, "Select an object to repair." if settings.scope == "SELECTED" else "Nothing visible to repair.")
            return False
        self.targets = _export(context, objects, self.folder, False)
        if not self.targets:
            self.report({"WARNING"}, "The objects have no faces.")
            return False
        self.outcomes = []
        self.done_count = 0
        self.created = []
        keep = _prefs(context).keep_cavities
        targets, outcomes, count = self.targets, self.outcomes, len(self.targets)

        def repair_all(work):
            for i, target in enumerate(targets):
                if work.stop:
                    break
                state.job["message"] = "%s (%d of %d)" % (target["label"], i + 1, count) if count > 1 else target["label"]
                output = target["file"][:-4] + "-repaired.stl"

                def percent(p, i=i):
                    state.job["percent"] = (i + p / 100) / count
                outcomes.append((target, output) + caliper.repair(state.cli, target["file"], output, keep, percent, lambda: work.stop))
            return outcomes

        self.work = _Work("Repairing", repair_all)
        return True

    def step(self, context):
        # Import each object as soon as its repair ends, while the next one runs.
        while self.done_count < len(self.outcomes):
            if self.work.stop and self.done_count == len(self.outcomes) - 1 and not self.work.done:
                break
            self._bring_back(context, *self.outcomes[self.done_count])
            self.done_count += 1

    def _bring_back(self, context, target, output, code, doc, err):
        original = bpy.data.objects.get(target["objects"][0])
        if self.work.stop and code != 0:
            return
        if code == 8:
            state.results.append({"label": target["label"], "objects": [target["label"]], "repair": doc, "clean": True})
            return
        if code != 0 or not os.path.isfile(output):
            state.results.append({"label": target["label"], "objects": [], "error": caliper.failure(code, err)})
            return
        tris = mesh_io.read_stl(output)
        collections = list(original.users_collection) if original else [context.scene.collection]
        name = (original.name if original else target["label"]) + "_repaired"
        new = mesh_io.new_object(name, tris, original, collections)
        self.created.append(new)
        if original and _prefs(context).hide_original:
            original.hide_set(True)
        state.markers.pop(target["objects"][0], None)
        state.results.append({"label": new.name, "objects": [new.name], "repair": doc, "from": target["label"]})

    def finish(self, context):
        if self.work.error:
            self.report({"ERROR"}, self.work.error)
        if self.created:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in self.created:
                obj.select_set(True)
            context.view_layer.objects.active = self.created[-1]
        if self.work.stop:
            self.report({"INFO"}, "Repair cancelled." + (" %d object(s) were repaired before that." % len(self.created) if self.created else ""))
        elif self.created:
            self.report({"INFO"}, "Caliper repaired %d object(s); the originals are kept." % len(self.created))


class CALIPER_OT_cancel(Operator):
    bl_idname = "caliper.cancel"
    bl_label = "Cancel"
    bl_description = "Stop Caliper. Objects already repaired stay"

    def execute(self, context):
        if state.job:
            state.job["work"].stop = True
        return {"FINISHED"}


class CALIPER_OT_detect(Operator):
    bl_idname = "caliper.detect"
    bl_label = "Look again"
    bl_description = "Look for Caliper Nightly again"

    def execute(self, context):
        detect()
        if not state.cli:
            self.report({"WARNING"}, "Caliper Nightly was not found.")
        return {"FINISHED"}


class CALIPER_OT_clear(Operator):
    bl_idname = "caliper.clear"
    bl_label = "Clear"
    bl_description = "Forget the results and stop drawing the errors"

    def execute(self, context):
        state.clear()
        overlay.redraw()
        return {"FINISHED"}


def _all_markers():
    out = []
    for name, group in state.markers.items():
        obj = bpy.data.objects.get(name)
        if obj and obj.visible_get():
            out.extend((name, i) for i in range(len(group["kinds"])))
    return out


class CALIPER_OT_focus(Operator):
    bl_idname = "caliper.focus"
    bl_label = "Go to error"
    bl_description = "Move the view to the next error Caliper found"

    step: bpy.props.IntProperty(default=1)

    def execute(self, context):
        every = _all_markers()
        if not every:
            return {"CANCELLED"}
        at = every.index(state.focus) if state.focus in every else -1
        state.focus = every[(at + self.step) % len(every)] if at >= 0 or self.step > 0 else every[-1]
        name, i = state.focus
        obj = bpy.data.objects[name]
        group = state.markers[name]
        moved = np.array(obj.matrix_world, np.float64) @ group["inverse"]
        point = group["center"][i] @ moved[:3, :3].T + moved[:3, 3]
        size = max(obj.dimensions) if max(obj.dimensions) > 0 else 1.0
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    view = area.spaces.active.region_3d
                    view.view_location = point.tolist()
                    view.view_distance = size * 0.35
                    area.tag_redraw()
        _redraw_panels(context)
        return {"FINISHED"}


class CALIPER_OT_select_faces(Operator):
    bl_idname = "caliper.select_faces"
    bl_label = "Select faces"
    bl_description = "Select the faces with errors in Edit Mode"

    object: StringProperty()

    def execute(self, context):
        import bmesh
        obj = bpy.data.objects.get(self.object)
        group = state.markers.get(self.object)
        if not obj or not group or not group["same_polygons"]:
            return {"CANCELLED"}
        polygons = sorted({int(group["polygons"][t]) for k, t in zip(group["kinds"], group["triangles"]) if k != "cavity"})
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        context.tool_settings.mesh_select_mode = (False, False, True)
        bm = bmesh.from_edit_mesh(obj.data)
        for f in bm.faces:
            f.select_set(False)
        bm.faces.ensure_lookup_table()
        for p in polygons:
            if p < len(bm.faces):
                bm.faces[p].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"}, "%d face(s) selected." % len(polygons))
        return {"FINISHED"}


# ---------------------------------------------------------------- the panel

def _length(context, value):
    units = context.scene.unit_settings
    if units.system == "NONE":
        return "%.3g" % value
    return bpy.utils.units.to_string(units.system, "LENGTH", value * units.scale_length, precision=3)


def _volume(context, value):
    units = context.scene.unit_settings
    if units.system == "NONE":
        return "%.4g" % value
    return bpy.utils.units.to_string(units.system, "VOLUME", value * units.scale_length ** 3, precision=3)


DEFECTS = [
    ("open_edges", "boundary"),
    ("non_manifold_edges", "nonmanifold"),
    ("inconsistent_winding", "winding"),
    ("degenerate_faces", "degenerate"),
    ("duplicate_faces", "duplicate"),
]


def _draw_defects(col, defects):
    any_shown = False
    for key, kind in DEFECTS:
        n = defects.get(key, 0)
        if n:
            col.label(text="%s: %s" % (overlay.KINDS[kind][0], format(n, ",")), icon="DOT")
            any_shown = True
    return any_shown


class VIEW3D_PT_caliper(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Caliper"
    bl_label = "Caliper"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.caliper
        if not state.cli:
            box = layout.box()
            box.label(text="Caliper Nightly not found", icon="ERROR")
            box.label(text="Install it, or name its folder in the")
            box.label(text="extension's preferences.")
            box.operator("caliper.detect", icon="FILE_REFRESH")
            return
        info = state.cli_version or {}
        row = layout.row()
        row.label(text="Caliper " + info.get("version", ""), icon="CHECKMARK")
        if info.get("activation") in ("renew_required", "not_activated"):
            layout.label(text="Activate Caliper Nightly to use it here.", icon="ERROR")

        layout.prop(settings, "scope", expand=True)
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("caliper.analyze", icon="VIEWZOOM")
        row.operator("caliper.repair", icon="MODIFIER")

        if state.job:
            box = layout.box()
            title = state.job["title"] + (": " + state.job["message"] if state.job.get("message") else "")
            percent = state.job.get("percent")
            if percent is not None and hasattr(box, "progress"):
                box.progress(factor=percent, type="BAR", text="%s  %d%%" % (title, percent * 100))
            else:
                box.label(text=title + "...", icon="SORTTIME")
            box.operator("caliper.cancel", icon="CANCEL")

        for result in state.results:
            self._draw_result(context, layout.box(), result)

        if state.markers:
            box = layout.box()
            row = box.row(align=True)
            row.prop(settings, "show_markers", toggle=True, icon="HIDE_OFF" if settings.show_markers else "HIDE_ON")
            row.prop(settings, "markers_through", toggle=True, icon="XRAY")
            every = _all_markers()
            if every:
                row = box.row(align=True)
                row.operator("caliper.focus", text="", icon="TRIA_LEFT").step = -1
                at = every.index(state.focus) + 1 if state.focus in every else 0
                row.label(text="Error %d of %d" % (at, len(every)) if at else "%d errors drawn" % len(every))
                row.operator("caliper.focus", text="", icon="TRIA_RIGHT").step = 1
        if state.results:
            layout.operator("caliper.clear", icon="X")

    def _draw_result(self, context, box, result):
        entry = result.get("entry")
        if result.get("error"):
            box.label(text=result["label"], icon="ERROR")
            for line in _wrap(result["error"], 42):
                box.label(text=line)
            return
        if "repair" in result:
            doc = result.get("repair") or {}
            if result.get("clean"):
                box.label(text=result["label"], icon="CHECKMARK")
                box.label(text="No errors found, nothing to repair.")
                return
            after = doc.get("after", {})
            box.label(text=result["label"], icon="CHECKMARK" if not after.get("needs_repair") else "ERROR")
            col = box.column(align=True)
            col.label(text="Repaired from %s in %.1f s" % (result.get("from", ""), doc.get("seconds", 0)))
            col.label(text="Triangles: %s → %s" % (format(doc.get("triangles_before", 0), ","), format(doc.get("triangles_after", 0), ",")))
            if not _draw_defects(col, after.get("defects", {})):
                col.label(text="No errors left.")
            return
        needs = entry.get("needs_repair")
        box.label(text=result["label"], icon="ERROR" if needs else "CHECKMARK")
        col = box.column(align=True)
        shown = _draw_defects(col, entry.get("defects", {}))
        cavities = entry.get("cavities", {})
        if cavities.get("state") == "complete" and cavities.get("count"):
            col.label(text="Sealed cavities: %d" % cavities["count"], icon="DOT")
            shown = True
        bodies = entry.get("bodies")
        if bodies and bodies > 1:
            col.label(text="Bodies: %d" % bodies, icon="DOT")
        if not shown and not needs:
            col.label(text="No errors found.")
        elif not shown and needs:
            col.label(text="Bodies that touch or cross: Repair joins them.")
        if needs and not result.get("markers"):
            col.label(text="Update Caliper Nightly to see where.", icon="INFO")
        sub = box.column(align=True)
        sub.scale_y = 0.8
        sub.label(text="Triangles: %s" % format(entry.get("triangles", 0), ","))
        size = entry.get("size_mm")
        if size:
            sub.label(text="Size: " + " × ".join(_length(context, v) for v in size))
        if "volume_mm3" in entry:
            sub.label(text="Volume: %s%s" % (_volume(context, entry["volume_mm3"]), "" if entry.get("volume_exact") else " (approx.)"))
        if not entry.get("fully_checked") and cavities.get("state") != "complete":
            sub.label(text="Cavities not fully checked.")
        for name in result["objects"]:
            group = state.markers.get(name)
            if group and group["same_polygons"] and any(k != "cavity" for k in group["kinds"]):
                op = box.operator("caliper.select_faces", text="Select faces" + ("" if len(result["objects"]) == 1 else ": " + name), icon="RESTRICT_SELECT_OFF")
                op.object = name


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


@persistent
def _on_load(_):
    state.clear()


CLASSES = (CaliperPreferences, CaliperSettings, CALIPER_OT_analyze, CALIPER_OT_repair, CALIPER_OT_cancel,
           CALIPER_OT_detect, CALIPER_OT_clear, CALIPER_OT_focus, CALIPER_OT_select_faces, VIEW3D_PT_caliper)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.caliper = bpy.props.PointerProperty(type=CaliperSettings)
    bpy.app.handlers.load_post.append(_on_load)
    overlay.register()
    bpy.app.timers.register(detect, first_interval=0.1)


def unregister():
    overlay.unregister()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    del bpy.types.Scene.caliper
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    state.clear()
