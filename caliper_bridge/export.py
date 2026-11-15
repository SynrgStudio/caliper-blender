# SPDX-License-Identifier: GPL-3.0-or-later
"""Export, check and repair to files, four ways:

1. Combine: the selected objects as one model.
2. Objects: each selected object as its own model.
3. Collections: each ticked collection as one model, with every object inside it.
4. Variants: the Core collections plus one alternative of each Pick one collection (or none, when it
   is Optional), for every combination. An alternative is an object in that collection, or a
   collection inside it (several parts that go together).

Each model is exported as it prints (modifiers applied, world space), repaired by Caliper Nightly and
written to the output folder as a binary STL. Blender's objects are never changed."""
import os
import shutil
import tempfile
import threading
import time

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import caliper, mesh_io, state, variants

PRINTABLE = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
CONFIRM_ABOVE = 50

ROLES = [
    ("OFF", "Off", "Not exported", "X", 0),
    ("CORE", "Core", "In every model", "PINNED", 1),
    ("PICK", "One of", "Each model takes one of its objects (or of the collections inside it)", "RADIOBUT_ON", 2),
    ("OPTIONAL", "Optional", "Like Pick one, and also a model without any of them", "RADIOBUT_OFF", 3),
]
ROLE_INFO = {r[0]: r for r in ROLES}


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


class CaliperExportSettings(PropertyGroup):
    mode: EnumProperty(name="Export", default="OBJECTS", items=[
        ("COMBINE", "Combine selected", "The selected objects as one model", "MOD_BOOLEAN", 0),
        ("OBJECTS", "Each selected object", "Each selected object as its own model", "OUTLINER_OB_MESH", 1),
        ("COLLECTIONS", "Each collection", "Each ticked collection as one model, with every object inside it", "OUTLINER_COLLECTION", 2),
        ("VARIANTS", "Variants", "Every combination of the Core collections and one alternative of each Pick one collection", "MOD_ARRAY", 3),
    ])
    action: EnumProperty(name="Caliper", default="REPAIR", items=[
        ("REPAIR", "Repair", "Repair each model; clean ones are written as they are"),
        ("CHECK", "Check", "Write each model as it is and say which ones need repair"),
        ("EXPORT", "Export only", "Write the models without Caliper"),
    ])
    folder: StringProperty(name="Folder", subtype="DIR_PATH", default="//Caliper export",
                           description="Where the STL files go. // is the .blend's folder")
    combined_name: StringProperty(name="Name", description="The combined model's file name. Empty: the active object's name")
    scale: FloatProperty(name="Scale", default=1.0, min=1e-6, soft_max=1000, precision=4,
                         description="Multiplies the coordinates. Caliper reads one Blender unit as one millimetre")
    overwrite: BoolProperty(name="Replace files", default=True,
                            description="Replace a file with the same name in the folder. Off: such models are skipped")
    show_names: BoolProperty(name="Show the files", default=False, description="List the files the export will write")


# ---------------------------------------------------------------- what each mode exports

def printable(obj):
    return obj.type in PRINTABLE


def _objects_in(collection):
    seen, out = set(), []
    for o in collection.all_objects:
        if printable(o) and o.name not in seen:
            seen.add(o.name)
            out.append(o.name)
    return out


def scene_collections(scene, skip_inside_roles=False):
    """(collection, depth) for the scene's collections, as the Outliner orders them. With
    skip_inside_roles, the collections inside one that has a role are left out: they are its parts."""
    out = []

    def walk(parent, depth):
        for c in parent.children:
            out.append((c, depth))
            if not (skip_inside_roles and c.caliper_role != "OFF"):
                walk(c, depth + 1)
    walk(scene.collection, 0)
    return out


def alternatives(collection):
    """A Pick one collection's alternatives: each object directly in it, and each collection inside it."""
    out = [(o.name, [o.name]) for o in collection.objects if printable(o)]
    out += [(c.name, _objects_in(c)) for c in collection.children if _objects_in(c)]
    return out


def plan(context):
    """(models [(name, [object names])], problems [text]) for the current mode."""
    settings = context.scene.caliper_export
    problems = []
    if settings.mode in ("COMBINE", "OBJECTS"):
        selected = [o for o in context.selected_objects if printable(o)]
        if not selected:
            return [], ["Select the objects to export."]
        if settings.mode == "OBJECTS":
            return [(o.name, [o.name]) for o in selected], problems
        active = context.active_object
        name = settings.combined_name.strip() or (active.name if active and active in selected else selected[0].name)
        return [(name, [o.name for o in selected])], problems
    if settings.mode == "COLLECTIONS":
        models = []
        for c, _ in scene_collections(context.scene):
            if c.caliper_batch:
                objects = _objects_in(c)
                if objects:
                    models.append((c.name, objects))
                else:
                    problems.append("%s has no objects to export." % c.name)
        if not models and not problems:
            problems.append("Tick the collections to export.")
        return models, problems
    # Variants
    core_names, core_objects, choices = [], [], []
    for c, _ in scene_collections(context.scene, skip_inside_roles=True):
        if c.caliper_role == "CORE":
            core_names.append(c.name)
            core_objects.extend(o for o in _objects_in(c) if o not in core_objects)
        elif c.caliper_role in ("PICK", "OPTIONAL"):
            alts = alternatives(c)
            if alts:
                choices.append((c.name, alts, c.caliper_role == "OPTIONAL"))
            else:
                problems.append("%s has nothing to pick." % c.name)
    if not core_objects and not choices:
        return [], problems + ["Give the collections a role: Core, Pick one or Optional."]
    total = variants.count(choices)
    if total > variants.LIMIT:
        return [], ["That is %s models; the limit is %s." % (format(total, ","), format(variants.LIMIT, ","))]
    models = [(n, objs) for n, objs in variants.combinations(core_names, core_objects, choices) if objs]
    return models, problems


def variant_summary(context):
    """"Body × Heads 3 × Weapon 4 + none" for the panel."""
    parts = []
    for c, _ in scene_collections(context.scene, skip_inside_roles=True):
        if c.caliper_role == "CORE":
            parts.append(c.name)
        elif c.caliper_role in ("PICK", "OPTIONAL"):
            n = len(alternatives(c))
            parts.append("%s %d%s" % (c.name, n, " + none" if c.caliper_role == "OPTIONAL" else ""))
    return " × ".join(parts)


def output_folder(context):
    folder = context.scene.caliper_export.folder.strip()
    if not folder:
        return None, "Choose the output folder."
    if folder.startswith("//") and not bpy.data.filepath:
        return None, "Save the .blend first, or choose a full folder path."
    return os.path.normpath(bpy.path.abspath(folder)), None


# ---------------------------------------------------------------- operators

class CALIPER_OT_cycle_role(Operator):
    bl_idname = "caliper.cycle_role"
    bl_label = "Role"
    bl_description = "Click to change: Off, Core (in every model), Pick one, Optional (pick one or none)"
    bl_options = {"UNDO", "INTERNAL"}

    collection: StringProperty()

    def execute(self, context):
        c = bpy.data.collections.get(self.collection)
        if c:
            order = [r[0] for r in ROLES]
            c.caliper_role = order[(order.index(c.caliper_role) + 1) % len(order)]
        return {"FINISHED"}


class CALIPER_OT_open_folder(Operator):
    bl_idname = "caliper.open_folder"
    bl_label = "Open folder"
    bl_description = "Open the folder the files were written to"

    def execute(self, context):
        folder = (state.export or {}).get("folder")
        if folder and os.path.isdir(folder):
            bpy.ops.wm.path_open(filepath=folder)
        return {"FINISHED"}


class CALIPER_OT_export(Operator):
    bl_idname = "caliper.export"
    bl_label = "Export"
    bl_description = "Export the models, checked or repaired by Caliper Nightly, to the output folder"

    @classmethod
    def poll(cls, context):
        return state.job is None

    def invoke(self, context, event):
        models, _ = plan(context)
        if len(models) > CONFIRM_ABOVE:
            return context.window_manager.invoke_confirm(
                self, event, title="Export %s models?" % format(len(models), ","),
                message="Each one is exported and %s; this can take a while." % {
                    "REPAIR": "repaired", "CHECK": "checked", "EXPORT": "written"}[context.scene.caliper_export.action],
                confirm_text="Export")
        return self.execute(context)

    def execute(self, context):
        settings = context.scene.caliper_export
        models, problems = plan(context)
        if not models:
            self.report({"WARNING"}, problems[0] if problems else "Nothing to export.")
            return {"CANCELLED"}
        folder, error = output_folder(context)
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        if settings.action != "EXPORT":
            if not state.cli:
                from . import detect
                detect()
            if not state.cli:
                self.report({"ERROR"}, "Caliper Nightly was not found. Install it, or name its folder in the extension's preferences.")
                return {"CANCELLED"}
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.report({"ERROR"}, "Cannot make %s: %s" % (folder, e.strerror))
            return {"CANCELLED"}
        names = variants.unique([variants.file_name(n) for n, _ in models])
        self.batch = _Batch(context, [(file_name, objects) for file_name, (_, objects) in zip(names, models)],
                            folder, settings.action, settings.scale, settings.overwrite, _prefs().keep_cavities)
        if bpy.app.background or context.window is None:
            while not self.batch.advance(context):
                if self.batch.worker:
                    self.batch.worker.join()
            return {"FINISHED"}
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        done = self.batch.advance(context)
        from . import ui
        ui.redraw_panels(context)
        if not done:
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        report = state.export
        failed = report["counts"].get("failed", 0)
        self.report({"WARNING"} if failed else {"INFO"}, "Caliper: %d file(s) written to %s%s." % (
            report["written"], report["folder"], ", %d failed" % failed if failed else ""))
        return {"FINISHED"}


# ---------------------------------------------------------------- the pipeline

class _Batch:
    """One model at a time: exported on Blender's thread (it reads the scene), then Caliper works on it
    in a thread while Blender keeps responding. An object used by several models is read once."""

    def __init__(self, context, models, folder, action, scale, overwrite, keep_cavities):
        self.models, self.folder, self.action = models, folder, action
        self.scale, self.overwrite, self.keep = scale, overwrite, keep_cavities
        self.temp = tempfile.mkdtemp(prefix="caliper-export-")
        self.next = 0
        self.worker = None
        self.row = None
        self.stop = False
        self.started = time.monotonic()
        self.uses = {}
        for _, objects in models:
            for o in objects:
                self.uses[o] = self.uses.get(o, 0) + 1
        self.cache = {}
        state.export = {"folder": folder, "action": action, "rows": [], "counts": {}, "written": 0,
                        "total": len(models), "running": True}
        state.job = {"title": {"REPAIR": "Repairing", "CHECK": "Checking", "EXPORT": "Exporting"}[action],
                     "percent": 0.0, "message": "", "work": self, "kind": "export"}

    # The panel's Cancel sets work.stop.
    @property
    def done_count(self):
        return len(state.export["rows"])

    def _triangles(self, context, depsgraph, name):
        if name in self.cache:
            return self.cache[name]
        obj = bpy.data.objects.get(name)
        if obj is None:
            return np.zeros((0, 3, 3), np.float32)
        tris, _, _ = mesh_io.world_triangles(obj, depsgraph)
        tris = (tris * self.scale).astype(np.float32)
        if self.uses.get(name, 0) > 1:
            self.cache[name] = tris
        return tris

    def _record(self, row):
        state.export["rows"].append(row)
        counts = state.export["counts"]
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row.get("written"):
            state.export["written"] += 1

    def advance(self, context):
        """Moves the work on; True once every model is done (or cancelled)."""
        total = len(self.models)
        if self.worker is not None:
            if self.worker.is_alive():
                p = state.job.get("model_percent") or 0
                state.job["percent"] = (self.done_count + p / 100) / total
                return False
            self._record(self.row)
            self.worker = None
        if self.stop or self.next >= total:
            return self._finish()
        name, objects = self.models[self.next]
        self.next += 1
        destination = os.path.join(self.folder, name + ".stl")
        state.job["message"] = "%s (%d of %d)" % (name, self.next, total) if total > 1 else name
        state.job["model_percent"] = 0
        state.job["percent"] = self.done_count / total
        if os.path.exists(destination) and not self.overwrite:
            self._record({"name": name, "status": "skipped", "detail": "A file with this name is already there."})
            return False
        depsgraph = context.evaluated_depsgraph_get()
        parts = [self._triangles(context, depsgraph, o) for o in objects]
        parts = [p for p in parts if len(p)]
        if not parts:
            self._record({"name": name, "status": "failed", "detail": "No faces to export."})
            return False
        source = os.path.join(self.temp, "%d.stl" % self.next)
        mesh_io.write_stl(source, np.concatenate(parts))
        self.row = {"name": name, "status": "failed", "detail": "", "file": destination}
        self.worker = threading.Thread(target=self._work, args=(source, destination, self.row), daemon=True)
        self.worker.start()
        return False

    def _work(self, source, destination, row):
        try:
            stop = lambda: self.stop
            if self.action == "EXPORT":
                shutil.copyfile(source, destination)
                row.update(status="exported", written=True)
            elif self.action == "CHECK":
                code, doc, err = caliper.run(state.cli, ["analyze", source], should_stop=stop)
                if self.stop:
                    row.update(status="cancelled")
                elif code in (0, 1) and doc:
                    entry = doc["files"][0]
                    shutil.copyfile(source, destination)
                    row.update(written=True, status="needs repair" if entry.get("needs_repair") else "clean",
                               detail=_defects_text(entry.get("defects", {})))
                else:
                    row.update(detail=caliper.failure(code, err))
            else:
                repaired = source[:-4] + "-repaired.stl"

                def percent(p):
                    state.job["model_percent"] = p
                code, doc, err = caliper.repair(state.cli, source, repaired, self.keep, percent, stop)
                if self.stop and code != 0:
                    row.update(status="cancelled")
                elif code == 0:
                    shutil.move(repaired, destination)
                    after = (doc or {}).get("after", {})
                    row.update(written=True, status="errors left" if after.get("needs_repair") else "repaired",
                               detail="%s → %s triangles, %.1f s" % (format(doc.get("triangles_before", 0), ","),
                                                                    format(doc.get("triangles_after", 0), ","),
                                                                    doc.get("seconds", 0)))
                elif code == 8:
                    shutil.copyfile(source, destination)
                    row.update(written=True, status="clean", detail="Nothing to repair.")
                else:
                    row.update(detail=caliper.failure(code, err))
        except OSError as e:
            row.update(status="failed", written=False, detail="Cannot write %s: %s" % (os.path.basename(destination), e.strerror or e))
        except Exception as e:  # shown in the panel; the thread must not die silently
            row.update(status="failed", written=False, detail=str(e))
        finally:
            try:
                os.remove(source)
            except OSError:
                pass

    def _finish(self):
        state.export["running"] = False
        state.export["seconds"] = time.monotonic() - self.started
        if self.stop:
            state.export["cancelled"] = True
        state.job = None
        self.cache.clear()
        shutil.rmtree(self.temp, ignore_errors=True)
        return True


def _defects_text(defects):
    from .overlay import KINDS
    names = {"open_edges": "boundary", "non_manifold_edges": "nonmanifold", "inconsistent_winding": "winding",
             "degenerate_faces": "degenerate", "duplicate_faces": "duplicate"}
    parts = ["%s %s" % (format(n, ","), KINDS[names[k]][0].lower()) for k, n in defects.items() if n and k in names]
    return ", ".join(parts)


CLASSES = (CaliperExportSettings, CALIPER_OT_cycle_role, CALIPER_OT_open_folder, CALIPER_OT_export)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.caliper_export = bpy.props.PointerProperty(type=CaliperExportSettings)
    bpy.types.Collection.caliper_role = EnumProperty(name="Variant role", items=ROLES, default="OFF",
                                                     description="How Variants uses this collection")
    bpy.types.Collection.caliper_batch = BoolProperty(name="Export", default=False,
                                                      description="Export this collection as one model")


def unregister():
    del bpy.types.Collection.caliper_batch
    del bpy.types.Collection.caliper_role
    del bpy.types.Scene.caliper_export
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
