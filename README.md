# Caliper Bridge for Blender

A Blender extension (4.2 and newer, made on 5.2) that checks and repairs objects with **Caliper Nightly**, from the N panel's **Caliper** tab.

- **Analyze**: exports the objects as they print (modifiers applied, world space) to a temporary binary STL, runs `caliper-nightly-cli analyze --markers` and shows what Caliper found: the panel lists the errors per object, and the viewport draws each one where it is (open edges red, non-manifold orange, flipped faces purple, degenerate yellow, duplicate cyan, sealed cavities blue). The arrows move the view from one error to the next; **Select faces** selects them in Edit Mode.
- **Repair**: runs `caliper-nightly-cli repair` on each object and imports the result as a new object, `<name>_repaired`, with the same transform and materials. The original is kept (hidden, by default).
- **Selected** checks each selected object on its own; **Whole scene** checks every visible object together, as one print plate. Repair is always per object.

## Export

The **Export** panel writes print-ready STL files to a folder (by default `Caliper export` beside the .blend), each one repaired by Caliper on the way (**Repair**), only checked (**Check**: written as it is, the panel says which need repair) or written without Caliper (**Export only**). Blender's objects are never changed.

1. **Combine selected**: the selected objects as one model (Caliper joins the parts that touch or cross).
2. **Each selected object**: one file per object.
3. **Each collection**: tick collections; each becomes one file with every object inside it.
4. **Variants**: click each collection's role. **Core** goes in every model; **One of** gives one alternative per model; **Optional** is the same plus a model without it. An alternative is an object in that collection, or a collection inside it (parts that go together). `Body` Core, `Heads` (HeadA, HeadB, HeadC) One of and `Weapon` (GunA, SwordA) Optional give 9 files: `Body_HeadA_GunA.stl`, `Body_HeadA_SwordA.stl`, `Body_HeadA.stl`, … Hidden objects count; the roles are saved in the .blend.

Before exporting, the panel shows how many models come out and can list their file names; above 50 it asks first, above 10,000 it refuses. An object used by many models is read once. The report lists every file with its result, problems first, and opens the folder. **Scale** multiplies the coordinates; **Replace files** off skips names already in the folder.

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
