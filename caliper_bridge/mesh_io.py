# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender meshes to binary STL and back, with numpy. Coordinates are Blender units, written as they are:
what Caliper calls millimetres is one Blender unit, as Blender's own STL export does."""
import numpy as np

STL_TRIANGLE = np.dtype([("normal", "<f4", (3,)), ("corners", "<f4", (3, 3)), ("attribute", "<u2")])


def world_triangles(obj, depsgraph):
    """The object as it prints: modifiers applied, in world space. Returns (triangles float64 (n,3,3),
    polygon index of each triangle, whether those indices are the original mesh's polygons)."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        nv, nt = len(mesh.vertices), len(mesh.loop_triangles)
        co = np.empty(nv * 3, np.float64)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(nv, 3)
        corners = np.empty(nt * 3, np.int64)
        mesh.loop_triangles.foreach_get("vertices", corners)
        polygons = np.empty(nt, np.int64)
        mesh.loop_triangles.foreach_get("polygon_index", polygons)
        same_polygons = obj.type == "MESH" and len(mesh.polygons) == len(obj.data.polygons) and not any(
            m.show_viewport for m in obj.modifiers)
    finally:
        evaluated.to_mesh_clear()
    matrix = np.array(obj.matrix_world, np.float64)
    world = co @ matrix[:3, :3].T + matrix[:3, 3]
    tris = world[corners.reshape(nt, 3)]
    if np.linalg.det(matrix[:3, :3]) < 0:  # mirrored: keep the faces pointing out, as Blender's export does
        tris = tris[:, ::-1, :]
    return tris, polygons, same_polygons


def write_stl(path, tris):
    data = np.zeros(len(tris), STL_TRIANGLE)
    data["corners"] = tris
    normal = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    data["normal"] = np.divide(normal, length, out=np.zeros_like(normal), where=length > 0)
    with open(path, "wb") as f:
        f.write(b"Blender to Caliper".ljust(80, b" "))
        f.write(np.uint32(len(tris)).tobytes())
        f.write(data.tobytes())


def read_stl(path):
    """A binary STL's triangles as float32 (n,3,3). Caliper writes binary STL only."""
    with open(path, "rb") as f:
        f.seek(80)
        count = int(np.frombuffer(f.read(4), "<u4")[0])
        data = np.frombuffer(f.read(count * STL_TRIANGLE.itemsize), STL_TRIANGLE, count)
    return data["corners"].copy()


def new_object(name, tris, like, collections):
    """A new object from world-space triangles, with the same origin, transform and materials as `like`.
    Corners at the same exact position become one vertex, the way Caliper connects them."""
    import bpy
    from mathutils import Matrix
    flat = tris.reshape(-1, 3)
    vertices, index = np.unique(flat, axis=0, return_inverse=True)
    faces = index.reshape(-1, 3)
    matrix = Matrix(like.matrix_world) if like else Matrix.Identity(4)
    m = np.array(matrix, np.float64)
    if abs(np.linalg.det(m[:3, :3])) < 1e-12:
        matrix, m = Matrix.Identity(4), np.eye(4)
    inverse = np.linalg.inv(m)
    local = vertices.astype(np.float64) @ inverse[:3, :3].T + inverse[:3, 3]
    if np.linalg.det(m[:3, :3]) < 0:
        faces = faces[:, ::-1]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(local, [], faces)
    mesh.update()
    if like and like.type == "MESH":
        for material in like.data.materials:
            mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    for collection in collections:
        collection.objects.link(obj)
    obj.matrix_world = matrix
    return obj
