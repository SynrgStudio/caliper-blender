# SPDX-License-Identifier: GPL-3.0-or-later
"""Drawing Caliper's markers in the 3D viewport: each defect where Caliper found it, colored by kind."""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import state

KINDS = {
    # kind: (label, color, the panel's icon of that color)
    "boundary": ("Open edges", (1.0, 0.25, 0.25, 1.0), "SEQUENCE_COLOR_01"),
    "nonmanifold": ("Non-manifold edges", (1.0, 0.6, 0.1, 1.0), "SEQUENCE_COLOR_02"),
    "winding": ("Flipped faces", (0.75, 0.35, 1.0, 1.0), "SEQUENCE_COLOR_06"),
    "degenerate": ("Degenerate faces", (1.0, 0.9, 0.2, 1.0), "SEQUENCE_COLOR_03"),
    "duplicate": ("Duplicate faces", (0.35, 0.9, 0.35, 1.0), "SEQUENCE_COLOR_04"),
    "cavity": ("Sealed cavities", (0.3, 0.55, 1.0, 1.0), "SEQUENCE_COLOR_05"),
}

_handle = None


def _box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    c = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    return [p for e in edges for p in (c[e[0]], c[e[1]])]


def _draw():
    settings = getattr(bpy.context.scene, "caliper", None)
    if not settings or not settings.show_markers or not state.markers:
        return
    lines, points = {}, {}
    focus = None
    for name, group in state.markers.items():
        obj = bpy.data.objects.get(name)
        if obj is None or not obj.visible_get():
            continue
        moved = np.array(obj.matrix_world, np.float64) @ group["inverse"]

        def place(p):
            return p @ moved[:3, :3].T + moved[:3, 3]
        a, b, center = place(group["a"]), place(group["b"]), place(group["center"])
        for i, kind in enumerate(group["kinds"]):
            if kind == "cavity":
                lines.setdefault(kind, []).extend(_box(a[i], b[i]))
            else:
                lines.setdefault(kind, []).extend((tuple(a[i]), tuple(b[i])))
            points.setdefault(kind, []).append(tuple(center[i]))
            if state.focus == (name, i):
                focus = tuple(center[i])

    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE" if settings.markers_through else "LESS_EQUAL")
    viewport = gpu.state.viewport_get()
    line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    line_shader.uniform_float("viewportSize", (viewport[2], viewport[3]))
    line_shader.uniform_float("lineWidth", 3.0)
    point_shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
    for kind, coords in lines.items():
        line_shader.uniform_float("color", KINDS.get(kind, ("", (1, 1, 1, 1), ""))[1])
        batch_for_shader(line_shader, "LINES", {"pos": coords}).draw(line_shader)
    gpu.state.point_size_set(7.0)
    for kind, coords in points.items():
        point_shader.uniform_float("color", KINDS.get(kind, ("", (1, 1, 1, 1), ""))[1])
        batch_for_shader(point_shader, "POINTS", {"pos": coords}).draw(point_shader)
    if focus:
        gpu.state.depth_test_set("NONE")
        gpu.state.point_size_set(16.0)
        point_shader.uniform_float("color", (1, 1, 1, 0.9))
        batch_for_shader(point_shader, "POINTS", {"pos": [focus]}).draw(point_shader)
    gpu.state.point_size_set(1.0)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
