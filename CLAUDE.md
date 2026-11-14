# Caliper Bridge (Blender extension)

- Separate from `calipernext` on purpose: Blender extensions are GPL; Caliper stays closed. Never copy Caliper code here, only call its command line (`docs/CLI.md` in calipernext).
- Headless tests: `python tests/run.py --cli <path to caliper-nightly-cli or caliper-cli>`; add `--old-cli` with an installed older Nightly to check the no-markers fallback.
- Window tests: `python ../CaliperNext/tools/run_private_validation.py python <abs>/tests/run.py --cli <abs cli> --window <shots>`; never on the owner's desktop.
- A Nightly CLI whose key needs renewal refuses work (exit 9); `build/caliper-cli.exe` from calipernext has the same commands and no key, use it to test.
- Blender reserves `poll` on operators: don't name instance methods `poll`. Manifest `permissions` values max 64 chars; `extension build` needs its output folder to exist.
- Python edits: binary/LF writes, as in calipernext.
