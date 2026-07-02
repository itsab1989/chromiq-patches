#!/usr/bin/env python3
"""Sync the vendored ChromIQ modules into chromiq-patches.

chromiq-patches is a standalone cut of ChromIQ's chart-design tool (the
"Edit / create chart patch set" editor): Knut's patch generators, the ChromIQ
layout engine, and the i1Profiler export. The files listed in MANIFEST are
vendored **byte-identical** from a ChromIQ checkout so improvements flow
downstream with one command:

    python3 tools/sync_from_chromiq.py [path-to-ChromIQ-checkout]

(default checkout location: ../ChromIQ next to this repo)

Files NOT in the manifest are owned by this repo and never overwritten —
most importantly:

  main.py                  standalone entry point
  ui/tabs/tab_chart.py     hand-maintained shim: only the built-in-preset
                           registry + the two functions the editor imports
                           (builtin_recipe_choices / comparable_presets),
                           extracted from ChromIQ's full Create Chart tab
  core/version.py          this app's own version number

After syncing, check the shim still matches upstream's registry:

    diff <(sed -n '/^class _Ti1Preset/,/^DISABLED_BUILTIN/p' ../ChromIQ/ui/tabs/tab_chart.py) \
         <(sed -n '/^class _Ti1Preset/,/^DISABLED_BUILTIN/p' ui/tabs/tab_chart.py)

and run the smoke test:  QT_QPA_PLATFORM=offscreen python3 tools/smoke_test.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Python modules copied verbatim from ChromIQ (paths relative to both roots).
MODULES = [
    "core/__init__.py",
    "core/argyll_detect.py",
    "core/argyll_runner.py",
    "core/file_manager.py",
    "core/i18n.py",
    "core/logger.py",
    "core/platform_paths.py",
    "core/preset_store.py",
    "core/resource_path.py",
    "core/settings.py",
    "core/strip_utils.py",
    "core/webengine_shutdown.py",
    "core/webengine_warmup.py",
    "data/__init__.py",
    "data/patch_db.py",
    "ui/__init__.py",
    "ui/dialogs/__init__.py",
    "ui/dialogs/layout_options_panel.py",
    "ui/dialogs/patch_cube_dialog.py",
    "ui/dialogs/ti2_relayout_dialog.py",
    "ui/fade_scroll.py",
    "ui/gradient_overlay.py",
    "ui/light_styles.py",
    "ui/patch_cube_panel.py",
    "ui/styles.py",
    "ui/tab_header.py",
    "ui/tabs/__init__.py",
    "ui/theme.py",
    "ui/tooltip_button.py",
    "ui/widgets.py",
    "workflow/__init__.py",
    "workflow/chart_exports.py",
    "workflow/cie_data.py",
    "workflow/i1profiler_export.py",
    "workflow/i1profiler_import.py",
    "workflow/icc_info.py",
    "workflow/patch_cube.py",
    "workflow/patch_generators.py",
    "workflow/ti2_relayout.py",
    "workflow/ti3_analysis.py",
]

# Whole directories copied recursively (deleted + re-copied so removals sync).
TREES = [
    "workflow/layout_engine",
    "assets/fonts",
    "assets/charts",
    "assets/folder",
    "assets/refresh",
    "data/i18n",
]

# Individual asset files.
ASSETS = [
    "assets/app_icon.png",
    "assets/app_icon.icns",
    "assets/settings_v2.png",
    "assets/arrow_down.svg",
    "assets/arrow_down_dark.svg",
    "assets/arrow_up.svg",
    "assets/arrow_up_dark.svg",
    "assets/plotly-gl3d.min.js",
]


def main() -> int:
    src_root = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO.parent / "ChromIQ"
    if not (src_root / "ui" / "dialogs" / "ti2_relayout_dialog.py").is_file():
        print(f"error: {src_root} does not look like a ChromIQ checkout")
        return 1

    copied = 0
    for rel in MODULES + ASSETS:
        src, dst = src_root / rel, REPO / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    for rel in TREES:
        src, dst = src_root / rel, REPO / rel
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
        copied += 1
    print(f"synced {copied} entries from {src_root}")
    print("note: ui/tabs/tab_chart.py (shim), main.py and core/version.py are "
          "repo-owned and were not touched — see this script's docstring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
