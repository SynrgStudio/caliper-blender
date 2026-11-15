# Caliper Bridge (Blender extension)

- Separate from `calipernext` on purpose: Blender extensions are GPL; Caliper stays closed. Never copy Caliper code here, only call its command line (`docs/CLI.md` in calipernext).
- Headless tests: `python tests/run.py --cli <path to caliper-nightly-cli or caliper-cli>`; add `--old-cli` with an installed older Nightly to check the no-markers fallback.
- Window tests: `python ../CaliperNext/tools/run_private_validation.py python <abs>/tests/run.py --cli <abs cli> --window <shots>`; never on the owner's desktop.
- A Nightly CLI whose key needs renewal refuses work (exit 9); `build/caliper-cli.exe` from calipernext has the same commands and no key, use it to test.
- `caliper.find_cli` order: the preferences' path (a folder: the CLI in it that works), else installed Caliper Nightly while usable, else Caliper Stable's `caliper-cli` (0.9.8+, calipernext #439), else a Nightly needing its key so the panel says so.
- Blender reserves `poll` on operators: don't name instance methods `poll`. Manifest `permissions` values max 64 chars; `extension build` needs its output folder to exist.
- Icons: the colored squares are `STRIP_COLOR_0n` in Blender 5 and `SEQUENCE_COLOR_0n` in 4.2; `ui.icon()` maps them. An unknown icon name only fails when the panel draws, so only the window test catches it.
- The window test moves the Caliper panels to the Item tab (it can't click a tab); the panel itself lives in the Caliper tab.
- Python edits: binary/LF writes, as in calipernext.
