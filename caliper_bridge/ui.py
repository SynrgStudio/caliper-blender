# SPDX-License-Identifier: GPL-3.0-or-later
"""The N panel's Caliper tab: Caliper's state on top, then Check & Repair and Export."""
import os

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from . import export, overlay, state, variants

DEFECTS = [
    ("open_edges", "boundary"),
    ("non_manifold_edges", "nonmanifold"),
    ("inconsistent_winding", "winding"),
    ("degenerate_faces", "degenerate"),
    ("duplicate_faces", "duplicate"),
]

STATUS = {
    # status: (icon, label)
    "repaired": ("CHECKMARK", "Repaired"),
    "clean": ("CHECKMARK", "Clean"),
    "exported": ("CHECKMARK", "Exported"),
    "needs repair": ("ERROR", "Needs repair"),
    "errors left": ("ERROR", "Errors left"),
    "skipped": ("FORWARD", "Skipped"),
    "cancelled": ("CANCEL", "Cancelled"),
    "failed": ("CANCEL", "Failed"),
}


def redraw_panels(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()


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


_ICONS = None


def icon(name):
    """The icon, under the name this Blender gives it: the colored squares are STRIP_COLOR_* from 5.0,
    SEQUENCE_COLOR_* before."""
    global _ICONS
    if _ICONS is None:
        _ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
    if name in _ICONS:
        return name
    for old, new in (("SEQUENCE_COLOR_", "STRIP_COLOR_"), ("STRIP_COLOR_", "SEQUENCE_COLOR_")):
        if name.startswith(old) and name.replace(old, new) in _ICONS:
            return name.replace(old, new)
    return "DOT"


def _pair(layout, label, value, icon_name="NONE", factor=0.42):
    """A row with the label on the left and its value on the right."""
    row = layout.row(align=True)
    split = row.split(factor=factor, align=True)
    split.label(text=label, icon=icon(icon_name))
    right = split.row()
    right.alignment = "RIGHT"
    right.label(text=value)


def _errors(entry):
    return sum(entry.get("defects", {}).values())


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


def _job_box(layout, kind):
    job = state.job
    if not job or job.get("kind", "check") != kind:
        return
    box = layout.box()
    title = job["title"] + (": " + job["message"] if job.get("message") else "")
    percent = job.get("percent")
    if percent is not None:
        box.progress(factor=percent, type="BAR", text="%d%%" % (percent * 100))
    box.label(text=title, icon="SORTTIME")
    box.operator("caliper.cancel", icon="CANCEL")


class CALIPER_OT_toggle(Operator):
    bl_idname = "caliper.toggle"
    bl_label = "Show or hide"
    bl_description = "Show or hide the details"
    bl_options = {"INTERNAL"}

    key: StringProperty()

    def execute(self, context):
        state.expanded[self.key] = not state.expanded.get(self.key, True)
        return {"FINISHED"}


class VIEW3D_PT_caliper(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Caliper"
    bl_label = "Caliper"

    def draw_header_preset(self, context):
        self.layout.operator("preferences.addon_show", text="", icon="PREFERENCES", emboss=False).module = __package__

    def draw(self, context):
        layout = self.layout
        if not state.cli:
            box = layout.box()
            box.alert = True
            box.label(text="Caliper Nightly not found", icon="ERROR")
            col = box.column(align=True)
            col.alert = False
            col.label(text="Install Caliper Nightly, or name its")
            col.label(text="folder in the preferences (gear above).")
            box.operator("caliper.detect", icon="FILE_REFRESH")
            return
        info = state.cli_version or {}
        row = layout.row()
        row.label(text="Caliper " + info.get("version", ""), icon="CHECKMARK")
        if info.get("activation") in ("renew_required", "not_activated"):
            box = layout.box()
            box.alert = True
            box.label(text="Activate Caliper Nightly to use it here.", icon="ERROR")


class VIEW3D_PT_caliper_check(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Caliper"
    bl_label = "Check & Repair"
    bl_parent_id = "VIEW3D_PT_caliper"

    @classmethod
    def poll(cls, context):
        return state.cli is not None

    def draw(self, context):
        layout = self.layout
        settings = context.scene.caliper
        layout.row().prop(settings, "scope", expand=True)
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("caliper.analyze", icon="VIEWZOOM")
        row.operator("caliper.repair", icon="MODIFIER")
        _job_box(layout, "check")

        for i, result in enumerate(state.results):
            self._result(context, layout, result, i)

        if state.markers:
            from . import _all_markers
            box = layout.box()
            row = box.row(align=True)
            row.prop(settings, "show_markers", toggle=True, icon="HIDE_OFF" if settings.show_markers else "HIDE_ON")
            row.prop(settings, "markers_through", toggle=True, icon="XRAY")
            every = _all_markers()
            if every:
                row = box.row(align=True)
                row.operator("caliper.focus", text="", icon="TRIA_LEFT").step = -1
                at = every.index(state.focus) + 1 if state.focus in every else 0
                row.label(text=("Error %d of %d" % (at, len(every))) if at else ("%d errors drawn" % len(every)))
                row.operator("caliper.focus", text="", icon="TRIA_RIGHT").step = 1
        if state.results:
            layout.operator("caliper.clear", icon="X")

    def _header(self, layout, key, icon, title, note, alert=False):
        """A result's title row: click to open or close it."""
        open_ = state.expanded.get(key, True)
        row = layout.row(align=True)
        row.alert = alert
        op = row.operator("caliper.toggle", text="", icon="DOWNARROW_HLT" if open_ else "RIGHTARROW", emboss=False)
        op.key = key
        row.label(text=title, icon=icon)
        if note:
            sub = row.row()
            sub.alignment = "RIGHT"
            sub.label(text=note)
        return open_

    def _result(self, context, layout, result, index):
        box = layout.box()
        key = "%d:%s" % (index, result["label"])
        if result.get("error"):
            if self._header(box, key, "CANCEL", result["label"], "Failed", alert=True):
                for line in _wrap(result["error"], 40):
                    box.label(text=line)
            return
        if "repair" in result:
            doc = result.get("repair") or {}
            if result.get("clean"):
                self._header(box, key, "CHECKMARK", result["label"], "Already clean")
                return
            after = doc.get("after", {})
            left = _errors(after)
            if self._header(box, key, "CHECKMARK" if not after.get("needs_repair") else "ERROR", result["label"],
                            "Repaired" if not after.get("needs_repair") else "%s left" % format(left, ",")):
                col = box.column(align=True)
                _pair(col, "From", result.get("from", ""))
                _pair(col, "Triangles", "%s → %s" % (format(doc.get("triangles_before", 0), ","), format(doc.get("triangles_after", 0), ",")))
                _pair(col, "Time", "%.1f s" % doc.get("seconds", 0))
                self._defects(col, after.get("defects", {}))
            return
        entry = result["entry"]
        needs = entry.get("needs_repair")
        count = _errors(entry)
        note = ("%s errors" % format(count, ",")) if count else ("Needs repair" if needs else "No errors")
        if not self._header(box, key, "ERROR" if needs else "CHECKMARK", result["label"], note, alert=bool(needs)):
            return
        col = box.column(align=True)
        shown = self._defects(col, entry.get("defects", {}))
        cavities = entry.get("cavities", {})
        if cavities.get("state") == "complete" and cavities.get("count"):
            _pair(col, overlay.KINDS["cavity"][0], str(cavities["count"]), overlay.KINDS["cavity"][2], factor=0.75)
            shown = True
        bodies = entry.get("bodies")
        if bodies and bodies > 1:
            _pair(col, "Bodies", str(bodies), "MESH_DATA")
        if needs and not shown:
            col.label(text="Bodies touch or cross: Repair joins them.", icon="INFO")
        if needs and not result.get("markers"):
            col.label(text="Update Caliper Nightly to see where.", icon="INFO")
        col.separator()
        _pair(col, "Triangles", format(entry.get("triangles", 0), ","))
        size = entry.get("size_mm")
        if size:
            col.label(text="Size  " + " × ".join(_length(context, v) for v in size))
        if "volume_mm3" in entry:
            _pair(col, "Volume", _volume(context, entry["volume_mm3"]) + ("" if entry.get("volume_exact") else " ≈"))
        for name in result["objects"]:
            group = state.markers.get(name)
            if group and group["same_polygons"] and any(k != "cavity" for k in group["kinds"]):
                op = box.operator("caliper.select_faces", text="Select faces" + ("" if len(result["objects"]) == 1 else ": " + name),
                                  icon="RESTRICT_SELECT_OFF")
                op.object = name

    def _defects(self, col, defects):
        shown = False
        for key, kind in DEFECTS:
            n = defects.get(key, 0)
            if n:
                label, _, icon = overlay.KINDS[kind]
                _pair(col, label, format(n, ","), icon, factor=0.75)
                shown = True
        if not shown:
            col.label(text="No errors.", icon="CHECKMARK")
        return shown


class VIEW3D_PT_caliper_export(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Caliper"
    bl_label = "Export"
    bl_parent_id = "VIEW3D_PT_caliper"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.caliper_export
        col = layout.column(align=True)
        col.prop(settings, "mode", expand=True)
        layout.separator(factor=0.5)

        if settings.mode in ("COLLECTIONS", "VARIANTS"):
            self._collections(context, layout, settings)
        elif settings.mode == "COMBINE":
            layout.prop(settings, "combined_name", placeholder=context.active_object.name if context.active_object else "Name")

        models, problems = export.plan(context)
        if settings.mode == "VARIANTS":
            summary = export.variant_summary(context)
            if summary:
                box = layout.box()
                col = box.column(align=True)
                for line in _wrap(summary, 38):
                    col.label(text=line)
                col.label(text="= %s models" % format(len(models), ","), icon="MOD_ARRAY")
        for p in problems:
            layout.label(text=p, icon="INFO")
        if models:
            row = layout.row()
            row.prop(settings, "show_names", icon="DOWNARROW_HLT" if settings.show_names else "RIGHTARROW", emboss=False,
                     text="%s file%s" % (format(len(models), ","), "" if len(models) == 1 else "s"))
            if settings.show_names:
                names = variants.unique([variants.file_name(n) for n, _ in models])
                col = layout.box().column(align=True)
                for n, (_, objects) in list(zip(names, models))[:60]:
                    _pair(col, n + ".stl", "%d part%s" % (len(objects), "" if len(objects) == 1 else "s"), "FILE", factor=0.78)
                if len(models) > 60:
                    col.label(text="and %s more" % format(len(models) - 60, ","))

        box = layout.box()
        col = box.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(settings, "folder")
        col.prop(settings, "scale")
        col.prop(settings, "overwrite")
        box.row().prop(settings, "action", expand=True)

        row = layout.row()
        row.scale_y = 1.6
        verb = {"REPAIR": "Export & repair", "CHECK": "Export & check", "EXPORT": "Export"}[settings.action]
        row.enabled = bool(models) and (state.cli is not None or settings.action == "EXPORT")
        row.operator("caliper.export", icon="EXPORT",
                     text="%s %s model%s" % (verb, format(len(models), ","), "" if len(models) == 1 else "s") if models else verb)
        _job_box(layout, "export")
        self._report(layout)

    def _collections(self, context, layout, settings):
        variants_mode = settings.mode == "VARIANTS"
        rows = export.scene_collections(context.scene, skip_inside_roles=variants_mode)
        box = layout.box()
        if not rows:
            box.label(text="The scene has no collections.", icon="INFO")
            return
        if variants_mode:
            box.label(text="Click a role to change it:", icon="INFO")
        col = box.column(align=True)
        for c, depth in rows:
            row = col.row(align=True)
            if depth:
                row.separator(factor=1.6 * depth)
            if variants_mode:
                name, label, _, icon, _ = export.ROLE_INFO[c.caliper_role]
                inner = row.row(align=True)
                inner.active = c.caliper_role != "OFF"
                n = len(export.alternatives(c)) if c.caliper_role in ("PICK", "OPTIONAL") else 0
                inner.label(text="%s (%d)" % (c.name, n) if n else c.name, icon="OUTLINER_COLLECTION")
                button = row.row(align=True)
                button.ui_units_x = 5.2
                op = button.operator("caliper.cycle_role", text=label, icon=icon, depress=c.caliper_role != "OFF")
                op.collection = c.name
            else:
                row.prop(c, "caliper_batch", text=c.name, icon="OUTLINER_COLLECTION" if c.caliper_batch else "COLLECTION_NEW")

    def _report(self, layout):
        report = state.export
        if not report:
            return
        box = layout.box()
        counts = report["counts"]
        done = len(report["rows"])
        title = "Exporting… %d of %d" % (done, report["total"]) if report["running"] else (
            "Cancelled: %d of %d" % (done, report["total"]) if report.get("cancelled") else
            "%d file%s in %.0f s" % (report["written"], "" if report["written"] == 1 else "s", report.get("seconds", 0)))
        row = box.row()
        row.label(text=title, icon="EXPORT")
        if not report["running"] and report["written"]:
            row.operator("caliper.open_folder", text="", icon="FILE_FOLDER")
        summary = box.row(align=True)
        for status in ("repaired", "clean", "exported", "needs repair", "errors left", "skipped", "failed"):
            if counts.get(status):
                icon, label = STATUS[status]
                cell = summary.row()
                cell.alert = status in ("failed", "errors left", "needs repair")
                cell.label(text="%d %s" % (counts[status], label.lower()), icon=icon)
        key = "export-rows"
        open_ = state.expanded.get(key, len(report["rows"]) <= 12)
        row = box.row()
        op = row.operator("caliper.toggle", text="Files", icon="DOWNARROW_HLT" if open_ else "RIGHTARROW", emboss=False)
        op.key = key
        state.expanded.setdefault(key, open_)
        if not open_:
            return
        col = box.column(align=True)
        rows = report["rows"]
        problems = [r for r in rows if r["status"] in ("failed", "errors left", "needs repair")]
        shown = (problems + [r for r in rows if r not in problems])[:80]
        for r in shown:
            icon, label = STATUS.get(r["status"], ("DOT", r["status"]))
            line = col.row()
            line.alert = r["status"] in ("failed", "errors left", "needs repair")
            split = line.split(factor=0.74)
            split.label(text=r["name"], icon=icon)
            right = split.row()
            right.alignment = "RIGHT"
            right.label(text=label)
            if r.get("detail") and r["status"] not in ("repaired", "clean", "exported"):
                for text in _wrap(r["detail"], 44):
                    sub = col.row()
                    sub.alert = line.alert
                    sub.label(text="    " + text)
        if len(rows) > len(shown):
            col.label(text="and %s more" % format(len(rows) - len(shown), ","))


CLASSES = (CALIPER_OT_toggle, VIEW3D_PT_caliper, VIEW3D_PT_caliper_check, VIEW3D_PT_caliper_export)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
