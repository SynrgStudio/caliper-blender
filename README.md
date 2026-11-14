# Caliper Bridge for Blender

A Blender extension (4.2 and newer, made on 5.2) that checks and repairs objects with **Caliper Nightly**, from the N panel's **Caliper** tab.

- **Analyze**: exports the objects as they print (modifiers applied, world space) to a temporary binary STL, runs `caliper-nightly-cli analyze --markers` and shows what Caliper found: the panel lists the errors per object, and the viewport draws each one where it is (open edges red, non-manifold orange, flipped faces purple, degenerate yellow, duplicate cyan, sealed cavities blue). The arrows move the view from one error to the next; **Select faces** selects them in Edit Mode.
- **Repair**: runs `caliper-nightly-cli repair` on each object and imports the result as a new object, `<name>_repaired`, with the same transform and materials. The original is kept (hidden, by default).
- **Selected** checks each selected object on its own; **Whole scene** checks every visible object together, as one print plate. Repair is always per object.

It finds the installed Caliper Nightly by itself (Windows: its installer's registry entry; Linux: the menu entry `install.sh` writes, or `PATH`). The preferences can name another folder or `caliper-nightly-cli`. Nightly has to be activated. A Nightly older than `analyze --markers` (calipernext #437) still gives the counts, without drawing where.

One Blender unit is one Caliper millimetre, as Blender's own STL export writes them.

## Build and install

```
blender --command extension build --source-dir caliper_bridge --output-dir dist
blender --command extension install-file -r user_default -e dist/caliper_bridge-<version>.zip
```

## Tests

```
python tests/run.py --cli <caliper-nightly-cli> [--old-cli <a Nightly without --markers>]
python tests/run.py --cli <caliper-nightly-cli> --window <screenshots folder>
```

Both install the extension in a throwaway Blender profile. The first runs headless; the second opens Blender's window, clicks Analyze and Repair as a user would and saves screenshots: run it on a desktop nobody is using (on the Caliper PC, through `CaliperNext/tools/run_private_validation.py`).

## License

GPL-3.0-or-later, as Blender requires of extensions. Caliper itself is a separate program: the extension only runs it and reads its files.
