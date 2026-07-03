"""ChromIQ Patches — standalone chart patch-set / layout designer.  Entry point.

The app is ChromIQ's "Edit / create chart patch set" tool (Knut's patch
generators + the ChromIQ layout engine + i1Profiler export) cut loose from the
full profiling suite. The boot sequence below mirrors ChromIQ's main.py — the
early logging, faulthandler, WebEngine pre-import and hard-exit teardown all
exist for the same reasons documented there (issues #11/#13/#38 upstream).
"""
from __future__ import annotations

import logging
import os
import sys
import traceback

# Configure logging FIRST, before any heavy third-party imports (PyQt6, numpy,
# etc.) — a frozen bundle with a broken dylib graph must still leave a trace.
from core.logger import configure_logging, get_logger

configure_logging()
log = get_logger("chromiq-patches")


def _log_excepthook(exc_type, exc, tb):
    log.critical(
        "Uncaught exception:\n%s",
        "".join(traceback.format_exception(exc_type, exc, tb)),
    )
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_excepthook

# Native crash capture — fatal signals inside Qt/Chromium teardown never reach
# the excepthook above; faulthandler dumps every thread's stack to our own log
# directory instead. Kept at module scope so the fd stays open for the process
# lifetime.
import faulthandler  # noqa: E402

_crash_log = None
try:
    from datetime import datetime as _dt

    from core.platform_paths import log_dir as _log_dir

    _crash_dir = _log_dir()
    _crash_dir.mkdir(parents=True, exist_ok=True)
    _crash_log = open(_crash_dir / "chromiq-patches-crash.log", "a", encoding="utf-8")
    _crash_log.write(f"\n=== faulthandler armed {_dt.now():%Y-%m-%d %H:%M:%S} ===\n")
    _crash_log.flush()
    faulthandler.enable(file=_crash_log, all_threads=True)
except Exception:
    log.debug("Could not arm faulthandler to crash log; using stderr", exc_info=True)
    faulthandler.enable()

log.info(
    "ChromIQ Patches starting; python=%s platform=%s frozen=%s argv=%s",
    sys.version.split()[0],
    sys.platform,
    getattr(sys, "frozen", False),
    sys.argv,
)

if sys.platform == "win32":
    # Own taskbar identity before any window exists (see ChromIQ main.py).
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ChromIQ.Patches"
        )
    except Exception:
        log.debug("Could not set Windows AppUserModelID", exc_info=True)

    # Windows ARM: re-enable WebGL past the GPU blocklist, keep the software
    # compositor so the bypass doesn't break rendering.
    _existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    _extra = "--ignore-gpu-blocklist --disable-gpu-compositing"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{_existing} {_extra}".strip()

try:
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    # QtWebEngine must be imported before QApplication is instantiated.
    # Optional: without it only the 3D patch-cube preview is disabled.
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        log.warning("QtWebEngine not available — 3D patch cube will be disabled")

    from PyQt6.QtGui import QFontDatabase
    from core.resource_path import resource_path
    from core.settings import AppSettings
    from ui.dialogs.ti2_relayout_dialog import Ti2RelayoutDialog
    from ui.styles import WinButtonLayoutStyle
    from ui.theme import apply_appearance
    from ui.widgets import ButtonFontFilter, GroupBoxSurfaceFilter, TooltipWrapFilter
except BaseException:
    log.exception("Fatal error importing application modules")
    raise


def main() -> int:
    from core.version import APP_VERSION

    app = QApplication(sys.argv)
    app.setApplicationName("ChromIQ Patches")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("ChromIQ")
    app.setApplicationDisplayName("ChromIQ Patches — Chart Designer")

    try:
        for font_path in resource_path("assets/fonts").glob("*.ttf"):
            QFontDatabase.addApplicationFont(str(font_path))
    except Exception:
        pass  # fonts dir missing — app falls back to system fonts

    app.setStyle(WinButtonLayoutStyle("Fusion"))

    _btn_font_filter = ButtonFontFilter(app)
    app.installEventFilter(_btn_font_filter)

    _gb_surface_filter = GroupBoxSurfaceFilter(app)
    app.installEventFilter(_gb_surface_filter)

    _tooltip_wrap_filter = TooltipWrapFilter(app)
    app.installEventFilter(_tooltip_wrap_filter)

    # Settings are shared with ChromIQ (same QSettings scope + preset store),
    # deliberately: charts and presets designed here appear in ChromIQ and
    # vice versa, and an already-configured Argyll path / language / theme
    # carries over. Without ChromIQ installed you simply start fresh.
    settings = AppSettings()

    from core.i18n import install_qt_translator, set_language
    set_language(settings.get("language", "en"))
    install_qt_translator(app)

    # Standalone catalog overlay: the vendored data/i18n/ is overwritten on
    # every sync_from_chromiq.py run, so translations for the strings that
    # exist only in this app live in repo-owned data/i18n_standalone/ and are
    # merged into the loaded catalog here.
    import json as _json
    import core.i18n as _i18n
    _code = settings.get("language", "en")
    _overlay = resource_path(f"data/i18n_standalone/{_code}.json")
    if _code != "en" and _overlay.is_file():
        try:
            _i18n._catalog.update(_json.loads(_overlay.read_text(encoding="utf-8")))
        except Exception:
            log.warning("standalone i18n overlay failed to load", exc_info=True)

    appearance = settings.get("appearance", "auto")
    apply_appearance(app, None, appearance)

    icon_path = resource_path("assets/app_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Standalone wording overrides, applied at tr() lookup time so the
    # vendored editor stays byte-identical: everywhere the editor teaches
    # ChromIQ's button names ("Save & apply…", "Apply / Save…", the Create
    # Chart tab), the standalone says "Save / Export…" instead. Keyed by the
    # EXACT source string; anything else falls through to the normal catalog.
    # Installed before the dialog is built — tooltips resolve at construction.
    import ui.dialogs.ti2_relayout_dialog as _editor_mod
    _orig_tr = _editor_mod.tr
    _REWORDS = {
        ("If you close now they'll be lost. Use “Save As…” or "
         "“Save & apply…” first to keep them."):
            ("If you close now they'll be lost. Use “Save / Export…” "
             "first to keep them."),
        ("Overwrite the chart currently loaded in the Create Chart tab with "
         "this layout — or Save As to export the full chart to a folder you "
         "pick, without leaving the editor."):
            ("Save the chart to a folder you pick — the print-ready TIFF "
             "pages plus the .ti1 patch set and the i1Profiler export files."),
        ("Close the editor without saving. If the layout has unsaved "
         "changes you'll be asked to confirm first; “Apply / Save…” "
         "keeps your work."):
            ("Close the editor without saving. If the layout has unsaved "
             "changes you'll be asked to confirm first; “Save / Export…” "
             "keeps your work."),
    }

    # The New-chart ⓘ help documents the (here removed) "Seed from targen"
    # and "Blank canvas" source modes. The help is one huge catalog key, so
    # instead of rewording the key, edit the TRANSLATED text: every language
    # keeps each mode on its own "    – …" bullet line (targen carries the
    # word "targen" verbatim; Blank canvas is the first bullet in all 12
    # catalogs), and the ways-count is a one-word swap per language (each
    # pair only ever matches its own text). ChromIQ itself dropped Blank
    # canvas upstream, so a future vendor sync brings a source with three
    # ways and no blank bullet — both the "Blank canvas" guard and the
    # three-ways pairs below keep this correct on either side of that sync.
    _WAYS_COUNT = (
        ("four ways", "two ways"), ("three ways", "two ways"),
        ("vier Wege", "zwei Wege"), ("drei Wege", "zwei Wege"),
        ("cuatro formas", "dos formas"), ("tres formas", "dos formas"),
        ("quatre méthodes", "deux méthodes"),
        ("trois méthodes", "deux méthodes"),
        ("quattro modi", "due modi"), ("tre modi", "due modi"),
        ("4 つの方法", "2 つの方法"), ("3 つの方法", "2 つの方法"),
        ("vier manieren", "twee manieren"), ("drie manieren", "twee manieren"),
        ("fire måter", "to måter"), ("tre måter", "to måter"),
        ("cztery sposoby", "dwa sposoby"), ("trzy sposoby", "dwa sposoby"),
        ("quatro formas", "duas formas"), ("três formas", "duas formas"),
        ("четыре способа", "два способа"), ("три способа", "два способа"),
        ("fyra sätt", "två sätt"), ("tre sätt", "två sätt"),
        ("四种方式", "两种方式"), ("三种方式", "两种方式"),
    )

    def _tr_standalone(text: str) -> str:
        hit = _REWORDS.get(text)
        # The reworded English is itself a tr() key: once the language
        # catalogs carry translations for the standalone strings, they show
        # up here automatically instead of pinning these lines to English.
        out = _orig_tr(hit) if hit is not None else _orig_tr(text)
        if "Seed from targen — enter a number" in text:
            drop_blank = "Blank canvas" in text   # pre-sync source only
            kept = []
            for line in out.split("\n"):
                if line.lstrip().startswith("–"):
                    if "targen" in line:
                        continue
                    if drop_blank:   # Blank canvas leads the bullet list
                        drop_blank = False
                        continue
                kept.append(line)
            out = "\n".join(kept)
            for many, two in _WAYS_COUNT:
                out = out.replace(many, two)
        return out

    _editor_mod.tr = _tr_standalone

    # New chart / Add patches: put "Generate colour sets" and its sub-options
    # at the top of the Patches box — in the standalone it's the lead way to
    # build a chart (paste/blank are the alternatives, layout comes later).
    # Done by moving the two layout items (mode radio + generate panel) to the
    # front at construction time, via subclasses installed over the module
    # attributes — the vendored dialog classes stay untouched.
    def _generate_sets_first(d) -> None:
        try:
            box = d._mode_generate.parentWidget()
            sl = box.layout()
            idx = next(i for i in range(sl.count())
                       if sl.itemAt(i).widget() is d._mode_generate)
            panel = sl.takeAt(idx + 1)   # the generators sub-panel (a layout)
            radio = sl.takeAt(idx)       # the "Generate colour sets" radio
            sl.insertItem(0, radio)
            sl.insertItem(1, panel)
        except Exception:
            log.exception("could not reorder Generate colour sets to the top")

    # Unchecked radio rings: the vendored editor's scoped stylesheets draw
    # them with palette(mid)/palette(base), which is nearly invisible in dark
    # mode. ChromIQ fixes this upstream (_unchecked_indicator_css in the
    # editor) — mirror it here until the next vendor sync by appending a
    # later (thus winning) base rule with explicit per-theme colours; the
    # magenta :checked rule keeps outranking it (pseudo-state specificity).
    def _radio_ring_qss() -> str:
        from ui.theme import resolve_mode
        light = resolve_mode(settings.get("appearance", "auto")) == "light"
        border, fill = (("#b0aba4", "#ffffff") if light
                        else ("#4a4a4a", "#1f1f1f"))
        return ("\nQRadioButton::indicator { width: 14px; height: 14px;"
                " border: 1px solid " + border + "; border-radius: 8px;"
                " background: " + fill + "; }")

    # "Seed from targen" and "Blank canvas" are removed from the New-chart
    # window: targen is the one feature that needs ArgyllCMS installed, and
    # the blank canvas duplicates what the editor itself does — while the
    # generators cover both. Hide the radios and the patch-count row; the
    # widgets stay constructed so the vendored persistence code keeps
    # working. (The Add-patches window has neither mode — nothing to remove
    # there. ChromIQ dropped Blank canvas upstream too, so after the next
    # vendor sync the _mode_blank getattr simply finds nothing.)
    def _drop_targen_seed(d) -> None:
        try:
            sl = d._mode_seed.parentWidget().layout()
            for i in range(sl.count()):
                row = sl.itemAt(i).layout()
                if row and any(row.itemAt(j).widget() is d._count
                               for j in range(row.count())):
                    for j in range(row.count()):
                        w = row.itemAt(j).widget()
                        if w is not None:
                            w.hide()
                    break
            d._mode_seed.hide()
            blank = getattr(d, "_mode_blank", None)
            if blank is not None:
                blank.hide()
            if d._mode_seed.isChecked() or (blank is not None
                                            and blank.isChecked()):
                d._mode_generate.setChecked(True)
        except Exception:
            log.exception("could not remove the targen seed option")

    _OrigNewChart = _editor_mod._NewChartDialog
    _OrigAddPatches = _editor_mod._AddPatchesDialog

    class _StandaloneNewChartDialog(_OrigNewChart):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            _generate_sets_first(self)
            _drop_targen_seed(self)
            self.setStyleSheet(self.styleSheet() + _radio_ring_qss())

        def _apply_gen_state(self, st):
            # Saved window state, "Load setup from preset" recipes (the preset
            # store is shared with ChromIQ) and the factory defaults can all
            # carry mode "seed" or "blank" — land those on the generators
            # instead of a hidden radio.
            if isinstance(st, dict) and st.get("mode") in ("seed", "blank"):
                st = {**st, "mode": "generate"}
            super()._apply_gen_state(st)

    class _StandaloneAddPatchesDialog(_OrigAddPatches):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            _generate_sets_first(self)
            self.setStyleSheet(self.styleSheet() + _radio_ring_qss())

    _editor_mod._NewChartDialog = _StandaloneNewChartDialog
    _editor_mod._AddPatchesDialog = _StandaloneAddPatchesDialog

    # The standalone ALWAYS lays charts out with the built-in engine — TIFFs
    # without printtarg, so no Argyll is needed to design and save. ChromIQ
    # gates the engine behind use_chromiq_layout_engine (default OFF, and the
    # QSettings store is shared with ChromIQ), so pin it at the reading site
    # with a proxy instead of writing the shared key: everything else reads
    # and writes straight through.
    class _ForceEngineSettings:
        def __init__(self, inner):
            self._inner = inner

        def get(self, key, default=None):
            if key == "use_chromiq_layout_engine":
                return True
            return self._inner.get(key, default)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    # The editor takes an ArgyllRunner as its first argument but never uses
    # it (it shells printtarg via _RegenWorker, and the standalone renders
    # engine-only anyway) — with the targen seed mode removed, nothing in
    # this app touches ArgyllCMS, so no runner is constructed at all.
    dlg = Ti2RelayoutDialog(None, _ForceEngineSettings(settings))
    apply_appearance(app, dlg, settings.get("appearance", "auto"))

    # As ChromIQ's tool it runs as a modal-ish QDialog; as THE app window it
    # must behave like a normal top-level window. Replacing the Dialog window
    # class with Window (not just adding button hints) is what lets macOS
    # minimize it to the Dock — a Qt.Dialog window ignores the minimize
    # button there. Must be set before show().
    from PyQt6.QtCore import Qt as _Qt
    dlg.setWindowFlags(_Qt.WindowType.Window
                       | _Qt.WindowType.WindowMinimizeButtonHint
                       | _Qt.WindowType.WindowMaximizeButtonHint
                       | _Qt.WindowType.WindowCloseButtonHint)
    dlg.setWindowTitle("ChromIQ Patches")

    # Standalone wording: there is no Create Chart tab to "apply" to — the
    # footer button saves the chart folder / exports the hand-off files.
    from core.i18n import tr as _tr
    if hasattr(dlg, "_apply_btn"):
        dlg._apply_btn.setText(_tr("Save / Export…").replace("&", "&&"))

    # Standalone save prompt: chart name + location + Browse… (mirroring the
    # vendored editor prompt, minus its descriptive-prefix machinery) instead
    # of Qt's built-in file browser. The browser was a poor fit for
    # folder-shaped saves: its accept button turns into "Open" whenever the
    # typed name matches an existing folder (e.g. re-saving a chart) and
    # navigates into it instead of saving, and its toolbar arrows ignore the
    # dark theme. Browse… uses ChromIQ's open_dir_dialog, which themes them.
    # The instance attribute shadows the vendored _prompt_save_as_name; the
    # chart is written as <location>/<name>/<name>.*.
    def _basic_save_prompt() -> "tuple[str, str] | None":
        import re
        from pathlib import Path as _P
        from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                                     QHBoxLayout, QLabel, QLineEdit,
                                     QMessageBox, QPushButton, QSizePolicy,
                                     QVBoxLayout)
        from core.i18n import tr
        from ui.styles import SPEC_MAGENTA
        from ui.widgets import confirm, open_dir_dialog

        d = QDialog(dlg)
        d.setWindowTitle(tr("Save chart as…"))
        d.setMinimumWidth(580)
        # The editor's magenta accent instead of the app-wide cyan — the same
        # controls _install_magenta_accents covers (checked/hovered boxes,
        # focused inputs), scoped to this dialog.
        d.setStyleSheet(f"""
            QCheckBox::indicator:checked {{
                background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
            }}
            QCheckBox::indicator:hover {{ border-color: {SPEC_MAGENTA}; }}
            QLineEdit:focus {{ border-color: {SPEC_MAGENTA}; }}
        """)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        heading = QLabel(tr("Save this chart to a folder"), d)
        heading.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(heading)
        sub = QLabel(tr("The name becomes both the folder and the chart's "
                        "file names."), d)
        sub.setWordWrap(True)
        lay.addWidget(sub)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(tr("Chart name:"), d))
        # Plain app-branded default instead of the layout-derived name
        # (ColorMunki-A4-495p-…) — the user names the chart, not the geometry.
        name_edit = QLineEdit("chromiq-patches-chart", d)
        name_edit.selectAll()
        name_row.addWidget(name_edit, 1)
        lay.addLayout(name_row)

        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel(tr("Location:"), d))
        start = (settings.get("patches_save_location", "")
                 or settings.get("custom_output_path", "")
                 or str(_P.home() / "ChromIQ"))
        loc_edit = QLineEdit(start, d)
        loc_row.addWidget(loc_edit, 1)
        browse = QPushButton(tr("Browse…"), d)
        browse.setSizePolicy(QSizePolicy.Policy.Fixed,
                             QSizePolicy.Policy.Fixed)
        browse.clicked.connect(lambda: (
            (lambda p: loc_edit.setText(p) if p else None)(
                open_dir_dialog(d, tr("Choose a folder"),
                                start_dir=loc_edit.text() or start))))
        loc_row.addWidget(browse)
        lay.addLayout(loc_row)

        # Optional second deliverable: the same chart with its patch ORDER
        # re-arranged for maximum neighbour/strip contrast (see
        # standalone_shuffle.py) — written next to the main save.
        shuffle_cb = QCheckBox(
            tr("Also save a shuffled copy (for i1Profiler)"), d)
        shuffle_cb.setToolTip(tr(
            "Writes a second version of this chart into a “shuffled” "
            "subfolder, with the patch order re-arranged for the best "
            "possible contrast between neighbouring patches and between "
            "strips. Use that version in i1Profiler — it measures an "
            "imported patch set exactly in file order and has no shuffle of "
            "its own, so a chart saved in designed order (ramps, cube order) "
            "puts look-alike colours side by side and misreads easily. "
            "The main save keeps your designed order."))
        shuffle_cb.setChecked(
            bool(settings.get("patches_save_shuffled_copy", False)))
        lay.addWidget(shuffle_cb)

        def _clean_name() -> str:
            # Same sanitisation as the vendored prompt — the name becomes
            # folder + file stems, so spaces turn into hyphens etc.
            raw = name_edit.text().strip()
            clean = re.sub(r"\s+", "-", raw)
            return re.sub(r"[^\w\-.]", "_", clean).strip("._-") or "chart"

        def _on_save() -> None:
            location = loc_edit.text().strip() or start
            target = _P(location) / _clean_name()
            if target.exists():
                choice = confirm(
                    d, tr("Overwrite existing folder?"),
                    tr("'{name}' already exists in:\n  {folder}\n\n"
                       "Overwrite it?").format(name=target.name,
                                               folder=target.parent),
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if choice != QMessageBox.StandardButton.Yes:
                    return
            d.accept()

        bb = QDialogButtonBox(d)
        ok_btn = bb.addButton(tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = bb.addButton(tr("Cancel"),
                                  QDialogButtonBox.ButtonRole.RejectRole)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(_on_save)
        cancel_btn.clicked.connect(d.reject)
        lay.addWidget(bb)
        name_edit.setFocus()

        if d.exec() != QDialog.DialogCode.Accepted:
            return None
        location = loc_edit.text().strip() or start
        settings.set("patches_save_shuffled_copy", shuffle_cb.isChecked())
        settings.set("patches_save_location", location)
        return (_clean_name(), location)

    dlg._prompt_save_as_name = _basic_save_prompt

    # The saved-confirmation popup's randomised-tag note ("Left untagged — …"/
    # "Tagged as randomised — …") is ChromIQ-measure-flow guidance that doesn't
    # apply here — layout and randomisation happen later, elsewhere. Keep the
    # method's side effect (the .ti2 still gets tagged when safe, so the saved
    # chart behaves identically downstream) but drop the note from the popup.
    _orig_tag_note = dlg._maybe_tag_randomised

    def _quiet_tag_randomised(ti2):
        _orig_tag_note(ti2)
        return ""

    dlg._maybe_tag_randomised = _quiet_tag_randomised

    # Standalone deliverable: the page TIFFs (visual reference — rendered by
    # the built-in engine, no Argyll involved), the .ti1 patch set, the colour
    # list and the i1Profiler files. ChromIQ's measure-flow artefacts
    # (<name>.ti2, meta.json, the _spacer_twin render) are dropped after the
    # vendored writer finishes; the popup's first line is reworded to match.
    import shutil as _shutil
    _orig_write_chart = dlg._write_chart_into

    def _write_shuffled_copy(target: "Path", name: str) -> str:
        """Write the contrast-shuffled second deliverable into
        ``<target>/shuffled/`` as ``<name>-shuffled.*``.

        The patch DATA order is permuted (standalone_shuffle), not just the
        on-sheet placement — so the .ti1, the colour list, the i1Profiler
        .txt/.pxf AND the TIFF pages all carry the mixed order. i1Profiler
        lays an imported set out exactly in list order and can't shuffle it,
        which is why the layout-only randomisation wouldn't help it. Must run
        while ``<name>.channels.json`` still exists (it carries the strip
        length the contrast scoring needs)."""
        import json as _json
        from standalone_shuffle import contrast_report, contrast_shuffle
        program = dlg._program_from_grid()
        if len(program) < 3 or dlg._spec is None:
            return ""
        steps = 0
        try:
            layout = _json.loads(
                (target / f"{name}.channels.json").read_text())["layout"]
            steps = int(layout.get("steps_in_pass") or 0)
        except Exception:  # noqa: BLE001
            log.warning("shuffled copy: no steps_in_pass — optimising "
                        "consecutive contrast only", exc_info=True)
        steps = steps or len(program)
        shuffled = contrast_shuffle(program, steps)
        sub = target / "shuffled"
        sub.mkdir(parents=True, exist_ok=True)
        sname = f"{name}-shuffled"
        from workflow import ti2_relayout as _R
        ti1 = _R.write_ti1(dlg._spec, shuffled, sub / f"{sname}.ti1")
        # Same engine recipe as the main save → identical geometry; the data
        # order IS the randomisation, so the layout stays sequential.
        from workflow.layout_engine import chart as _le_chart
        kw = dlg._engine_panel.get_recipe().build_kwargs()
        kw["randomize"] = False
        _le_chart.build_chart(str(ti1), sub / sname, project=sname, **kw)
        from workflow.chart_exports import write_sidecars
        write_sidecars(ti1, sub, sname)
        for leftover in (sub / f"{sname}.ti2", sub / f"{sname}.strips.json"):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
        before, after = (contrast_report(p, steps)
                         for p in (program, shuffled))
        log.info("shuffled copy %s: min neighbour contrast %.1f -> %.1f, "
                 "strip symmetry %.1f -> %.1f, confusability %.1f -> %.1f",
                 sname, before[0], after[0], before[1], after[1],
                 before[2], after[2])
        return _tr("Shuffled copy (best patch/strip contrast): {folder}"
                   ).format(folder=f"shuffled/{sname}")

    def _standalone_write_chart(target, name):
        msg = _orig_write_chart(target, name)
        target = Path(target)
        shuffle_note = ""
        if settings.get("patches_save_shuffled_copy", False):
            try:
                shuffle_note = _write_shuffled_copy(target, name)
            except Exception:  # noqa: BLE001 — never abort the main save
                log.exception("shuffled copy failed")
                shuffle_note = _tr("The shuffled copy could not be written — "
                                   "the chart itself was saved.")
        _shutil.rmtree(target / "_spacer_twin", ignore_errors=True)
        for leftover in (target / f"{name}.ti2", target / "meta.json",
                         target / f"{name}.channels.json"):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
        pages = len(list(target.glob(f"{name}_*.tif")))
        page_word = _tr("{n} page TIFFs").format(n=pages) if pages != 1 \
            else _tr("1 page TIFF")
        head = _tr("Saved {name} ({pages} + patch files) to {folder}").format(
            name=name, pages=page_word, folder=target)
        rest = [l for l in msg.splitlines()[1:] if l.strip()]
        if shuffle_note:
            rest.append(shuffle_note)
        return "\n".join([head] + rest)

    dlg._write_chart_into = _standalone_write_chart

    # --- Engine-only rendering -------------------------------------------
    # In this ChromIQ build the editor's engine branch (_engine_active) is
    # gated on the engine layout panel being visible, but the panel is
    # permanently hidden (#93: the editor is a pure patch-set tool) — so
    # preview AND save silently fell back to printtarg: printtarg strip-label
    # font, chart text on the right, and a hard Argyll dependency. The
    # standalone renders with the engine, full stop.
    def _engine_always_active() -> bool:
        return dlg._spec is not None and dlg._engine_panel is not None

    dlg._engine_active = _engine_always_active

    # The engine layout panel is hidden in this build, so nothing lets the
    # user pick the layout instrument — whatever the loaded chart or the
    # last-used state carried would silently decide the strip geometry.
    # Pin the standalone to the i1Pro layout: get_recipe() is the single
    # choke point both the preview and the save renders read from.
    _orig_get_recipe = dlg._engine_panel.get_recipe

    def _i1pro_recipe():
        rec = _orig_get_recipe()
        rec.instrument = "i1"
        return rec

    dlg._engine_panel.get_recipe = _i1pro_recipe

    # Preview: skip the printtarg regen pass entirely — it only existed to
    # seed the printtarg preview; the engine preview derives everything from
    # the grid. (Callers that pass save_to use the old printtarg save path,
    # which the standalone never does — kept intact just in case.)
    _orig_regenerate = dlg._regenerate

    def _engine_regenerate(save_to=None) -> None:
        if save_to is None and _engine_always_active():
            dlg._do_engine_preview()
            dlg._status.setText(dlg._status.text() or "")
            return
        _orig_regenerate(save_to)

    dlg._regenerate = _engine_regenerate

    # A loaded .ti2 is patch data, not a layout to preserve — ChromIQ keeps
    # printtarg charts printtarg for geometry fidelity, the standalone lays
    # the same patches out with the engine. Seed the engine panel from the
    # chart's instrument/paper (same defaults the New-chart path uses).
    _orig_load_chart = dlg._load_chart_from

    def _load_as_patch_data(path) -> bool:
        ok = _orig_load_chart(path)
        if ok and dlg._loaded_printtarg_chart:
            dlg._loaded_printtarg_chart = False
            try:
                from workflow.layout_engine.presets import default_recipe
                # Always the i1Pro layout (see the get_recipe pin above) —
                # seeding with it too keeps the instrument-default margins
                # consistent with the forced instrument.
                rec = default_recipe("i1", dlg._spec.paper_flag)
                rec.randomize = False
                dlg._engine_panel.set_recipe(rec)
            except Exception:
                log.exception("engine-panel seed for loaded .ti2 failed")
            dlg._do_engine_preview()
        return ok

    dlg._load_chart_from = _load_as_patch_data

    # Same radio-ring fix for the editor's own controls panel (it re-declares
    # the indicator with palette(mid) too). Re-appended from the snapshotted
    # base on every theme switch so the ring colours follow the mode.
    _panel_base_qss = dlg._controls_panel.styleSheet()

    def _fix_editor_radio_rings() -> None:
        dlg._controls_panel.setStyleSheet(_panel_base_qss + _radio_ring_qss())

    _fix_editor_radio_rings()

    # The main window's own checkboxes and inputs (the grid-chrome row above
    # the patch grid — "Show patch number", "Show gap between patches", the
    # gap/zoom spinboxes) sit OUTSIDE the magenta-styled controls panel, so
    # they fell back to the app-wide cyan accent. Scope the editor's magenta
    # (the same rule set the controls panel carries) onto the whole dialog;
    # nearer stylesheets — the controls panel and the New-chart/Add
    # subclasses — keep precedence for their own widgets, with the same
    # accent anyway. Colour constants are theme-independent, so this needs
    # no re-application on theme switch.
    from ui.styles import SPEC_MAGENTA
    dlg.setStyleSheet(dlg.styleSheet() + f"""
        QCheckBox::indicator:checked {{
            background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
        }}
        QCheckBox::indicator:hover {{ border-color: {SPEC_MAGENTA}; }}
        QCheckBox::indicator:checked:disabled {{
            background: #4a4a4a; border-color: #4a4a4a;
        }}
        QRadioButton::indicator:checked {{
            background: {SPEC_MAGENTA}; border-color: {SPEC_MAGENTA};
        }}
        QRadioButton::indicator:checked:disabled {{
            background: #4a4a4a; border-color: #4a4a4a;
        }}
        QLineEdit:focus, QComboBox:focus,
        QSpinBox:focus, QDoubleSpinBox:focus {{
            border-color: {SPEC_MAGENTA};
        }}
        QComboBox QAbstractItemView {{
            selection-background-color: {SPEC_MAGENTA};
            selection-color: white;
        }}
        QComboBox QAbstractItemView::item:hover {{
            background: {SPEC_MAGENTA}; color: white;
        }}
    """)

    # Standalone-only bottom bar: version + attribution + settings gear,
    # appended below the editor's own footer. The vendored dialog stays
    # byte-identical to ChromIQ's (tools/sync_from_chromiq.py), so
    # standalone-only chrome like this lives here in main.py. Helper-text
    # colour is theme-aware — must stay legible in BOTH light and dark mode.
    from PyQt6.QtCore import Qt, QSize
    from PyQt6.QtWidgets import (
        QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
        QToolButton, QVBoxLayout, QWidget,
    )
    from PyQt6.QtGui import QPixmap
    from core.i18n import available_languages, tr
    from pathlib import Path
    from ui.theme import resolve_mode
    from ui.widgets import (
        NoScrollComboBox, apply_themed_icons,
        reapply_groupbox_surface, reapply_input_stylesheet,
    )

    credit = QLabel(
        "Based on an original idea by Knut Georg Larsson — "
        "developed together with Sebastian Reiprich", dlg)
    credit.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    credit.setToolTip(
        "ChromIQ Patches grew out of Knut Georg Larsson's idea for a patch "
        "generator that doesn't depend on ArgyllCMS targen. Knut and "
        "Sebastian Reiprich designed it together; Sebastian wrote the code "
        "with Claude. The chart engine is shared with ChromIQ "
        "(github.com/itsab1989/ChromIQ).")

    # Masthead wordmark: the editor's title becomes "ChromIQ Patches" with
    # ChromIQ's brand treatment — "IQ" in Instrument Serif Italic and the
    # masthead accent (#ff4573, same in both modes; see ui/masthead_header.py).
    # The italic face is the real InstrumentSerif-Italic.ttf loaded above.
    for _lbl in dlg.findChildren(QLabel):
        if _lbl.text() == _tr("Arrange and recolour your patches"):
            _lbl.setTextFormat(Qt.TextFormat.RichText)
            _lbl.setText(
                'Chrom<span style="color:#ff4573; font-style:italic;">IQ</span>'
                ' Patches')
            _lbl.setStyleSheet(
                "background: transparent;"
                " font-family: 'Instrument Serif'; font-size: 34px;")
            break

    # The masthead ⓘ tooltip is ChromIQ's welcome text — it talks about the
    # Create Chart tab, Apply/Save and the (here hidden) force-tag option.
    # Replace it with the standalone story. Not tr()-wrapped upstream, so the
    # wording override can't catch it; swap the button's stored texts instead.
    from ui.tooltip_button import TooltipButton
    for _tip in dlg.findChildren(TooltipButton):
        if getattr(_tip, "_title", "") == "Chart patch set editor":
            _tip._title = _tr("ChromIQ Patches — chart patch set editor")
            _tip._body = _tr(
                "Welcome to ChromIQ Patches! This is where you build the PATCH "
                "SET for your chart — the collection of little colour squares "
                "(we call each one a \"patch\") that will be measured. You "
                "choose which colours are in the set, what order they're in, "
                "and you can recolour, add or remove them.\n\n"
                "The page layout is handled for you: when you save, ChromIQ "
                "Patches lays the set out with its built-in chart engine — no "
                "other software is needed.\n\n"
                "Don't worry — you can't break anything here. Nothing is "
                "printed or measured until you choose to.\n\n"
                "Two areas to know about:\n\n"
                "• The patch grid fills most of the window: every colour is a "
                "small square. This is your workbench — drag squares around to "
                "reorder them, click to select, and recolour or add and remove "
                "patches. Use the controls above the grid to show or hide the "
                "patch numbers and the gaps between swatches.\n\n"
                "• The controls on the right let you add or remove patches, "
                "generate whole colour sets, recolour a selection, and save.\n\n"
                "A typical session goes: start a new patch set (or load one), "
                "arrange and recolour the patches, then Save / Export… — you "
                "get print-ready TIFF pages, the .ti1 patch set, a colour "
                "list, and files you can import straight into i1Profiler.")
            _tip.setToolTip(_tip._title + "\n\n" + _tr("Click for details"))
            break

    version_lbl = QLabel(f"v{APP_VERSION}", dlg)

    def _style_credit() -> None:
        col = "#b8b8b8" if resolve_mode(settings.get("appearance", "auto")) == "dark" else "#4a4a4a"
        qss = f"color: {col}; font-size: 11px; padding-top: 2px;"
        credit.setStyleSheet(qss)
        version_lbl.setStyleSheet(qss)

    def _apply_dialog_theme(mode_setting: str) -> None:
        """Mirror MainWindow.apply_theme for the standalone dialog: the global
        QSS/palette swap alone leaves widget-LOCAL styles stale — most visibly
        the NoScroll spin/combo boxes, whose input-background rule is
        snapshotted per-widget at construction (ChromIQ never live-switches
        the theme with this editor open, so only the standalone hits it)."""
        mode = apply_appearance(app, dlg, mode_setting)
        for w in dlg.findChildren(QWidget):
            fn = getattr(w, "set_appearance", None)
            if callable(fn):
                try:
                    fn(mode)
                except Exception:
                    pass
        apply_themed_icons(dlg)
        reapply_groupbox_surface(dlg)
        reapply_input_stylesheet(dlg)
        _fix_editor_radio_rings()
        _style_credit()
        _refresh_gear_icon()

    def _open_settings() -> None:
        """Minimal standalone preferences: language and appearance —
        everything else the editor needs lives in the editor itself.
        (Nothing here touches ArgyllCMS any more: with the targen seed mode
        removed, the app never runs an Argyll binary.)"""
        sdlg = QDialog(dlg)
        sdlg.setWindowTitle(tr("Preferences"))
        sdlg.setMinimumWidth(520)
        lay = QVBoxLayout(sdlg)
        form = QFormLayout()

        lang_combo = NoScrollComboBox(sdlg)
        lang_combo.addItem("English", "en")
        for code, name in available_languages():
            lang_combo.addItem(name, code)
        cur = settings.get("language", "en")
        idx = lang_combo.findData(cur)
        lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(tr("Language:"), lang_combo)

        mode_combo = NoScrollComboBox(sdlg)
        for label, value in ((tr("Auto (follow system)"), "auto"),
                             (tr("Light"), "light"),
                             (tr("Dark"), "dark")):
            mode_combo.addItem(label, value)
        midx = mode_combo.findData(settings.get("appearance", "auto"))
        mode_combo.setCurrentIndex(midx if midx >= 0 else 0)
        form.addRow(tr("Appearance:"), mode_combo)
        lay.addLayout(form)

        note = QLabel(tr("Language changes apply after a restart."), sdlg)
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        lay.addWidget(note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel, sdlg)
        bb.accepted.connect(sdlg.accept)
        bb.rejected.connect(sdlg.reject)
        lay.addWidget(bb)

        if sdlg.exec() != QDialog.DialogCode.Accepted:
            return
        settings.set("language", lang_combo.currentData())
        new_mode = mode_combo.currentData()
        if new_mode != settings.get("appearance", "auto"):
            settings.set("appearance", new_mode)
            _apply_dialog_theme(new_mode)

    gear = QToolButton(dlg)

    def _sliders_icon(size: int = 28) -> QIcon:
        """ChromIQ's settings glyph (three sliders, brand-coloured knobs),
        drawn programmatically like the masthead's light-mode fallback — the
        shipped settings_v2.png has white tracks tuned for the dark masthead
        and all but disappears on a light background. Track colour is
        theme-aware so the icon reads clearly in both modes."""
        from PyQt6.QtGui import QGuiApplication, QPainter, QPen, QColor
        dark = resolve_mode(settings.get("appearance", "auto")) == "dark"
        track_color = "#b0b0b0" if dark else "#6a6a6a"
        dpr = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(size * dpr)
        px = QPixmap(phys, phys)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_cols = ["#ff4573", "#37bcd6", "#ffb42d"]
        knob_x = [0.65, 0.30, 0.50]
        for i, (col, kx) in enumerate(zip(track_cols, knob_x)):
            y = int(phys * (0.28 + i * 0.22))
            p.setPen(QPen(QColor(track_color), max(1, int(phys * 0.07)),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(phys * 0.12), y, int(phys * 0.88), y)
            hx = int(phys * kx)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            r = max(2, int(phys * 0.13))
            p.drawEllipse(hx - r, y - r, r * 2, r * 2)
        p.end()
        px.setDevicePixelRatio(dpr)
        return QIcon(px)

    def _refresh_gear_icon() -> None:
        gear.setIcon(_sliders_icon())
        gear.setIconSize(QSize(28, 28))

    _refresh_gear_icon()
    gear.setFixedSize(36, 36)
    gear.setAutoRaise(True)
    gear.setToolTip(tr("Preferences — language and appearance"))
    gear.setCursor(Qt.CursorShape.PointingHandCursor)
    gear.clicked.connect(_open_settings)

    bottom = QHBoxLayout()
    bottom.setContentsMargins(8, 0, 8, 2)
    bottom.addWidget(version_lbl)
    bottom.addWidget(credit, 1)
    bottom.addWidget(gear)

    _style_credit()
    dlg.layout().addLayout(bottom)

    def _on_system_color_scheme_changed(_scheme=None) -> None:
        if settings.get("appearance", "auto") == "auto":
            _apply_dialog_theme("auto")

    app.styleHints().colorSchemeChanged.connect(_on_system_color_scheme_changed)

    dlg.show()

    # Update-available popup, same flow as ChromIQ's main window. The vendored
    # updater/dialog are pointed at this repo's releases by overriding the
    # module constants — core.updater reads them at call time, and the dialog
    # module (which binds _RELEASES_PAGE at import) is only imported below,
    # after the override.
    import core.updater as _updater
    _updater._RELEASES_API = ("https://api.github.com/repos/itsab1989/"
                              "ChromIQ-Patches/releases?per_page=30")
    _updater._RELEASES_PAGE = "https://github.com/itsab1989/ChromIQ-Patches/releases"

    _update_checker: list = []   # keep a ref so the QObject isn't collected

    def _on_update_available(latest: str) -> None:
        from ui.dialogs.update_dialog import UpdateAvailableDialog
        udlg = UpdateAvailableDialog(latest, dlg)
        # The vendored dialog says "ChromIQ {latest} is available" — brand the
        # standalone without diverging the vendored file.
        for lbl in udlg.findChildren(QLabel):
            if "is available" in lbl.text():
                lbl.setText(lbl.text().replace("ChromIQ ", "ChromIQ Patches ", 1))
        udlg.exec()
        if udlg.disable_notifications:
            settings.set("update_notify", False)

    def _check_for_updates() -> None:
        if not settings.get("update_notify", True):
            return
        checker = _updater.UpdateChecker(dlg)
        checker.update_available.connect(_on_update_available)
        _update_checker.append(checker)
        checker.check_async()

    # Pay QtWebEngine's costly first-init at idle on the main loop, so the
    # on-demand 3D-cube preview never spins Chromium up mid-transition.
    from PyQt6.QtCore import QTimer
    from core.webengine_warmup import warm_up_webengine
    QTimer.singleShot(0, warm_up_webengine)

    QTimer.singleShot(3000, _check_for_updates)

    log.info("Event loop starting")
    return app.exec()


def _hard_exit(code: int) -> None:
    """Flush our own buffers and hand straight to the OS, skipping CPython
    finalization — once any QWebEngineView has existed, letting the interpreter
    finalize walks SIP's wrapper graph into freed Chromium state and crashes
    (ChromIQ issue #38). All real cleanup already ran while the event loop was
    alive; there are no atexit hooks of our own to lose."""
    try:
        logging.shutdown()
    except Exception:
        pass
    try:
        if _crash_log is not None:
            _crash_log.flush()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


if __name__ == "__main__":
    _hard_exit(main())
