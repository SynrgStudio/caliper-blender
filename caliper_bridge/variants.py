# SPDX-License-Identifier: GPL-3.0-or-later
"""Variants: every model made by taking the core parts and one alternative of each choice. No bpy here.

A choice is (name, alternatives, optional); an alternative is (name, objects). An optional choice adds
"without it". The model's name is the core's names, then each alternative's, joined with "_"."""
import itertools
import re

LIMIT = 10000  # beyond this the panel refuses: nobody means to export that many


def count(choices):
    total = 1
    for _, alternatives, optional in choices:
        total *= len(alternatives) + (1 if optional else 0)
    return total


def combinations(core_names, core_objects, choices):
    """[(name, objects)] for every combination, in order: the first choice changes slowest."""
    options = []
    for _, alternatives, optional in choices:
        options.append(list(alternatives) + ([None] if optional else []))
    out = []
    for picked in itertools.product(*options):
        names = list(core_names) + [a[0] for a in picked if a is not None]
        objects = list(core_objects)
        for a in picked:
            if a is not None:
                objects.extend(o for o in a[1] if o not in objects)
        out.append(("_".join(names) or "Model", objects))
    return out


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM%d" % i for i in range(1, 10)), *("LPT%d" % i for i in range(1, 10))}


def file_name(name):
    """A name Windows, macOS and Linux all accept as a file name."""
    clean = _BAD.sub("_", name).strip().rstrip(". ")
    if not clean or clean.upper() in _RESERVED:
        clean = (clean or "Model") + "_"
    return clean[:180]


def unique(names):
    """The same names, made unique without case (Windows): a second "Body" becomes "Body (2)"."""
    seen, out = {}, []
    for n in names:
        key = n.lower()
        if key in seen:
            seen[key] += 1
            candidate = "%s (%d)" % (n, seen[key])
            while candidate.lower() in seen:
                seen[key] += 1
                candidate = "%s (%d)" % (n, seen[key])
            seen[candidate.lower()] = 1
            out.append(candidate)
        else:
            seen[key] = 1
            out.append(n)
    return out
