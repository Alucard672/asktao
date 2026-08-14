# Runtime templates

Only templates cropped from the user's own, current game window belong here.
Never manufacture positive/actionable templates from synthetic UI. Files are PNG
and use the exact lowercase name `<state>__<target>__<scale>.png`, for example
`dialogue__skip_dialogue__1x.png`.

Capture with the read-only helper (it never focuses or clicks the window):

```console
.venv/bin/python scripts/capture_template.py \
  --state dialogue --target skip_dialogue --scale 1x \
  --box X Y WIDTH HEIGHT --dry-run
```

Remove `--dry-run` only after reviewing the reported output path and dimensions.
Every run captures the exactly titled 886×672 client in memory, rejects crops
outside that geometry, OCR-scans the crop for
email addresses and mainland-mobile-like phone numbers, and refuses overlap with
conservative chat `(0,420,390,252)` or character-ID `(690,0,196,150)` regions.
OCR failure is fail-closed. Paths containing symlinked output components are
rejected. `--dry-run` creates no source or template files. `--save-source` is a
mutually exclusive alternative used only when an explicit local full-window
artifact is required; it writes ignored `runtime/source.png` after the privacy
scan. Supplying both flags is rejected.

Allowed targets are `main_quest`, `skip_dialogue`, `npc_main_option`,
`auto_path`, `battle_auto`, `claim`, and `equip`. Sanitized blocked-state fixtures
may instead use `--fixture`; manually inspect every fixture before committing it.
