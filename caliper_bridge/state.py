# SPDX-License-Identifier: GPL-3.0-or-later
"""What the extension knows between clicks. Kept in memory: a new .blend starts empty."""

cli = None          # path of the command line found, or None
cli_version = None  # its `version` document
results = []        # one dict per analyzed or repaired target, in the order the panel shows them
markers = {}        # object name -> {"inverse", "kinds", "a", "b", "center", "triangles", "polygons", "same_polygons"}
focus = None        # (object name, marker index) the view went to
job = None          # the running analysis or repair: {"title", "percent", "message"}


def clear():
    global results, markers, focus
    results, markers, focus = [], {}, None
