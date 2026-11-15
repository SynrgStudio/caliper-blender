# Caliper Bridge (Blender extension)

- Separate from Caliper's source on purpose: Blender extensions are GPL; Caliper stays closed. Never copy Caliper code here, only call its command line (`caliper-cli` with no command lists it; its JSON and exit codes are a stable contract).
- Headless tests: `python tests/run.py --cli <path to caliper-nightly-cli or caliper-cli>`; add `--old-cli` with an installed older Nightly to check the no-markers fallback.
- Window tests open Blender's window and click: run them on a desktop nobody is using (on the Caliper dev PC, through Caliper's private-desktop runner), never on the owner's desktop.
- A Nightly CLI whose key needs renewal refuses work (exit 9); test with Caliper's `caliper-cli`, which has the same commands and no key.
- `caliper.find_cli` order: the preferences' path (a folder: the CLI in it that works), else installed Caliper Nightly while usable, else Caliper Stable's `caliper-cli` (0.9.8+), else a Nightly needing its key so the panel says so.
- Blender reserves `poll` on operators: don't name instance methods `poll`. Manifest `permissions` values max 64 chars; `extension build` needs its output folder to exist.
- Icons: the colored squares are `STRIP_COLOR_0n` in Blender 5 and `SEQUENCE_COLOR_0n` in 4.2; `ui.icon()` maps them. An unknown icon name only fails when the panel draws, so only the window test catches it.
- The window test moves the Caliper panels to the Item tab (it can't click a tab); the panel itself lives in the Caliper tab.
- Python edits: binary/LF writes.
