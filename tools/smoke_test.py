#!/usr/bin/env python3
"""Offscreen smoke test — run after every sync_from_chromiq.py:

    QT_QPA_PLATFORM=offscreen python3 tools/smoke_test.py

Verifies the standalone closure end-to-end without a display or Argyll:
the editor dialog constructs, the layout engine renders a chart from a
bundled built-in .ti1, and the i1Profiler export writes .txt/.pxf.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    from core.argyll_runner import ArgyllRunner
    from core.resource_path import resource_path
    from core.settings import AppSettings
    from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
    from ui.tabs.tab_chart import builtin_recipe_choices, comparable_presets
    from workflow import i1profiler_export, patch_generators
    from workflow.layout_engine import chart as le_chart

    settings = AppSettings()
    runner = ArgyllRunner(settings)

    # 1. The editor dialog builds completely (all panels, preset registry).
    dlg = Ti2RelayoutDialog(runner, settings)
    assert dlg.windowTitle(), "dialog built without a title"
    recipes = builtin_recipe_choices()
    assert recipes, "no built-in recipes found — assets/charts missing?"
    groups = comparable_presets(settings)
    assert groups, "no comparable presets — bundled .ti1 assets missing?"
    print(f"dialog OK — {len(recipes)} built-in recipes, "
          f"{sum(len(i) for _, i in groups)} comparable presets")

    # 2. Knut's generators produce patches.
    patches = patch_generators.rgb_cube(4)
    assert len(patches) == 64, f"rgb_cube(4) gave {len(patches)} patches"
    print(f"generators OK — rgb_cube(4) = {len(patches)} patches")

    # 3. The layout engine renders a bundled built-in chart to TIFF, no Argyll.
    ti1 = resource_path("assets/charts/knut/rgb/fulllayout/"
                        "fls_i1pro_a4_484p_1page_portrait/chart.ti1")
    assert ti1.is_file(), f"missing bundled .ti1: {ti1}"
    with tempfile.TemporaryDirectory() as td:
        le_chart.build_chart(ti1, Path(td) / "smoke",
                             instrument="i1", paper="A4")
        tiffs = sorted(Path(td).glob("smoke*.tif"))
        assert tiffs, "engine rendered no TIFF pages"
        ti2s = sorted(Path(td).glob("smoke*.ti2"))
        assert ti2s, "engine wrote no .ti2"
        print(f"engine OK — {len(tiffs)} page(s) rendered: "
              f"{', '.join(t.name for t in tiffs)}")

        # 4. i1Profiler export from the same .ti1.
        txt, pxf = i1profiler_export.export_from_ti1(ti1, Path(td),
                                                     "smoke-i1profiler")
        assert pxf.is_file(), "i1Profiler export wrote no .pxf"
        assert txt is not None and txt.is_file(), "no .txt for an RGB chart"
        print(f"i1Profiler export OK — {txt.name}, {pxf.name}")

    # 5. The editor's own save flow (Save / Export…) — loads a bundled chart
    # and writes the full deliverable. Catches missing lazily-imported
    # modules (core.file_manager was found this way).
    ti2 = resource_path("assets/charts/pharmacist/rgb/i1pro/a4/tc924/tc924.ti2")
    assert ti2.is_file(), f"missing bundled .ti2: {ti2}"
    dlg2 = Ti2RelayoutDialog(runner, settings, initial_chart=ti2)
    app.processEvents()
    assert dlg2._grid.count() > 0, "editor did not load the bundled chart"
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "smoke-save"
        dlg2._write_chart_into(target, "smoke-save")
        for suffix in (".ti2", "-i1profiler.pxf", "_01.tif"):
            assert (target / f"smoke-save{suffix}").is_file(), \
                f"save flow did not write smoke-save{suffix}"
    print(f"save flow OK — chart with {dlg2._grid.count()} patches "
          "written with exports")
    dlg2.deleteLater()

    dlg.deleteLater()
    app.processEvents()
    print("smoke test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
