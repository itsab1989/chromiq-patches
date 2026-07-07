"""Shared widget factory helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QRect, QRectF, QSize, QSortFilterProxyModel, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QTextCursor

from core.i18n import tr
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionFrame,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ButtonFontFilter(QObject):
    """Applies Menlo + AllUppercase to every QPushButton as it is polished."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QPushButton) and event.type() == QEvent.Type.Polish:
            font = obj.font()
            font.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
            font.setCapitalization(QFont.Capitalization.AllUppercase)
            obj.setFont(font)
        return False


class _ExtensionFilterProxy(QSortFilterProxyModel):
    """Hides files whose extension is not in the allowed set; directories always shown."""

    def __init__(self, extensions: list[str], parent=None) -> None:
        super().__init__(parent)
        self._exts = {e.lower() for e in extensions}

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._exts:
            return True
        src = self.sourceModel()
        idx = src.index(source_row, 0, source_parent)
        try:
            if src.isDir(idx):
                return True
            name = src.fileName(idx)
        except Exception:
            return True
        dot = name.rfind(".")
        if dot < 0:
            return False
        return ("." + name[dot + 1:].lower()) in self._exts


def _parse_extensions(name_filter: str) -> list[str]:
    """Return ['.ti3', '.icc'] from 'ICC profiles (*.icc *.icm)'."""
    return ["." + e.lower() for e in re.findall(r"\*\.(\w+)", name_filter)]


def _input_bg_qss() -> str:
    """Per-widget QSS rule forcing the body of QComboBox / QSpinBox /
    QDoubleSpinBox to the current theme's input background colour
    (white in light, BG_INPUT #1f1f1f in dark). App-wide QSS for these
    rules is silently ignored by Qt's QStyleSheetStyle for compound
    widgets, but per-widget setStyleSheet bypasses that quirk."""
    bg = QApplication.palette().base().color().name()
    return (
        "QComboBox:enabled, QSpinBox:enabled, QDoubleSpinBox:enabled {"
        f" background-color: {bg};"
        "}"
    )


def confirm(
    parent,
    title: str,
    text: str,
    buttons: QMessageBox.StandardButton,
    default: "QMessageBox.StandardButton | None" = None,
) -> QMessageBox.StandardButton:
    """Yes/No-style confirmation prompt without the question-mark icon.

    A drop-in for ``QMessageBox.question`` (which bakes in the “?” icon the
    user dislikes): same signature shape, returns the StandardButton clicked.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setStandardButtons(buttons)
    if default is not None:
        box.setDefaultButton(default)
    box.exec()
    return box.standardButton(box.clickedButton())


class NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoScrollDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(_input_bg_qss())

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SuffixLockedLineEdit(QLineEdit):
    """A line edit with a locked, non-editable suffix tail.

    The user edits only the leading *base*; the *suffix* is set by the owner via
    :meth:`set_suffix` and can't be typed into, deleted, selected away or pasted
    over — it can only be changed or cleared programmatically (e.g. by an
    auto-name toggle that recomputes it from the chart settings). With an empty
    suffix it behaves exactly like a plain ``QLineEdit``.

    Enforcement is behavioural (a normal field can't visually lock part of its
    text): the suffix region is kept out of selections / the cursor, boundary
    deletes are swallowed, and a ``textChanged`` net repairs a wholesale replace.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._suffix = ""
        self._guard = False
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_cursor)

    # -- public ---------------------------------------------------------
    def set_suffix(self, suffix: str) -> None:
        """Replace the locked tail, preserving the user's base text."""
        suffix = suffix or ""
        if suffix == self._suffix:
            return
        base = self.base()
        self._suffix = suffix
        self._set(base, len(base))

    def base(self) -> str:
        """The editable leading part (text without the locked suffix)."""
        t = super().text()
        if self._suffix and t.endswith(self._suffix):
            return t[: len(t) - len(self._suffix)]
        return t

    def set_base(self, base: str) -> None:
        self._set(base or "", len(base or ""))

    # -- internals ------------------------------------------------------
    def _set(self, base: str, cursor: int) -> None:
        self._guard = True
        super().setText(base + self._suffix)
        super().setCursorPosition(min(cursor, len(base)))
        self._guard = False

    def _base_end(self) -> int:
        t = super().text()
        if self._suffix and t.endswith(self._suffix):
            return len(t) - len(self._suffix)
        return len(t)

    def _clamp_selection(self) -> None:
        if not self._suffix:
            return
        end = self._base_end()
        self._guard = True
        if self.selectionStart() >= 0 and self.selectionLength() > 0:
            s = min(self.selectionStart(), end)
            e = min(self.selectionStart() + self.selectionLength(), end)
            self.setSelection(s, max(0, e - s))
        elif self.cursorPosition() > end:
            super().setCursorPosition(end)
        self._guard = False

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if self._suffix:
            end = self._base_end()
            # A forward-delete sitting at the base/suffix boundary would eat into
            # the locked tail — swallow it.
            if (ev.key() == Qt.Key.Key_Delete and self.selectionLength() == 0
                    and self.cursorPosition() >= end):
                return
            self._clamp_selection()
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        self._clamp_selection()      # paste lands in the base, never the suffix
        super().insertFromMimeData(source)

    def _on_cursor(self, _old: int, new: int) -> None:
        if self._guard or not self._suffix or self.selectionLength() > 0:
            return
        end = self._base_end()
        if new > end:
            self._guard = True
            super().setCursorPosition(end)
            self._guard = False

    def _on_text_changed(self, _t: str) -> None:
        # Net for a wholesale replace (e.g. select-all then paste/typing that the
        # clamps didn't catch): if the suffix is gone, treat all text as base and
        # re-append it.
        if self._guard or not self._suffix:
            return
        if not super().text().endswith(self._suffix):
            self.set_base(super().text())


class PrefixLockedLineEdit(QLineEdit):
    """A line edit with a locked, non-editable *prefix* (the mirror of
    :class:`SuffixLockedLineEdit`).

    The user edits only the trailing part; the *prefix* is set by the owner via
    :meth:`set_prefix` and can't be typed into, deleted or pasted over. Used for
    a leading descriptive name part (sortable), with the user's free text as the
    editable tail. Focusing the field drops the cursor at the start of that tail
    and scrolls it into view, so a long prefix never hides where you type.
    """

    _SEP = "-"   # joins the locked prefix to the editable tail

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._prefix = ""
        self._guard = False
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_cursor)

    # -- public ---------------------------------------------------------
    def set_prefix(self, prefix: str) -> None:
        """Set the locked descriptive head (#68, Knut's model). The head is shown
        greyed and locked with a trailing ``-`` even when the tail is empty
        (``name-`` → ``name-mytext``); the user only edits the tail after it.
        Pass ``""`` to remove the lock entirely (a plain, fully editable field).
        A trailing separator on *prefix* is dropped (it's supplied
        automatically)."""
        prefix = prefix or ""
        if prefix.endswith(self._SEP):
            prefix = prefix[: -len(self._SEP)]
        if prefix == self._prefix:
            return
        tail = self.tail()
        self._prefix = prefix
        self._set(tail)

    def tail(self) -> str:
        """The editable trailing part (text after the locked prefix + separator)."""
        t = super().text()
        if not self._prefix:
            return t
        head = self._prefix + self._SEP
        if t.startswith(head):
            return t[len(head):]
        if t.startswith(self._prefix):   # transient: separator momentarily gone
            return t[len(self._prefix):]
        return t

    def set_tail(self, tail: str) -> None:
        self._set(tail or "")

    # -- internals ------------------------------------------------------
    def _full(self, tail: str) -> str:
        """Canonical text: ``prefix + '-' + tail`` whenever a prefix is set (the
        separator stays even when the tail is empty), else just the tail."""
        if not self._prefix:
            return tail
        return self._prefix + self._SEP + tail

    def _set(self, tail: str) -> None:
        self._guard = True
        super().setText(self._full(tail))
        super().setCursorPosition(len(super().text()))   # land in the tail
        self._guard = False

    def _locked_len(self) -> int:
        """Length of the non-editable head in the CURRENT text (prefix + the
        always-present separator)."""
        t = super().text()
        if not self._prefix:
            return 0
        head = self._prefix + self._SEP
        if t.startswith(head):
            return len(head)
        if t.startswith(self._prefix):   # transient (separator being re-added)
            return len(self._prefix)
        return 0

    def _clamp(self) -> None:
        if not self._prefix:
            return
        start = self._locked_len()
        self._guard = True
        if self.selectionStart() >= 0 and self.selectionLength() > 0:
            s = max(self.selectionStart(), start)
            e = max(self.selectionStart() + self.selectionLength(), start)
            self.setSelection(s, max(0, e - s))
        elif self.cursorPosition() < start:
            super().setCursorPosition(start)
        self._guard = False

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if self._prefix:
            start = self._locked_len()
            if self.selectionLength() == 0:
                # Backspace at the boundary would eat the locked head/separator;
                # forward-Delete from inside the locked head would eat a prefix
                # character. Swallow both.
                if (ev.key() == Qt.Key.Key_Backspace
                        and self.cursorPosition() <= start):
                    return
                if (ev.key() == Qt.Key.Key_Delete
                        and self.cursorPosition() < start):
                    return
            self._clamp()
        super().keyPressEvent(ev)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        self._clamp()
        super().insertFromMimeData(source)

    def focusInEvent(self, ev) -> None:  # noqa: N802
        super().focusInEvent(ev)
        if self._prefix:
            # Land in the editable tail (and let Qt scroll it into view) rather
            # than selecting the whole — locked — string.
            self.deselect()
            self.setCursorPosition(len(super().text()))

    def _on_cursor(self, _old: int, new: int) -> None:
        if self._guard or not self._prefix or self.selectionLength() > 0:
            return
        start = self._locked_len()
        if new < start:
            self._guard = True
            super().setCursorPosition(start)
            self._guard = False

    def _on_text_changed(self, _t: str) -> None:
        if self._guard or not self._prefix:
            return
        # Re-render canonically so the locked separator is always present while a
        # prefix is set (it can't be deleted to merge the head into the tail).
        t = super().text()
        canonical = self._full(self.tail())
        if t != canonical:
            self._guard = True
            super().setText(canonical)
            super().setCursorPosition(len(canonical))   # edits happen at the tail end
            self._guard = False

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)
        # Grey the locked head so it visibly reads as non-editable (#68, Knut).
        # Only when the text fits without horizontal scroll: once it scrolls, the
        # glyphs no longer start at the content's left edge and an overlay would
        # mis-paint — the always-present '-' still marks the boundary there.
        if self._prefix == "" or self.hasSelectedText():
            return
        locked = self._locked_len()
        if locked <= 0:
            return
        text = super().text()
        fm = self.fontMetrics()
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        content = self.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents, opt, self)
        if fm.horizontalAdvance(text) > content.width() - 4:
            return
        x0 = content.left() + 2
        w = fm.horizontalAdvance(text[:locked])
        rect = QRect(x0, content.top(), w, content.height())
        p = QPainter(self)
        p.fillRect(rect, self.palette().color(QPalette.ColorRole.Base))
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter
                             | Qt.AlignmentFlag.AlignLeft), text[:locked])
        p.end()


class ElidingLabel(QLabel):
    """Single-line label that middle-elides overflowing text with ``(...)``.

    A long file path used to expand the label to its full natural width and
    squeeze the adjacent "Load" button. This label reports a zero minimum
    width (size policy ``Ignored``) so it never pushes its neighbours, and
    middle-elides whatever no longer fits the available width — keeping the
    start of the path and the filename at the end both visible. The full,
    un-elided text is preserved and exposed as a hover tooltip and via
    ``text()``.
    """

    _SEP = "(...)"

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = text or ""
        self._apply_elision()

    def text(self) -> str:  # type: ignore[override]
        """Return the full, un-elided text (not what is currently painted)."""
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        fm = self.fontMetrics()
        avail = self.width()
        full = self._full_text
        if avail <= 0 or fm.horizontalAdvance(full) <= avail:
            super().setText(full)
            self.setToolTip("")
            return
        budget = avail - fm.horizontalAdvance(self._SEP)
        if budget <= 0:
            super().setText(self._SEP)
            self.setToolTip(full)
            return
        # Grow head and tail one character at a time, alternating, until the
        # next character would overflow the budget either side of the separator.
        head, tail = "", ""
        i, j = 0, len(full) - 1
        take_head = True
        while i <= j:
            ch = full[i] if take_head else full[j]
            if fm.horizontalAdvance(head + ch + tail) > budget:
                break
            if take_head:
                head += ch
                i += 1
            else:
                tail = ch + tail
                j -= 1
            take_head = not take_head
        super().setText(f"{head}{self._SEP}{tail}")
        self.setToolTip(full)


def reapply_input_stylesheet(root: QWidget) -> None:
    """Re-apply the per-widget input-bg QSS on every combo/spin descendant.
    Called from MainWindow.apply_theme on every theme switch so the
    hardcoded colour in the existing per-widget stylesheet is refreshed
    for the new theme."""
    qss = _input_bg_qss()
    for cls in (QComboBox, QSpinBox, QDoubleSpinBox):
        for w in root.findChildren(cls):
            w.setStyleSheet(qss)


class CollapsibleGroupBox(QGroupBox):
    """A QGroupBox whose title is clickable to collapse / expand its contents.

    Keeps the native framed look (border + embedded title) so it matches the
    other sections; the title gains a ▸ / ▾ arrow and, when collapsed, the body
    is hidden and the box shrinks to the title.

    Put content on the ``.body`` widget — ``QGridLayout(group.body)`` etc. — not
    on the group itself, so collapsing hides one container and each child keeps
    its own intended visibility (mode logic may hide individual fields) (Knut:
    collapsible Create-Chart sections)."""

    def __init__(self, title: str = "", parent=None, *, collapsed: bool = False):
        super().__init__("", parent)
        self._base_title = title
        self._collapsed = bool(collapsed)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self.body = QWidget(self)
        self._outer.addWidget(self.body)
        # Bold the section title (incl. the arrow) so the header clearly reads as
        # a clickable open/close control (Knut); keep the body at normal weight.
        _tf = self.font()
        _tf.setBold(True)
        self.setFont(_tf)
        _bf = QFont(_tf)
        _bf.setBold(False)
        self.body.setFont(_bf)
        self._render_title()
        self.body.setVisible(not self._collapsed)

    def setTitle(self, title: str) -> None:        # noqa: N802 (Qt override)
        self._base_title = title
        self._render_title()

    def _render_title(self) -> None:
        # Bigger, filled triangles (▶ / ▼) read far more clearly as an open/close
        # affordance than the small ▸ / ▾ (Knut). A trailing space sets them off.
        super().setTitle(("▶  " if self._collapsed else "▼  ") + self._base_title)

    def title(self) -> str:                        # noqa: N802 (Qt override)
        return self._base_title

    def _title_band(self) -> int:
        return self.fontMetrics().height() + 10

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self._render_title()
        self.body.setVisible(not self._collapsed)
        # Drop the box frame while collapsed so only the ▸ title line shows
        # (no empty bordered box); restore the frame when expanded (Knut).
        self.setFlat(self._collapsed)
        self.updateGeometry()

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def mousePressEvent(self, event) -> None:      # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton \
                and event.position().y() <= self._title_band():
            self.toggle()
            event.accept()
            return
        super().mousePressEvent(event)


def _apply_groupbox_surface(gb: QGroupBox) -> None:
    """Paint the GroupBox surface via QPalette + autoFillBackground instead
    of QSS. The QSS rule `QGroupBox { background: ... }` causes Qt's
    QStyleSheetStyle to propagate the colour into descendants' palette
    roles (including QPalette.Base), which makes QComboBox / QSpinBox
    bodies render the same surface colour as the section. Setting only
    palette.Window via setPalette() does not contaminate descendants'
    Base role, so inputs stay white per their own QSS rule."""
    app_pal = QApplication.palette()
    is_light = app_pal.window().color().lightness() > 150
    if is_light:
        from ui.light_styles import LM_BG_SURFACE
        gb.setAutoFillBackground(True)
        pal = gb.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(LM_BG_SURFACE))
        gb.setPalette(pal)
    else:
        gb.setAutoFillBackground(False)
        gb.setPalette(QPalette())  # revert to inherited


class GroupBoxSurfaceFilter(QObject):
    """Installs on QApplication. Whenever a QGroupBox is polished, applies
    the cream surface colour via setPalette + autoFillBackground so the
    QSS rule for QGroupBox can stay background-less and not contaminate
    descendant input widgets' palette.Base."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Polish and isinstance(obj, QGroupBox):
            _apply_groupbox_surface(obj)
        return False


class TooltipWrapFilter(QObject):
    """Installs on QApplication. Forces every native tooltip (Qt's private
    ``QTipLabel``) to word-wrap at a sane maximum width, so a long tooltip — in
    any language — never runs off the right edge of the screen (Knut, #70).

    Qt does not word-wrap plain-text tooltips on every platform (notably
    Windows, where Knut saw them reach far past the screen edge), so we enable
    wrapping and cap the width on the transient label as it is polished, before
    it is shown and sized. The label then re-flows to multiple lines on its own.
    """

    MAX_W = 460   # px — a comfortable reading measure; text re-flows to fit

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (event.type() in (QEvent.Type.Polish, QEvent.Type.Show)
                and obj.metaObject().className() == "QTipLabel"
                and isinstance(obj, QLabel)):
            fm = obj.fontMetrics()
            # Widest existing line (tooltips may already carry manual newlines).
            longest = max(
                (fm.horizontalAdvance(s) for s in obj.text().split("\n")),
                default=0,
            )
            m = obj.contentsMargins()
            pad = m.left() + m.right() + 2 * obj.margin() + 8
            if longest + pad > self.MAX_W:
                obj.setWordWrap(True)
                # heightForWidth gives the true wrapped height; pin both so
                # QToolTip's own resize(sizeHint()) can't clip it back to one
                # line (its sizeHint ignores the wrap on a transient label).
                h = obj.heightForWidth(self.MAX_W)
                if h > 0:
                    obj.setFixedSize(self.MAX_W, h)
                    # QToolTip already positioned the label using its huge
                    # pre-wrap width, so a very wide tooltip got shoved to the
                    # screen's left edge. Re-anchor the now-narrow box near the
                    # cursor, clamped on-screen, so it appears where the mouse is.
                    self._reanchor(obj, self.MAX_W, h)
        return False

    @staticmethod
    def _reanchor(obj: QLabel, w: int, h: int) -> None:
        from PyQt6.QtGui import QCursor, QGuiApplication
        cpos = QCursor.pos()
        screen = QGuiApplication.screenAt(cpos) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = cpos.x() + 14
        y = cpos.y() + 20
        if x + w > geo.right():
            x = cpos.x() - w - 4
        if y + h > geo.bottom():
            y = cpos.y() - h - 6
        x = min(max(x, geo.left()), geo.right() - w)
        y = min(max(y, geo.top()), geo.bottom() - h)
        obj.move(x, y)


def reapply_groupbox_surface(root: QWidget) -> None:
    """Walk every QGroupBox descendant of `root` and re-apply the surface
    colour. Called from MainWindow.apply_theme on every theme switch
    because Polish only fires once per widget."""
    for gb in root.findChildren(QGroupBox):
        _apply_groupbox_surface(gb)


def icc_profile_paths() -> list[str]:
    """Common OS-level ICC/ICM profile directories for file-dialog sidebars."""
    import os
    import sys
    home = Path.home()
    if sys.platform == "darwin":
        return [
            "/Library/ColorSync/Profiles",
            "/System/Library/ColorSync/Profiles",
            str(home / "Library/ColorSync/Profiles"),
        ]
    if sys.platform.startswith("win"):
        # Honour %SystemRoot% — Windows is not always installed on C:.
        win = os.environ.get("SystemRoot", r"C:\Windows")
        paths = [str(Path(win) / "System32" / "spool" / "drivers" / "color")]
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            paths.append(str(Path(local) / "Microsoft" / "Windows" / "Color"))
        return paths
    return [
        "/usr/share/color/icc",
        "/usr/local/share/color/icc",
        str(home / ".local/share/icc"),   # modern XDG per-user dir (colord/GNOME)
        str(home / ".color/icc"),         # older Argyll/oyranos convention
    ]


def _sidebar_urls(extra_path: str = "", extra_paths: tuple | list = ()) -> list[QUrl]:
    # OS-correct, localized standard folders (Windows known-folders, localized
    # names on macOS/Linux) — Desktop, Images, Downloads, Documents — rather than
    # hard-coded English paths under home.
    from PyQt6.QtCore import QStandardPaths
    SL = QStandardPaths.StandardLocation
    candidates: list[Path] = []
    for loc in (SL.DesktopLocation, SL.PicturesLocation,
                SL.DownloadLocation, SL.DocumentsLocation):
        p = QStandardPaths.writableLocation(loc)
        if p:
            candidates.append(Path(p))
    candidates.append(Path.home() / "ChromIQ")    # the app's working folder
    if extra_path:
        candidates.append(Path(extra_path))
    for p in extra_paths:
        if p:
            candidates.append(Path(p))
    # De-dupe while keeping order, then drop any that don't exist.
    seen, urls = set(), []
    for p in candidates:
        s = str(p)
        if s not in seen and p.exists():
            seen.add(s)
            urls.append(QUrl.fromLocalFile(s))
    return urls


_NAV_BUTTONS = {
    "backButton":     QStyle.StandardPixmap.SP_ArrowBack,
    "forwardButton":  QStyle.StandardPixmap.SP_ArrowForward,
    "toParentButton": QStyle.StandardPixmap.SP_FileDialogToParent,
}

# Arrow drawn at _NAV_ARROW_SIZE, centred inside a _NAV_BTN_SIZE canvas.
# Qt places the canvas icon at top-left of the button, so centering is
# baked into the transparent padding of the canvas image.
_NAV_BTN_SIZE   = QSize(28, 28)
_NAV_ARROW_SIZE = QSize(16, 16)


def _nav_icon(icon: QIcon, color: QColor) -> QIcon:
    """Recolor icon and centre it on a transparent canvas matching button size."""
    raw = icon.pixmap(_NAV_ARROW_SIZE)
    # recolor
    colored = QPixmap(raw.size())
    colored.fill(Qt.GlobalColor.transparent)
    p = QPainter(colored)
    p.drawPixmap(0, 0, raw)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(colored.rect(), color)
    p.end()
    # centre on canvas
    canvas = QPixmap(_NAV_BTN_SIZE)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    x = (_NAV_BTN_SIZE.width()  - _NAV_ARROW_SIZE.width())  // 2
    y = (_NAV_BTN_SIZE.height() - _NAV_ARROW_SIZE.height()) // 2
    p.drawPixmap(x, y, colored)
    p.end()
    return QIcon(canvas)


def _style_file_dialog_toolbar(dlg: QFileDialog) -> None:
    from core.settings import AppSettings
    from ui.theme import APPEARANCE_LIGHT, resolve_mode

    # Light mode's pale toolbar washes out the light arrows that read fine on
    # Dark mode's dark toolbar — use a near-black arrow there instead.
    mode = resolve_mode(AppSettings().get("appearance", "auto"))
    arrow_color = QColor("#1C1B18" if mode == APPEARANCE_LIGHT else "#e0e0e0")
    style = dlg.style()
    for name, sp in _NAV_BUTTONS.items():
        btn = dlg.findChild(QToolButton, name)
        if btn:
            btn.setIcon(_nav_icon(style.standardIcon(sp), arrow_color))
            btn.setIconSize(_NAV_BTN_SIZE)
            btn.setFixedSize(_NAV_BTN_SIZE)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    grip = dlg.findChild(QSizeGrip)
    if grip:
        grip.hide()


def _prefer_native_dialogs() -> bool:
    """User preference: use the OS-native file dialogs instead of ChromIQ's
    themed one (Settings → Behaviour). Native is much faster to populate on
    Windows, but — being the OS's own dialog — it can't carry our custom sidebar
    shortcuts or the injected image-preview pane, so those are skipped when on."""
    try:
        from core.settings import AppSettings
        return bool(AppSettings().get("use_native_file_dialogs", False))
    except Exception:
        return False


def open_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_dir: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
    preview: bool = False,
) -> str:
    """Open a Qt file dialog with sidebar shortcuts and proper file-type filtering.

    Non-matching files are hidden when name_filter is set. When ``preview`` is
    True, an image thumbnail of the highlighted file is shown beside the list
    (for picking images). With the native-dialogs setting on, the OS dialog is
    used instead (its own Quick Access + preview pane; our custom sidebar and
    preview don't apply).

    Returns the selected file path, or an empty string if cancelled.
    """
    native = _prefer_native_dialogs()
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    if not native:
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog)
        _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    if name_filter:
        dlg.setNameFilter(name_filter)
        if not native:
            exts = _parse_extensions(name_filter)
            if exts:
                dlg.setProxyModel(_ExtensionFilterProxy(exts, dlg))
    if not native:
        dlg.setSidebarUrls(_sidebar_urls(extra_path, extra_paths))
        if preview:
            _attach_image_preview(dlg)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


def _attach_image_preview(dlg: "QFileDialog") -> None:
    """Add a live image-thumbnail pane to a non-native QFileDialog.

    QFileDialog's body is a QGridLayout; we drop a preview label into the column
    to the right of the file list and refresh it on ``currentChanged``. Loading
    is done lazily off the highlighted path (a QPixmap of the whole file) and
    scaled down — fine for the modest sizes a user browses one at a time.
    """
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QGridLayout, QLabel
    layout = dlg.layout()
    if not isinstance(layout, QGridLayout):
        return
    holder = QLabel(dlg)
    holder.setObjectName("imagePreview")
    # Fixed width so the preview doesn't eat the extra width — that goes to the
    # file list, which is what should grow when the dialog is widened.
    holder.setFixedWidth(300)
    holder.setMinimumHeight(260)
    holder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
    holder.setText(tr("No preview"))
    holder.setStyleSheet(
        "QLabel#imagePreview { border: 1px solid palette(mid); color: palette(mid);"
        " background: palette(base); }")
    # Span the file-list rows on the far right.
    layout.addWidget(holder, 1, layout.columnCount(), layout.rowCount() - 1, 1)
    # Only widen — the file list is roomy alongside the fixed-width preview;
    # keep the standard file-dialog height (don't force it taller).
    dlg.setMinimumWidth(1000)
    dlg.resize(1280, dlg.height())

    def _show(path: str) -> None:
        if path and Path(path).is_file():
            pm = QPixmap(path)
            if not pm.isNull():
                holder.setPixmap(pm.scaled(
                    holder.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                return
        holder.setPixmap(QPixmap())
        holder.setText(tr("No preview"))

    dlg.currentChanged.connect(_show)


def open_files_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_dir: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
    preview: bool = False,
) -> list[str]:
    """Multi-file variant of :func:`open_file_dialog`.

    Shares the same OS-correct sidebar shortcuts; when ``preview`` is True an
    image thumbnail of the highlighted file is shown beside the list (for
    picking images). Returns the list of selected paths, or an empty list if
    cancelled.
    """
    native = _prefer_native_dialogs()
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    if not native:
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog)
        _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
    if name_filter:
        dlg.setNameFilter(name_filter)
        if not native:
            exts = _parse_extensions(name_filter)
            if exts:
                dlg.setProxyModel(_ExtensionFilterProxy(exts, dlg))
    if not native:
        dlg.setSidebarUrls(_sidebar_urls(extra_path, extra_paths))
        if preview:
            _attach_image_preview(dlg)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        return list(dlg.selectedFiles())
    return []


def save_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str = "",
    start_path: str = "",
    extra_path: str = "",
    extra_paths: tuple | list = (),
) -> str:
    """Open a Qt **save** file dialog with sidebar shortcuts.

    ``start_path`` may be a directory or a full path with a default
    filename — if it points at an existing directory the dialog opens
    there, otherwise it pre-selects the file inside its parent dir.
    Returns the chosen path, or an empty string if cancelled.
    """
    p = Path(start_path) if start_path else None
    if p is not None and p.is_dir():
        start_dir, default_name = str(p), ""
    elif p is not None:
        start_dir, default_name = str(p.parent), p.name
    else:
        start_dir, default_name = str(Path.home()), ""
    native = _prefer_native_dialogs()
    dlg = QFileDialog(parent, title, start_dir)
    if not native:
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog)
        _style_file_dialog_toolbar(dlg)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    if name_filter:
        dlg.setNameFilter(name_filter)
    if default_name:
        dlg.selectFile(default_name)
    if not native:
        dlg.setSidebarUrls(_sidebar_urls(extra_path, extra_paths))
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        files = dlg.selectedFiles()
        return files[0] if files else ""
    return ""


def open_dir_dialog(
    parent: QWidget,
    title: str,
    start_dir: str = "",
    extra_path: str = "",
) -> str:
    """Open a Qt directory dialog with sidebar shortcuts.

    Returns the selected directory path, or an empty string if cancelled.
    """
    native = _prefer_native_dialogs()
    dlg = QFileDialog(parent, title, start_dir or str(Path.home()))
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    if not native:
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        _style_file_dialog_toolbar(dlg)
        import sys as _sys
        urls = _sidebar_urls(extra_path)
        if _sys.platform == "darwin":
            urls.append(QUrl.fromLocalFile("/Applications"))
        dlg.setSidebarUrls(urls)
    if dlg.exec() == QFileDialog.DialogCode.Accepted:
        dirs = dlg.selectedFiles()
        return dirs[0] if dirs else ""
    return ""


def load_folder_icon(name: str) -> QIcon:
    """Load a colored folder icon from assets/folder/<name>.png.

    For the plain "folder" icon (used in the Preferences dialog), if the
    active palette is light, take the same PNG and re-tint every
    non-transparent pixel to #22211f so the shape stays identical to the
    coloured variants — just in a dark hue that reads on the warm-white
    Preferences background. The tab-specific coloured variants
    (folder_build, folder_print, …) are kept as-is since their hues
    already read on either background.

    Falls back to the OS system folder icon if no asset is found.
    """
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication

    dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
    phys = round(20 * dpr)

    src = resource_path(f"assets/folder/{name}.png")
    src_px = QPixmap(str(src))
    if not src_px.isNull():
        scaled = src_px.scaled(phys, phys,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        # Light-theme: recolour the bare "folder" icon to #22211f. Compose
        # the new colour using SourceIn so the icon's existing alpha mask
        # (the line work) is preserved exactly — every line that was
        # rendered in the dark PNG is repainted in the new colour.
        if name == "folder" and _is_light_palette():
            from PyQt6.QtGui import QImage, QPainter
            img = scaled.toImage().convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied
            )
            painter = QPainter(img)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceIn
            )
            painter.fillRect(img.rect(), QColor("#22211f"))
            painter.end()
            recoloured = QPixmap.fromImage(img)
            recoloured.setDevicePixelRatio(dpr)
            return QIcon(recoloured)
        scaled.setDevicePixelRatio(dpr)
        return QIcon(scaled)
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)


def _is_light_palette() -> bool:
    """True when the active app palette is a light theme."""
    from PyQt6.QtGui import QGuiApplication
    pal = QGuiApplication.palette()
    return pal.window().color().lightness() > 150


def load_preset_icon(name: str) -> QIcon:
    """Load a preset +/- icon, switching to the *_dark variant in light mode.

    `name` is the bare asset stem ("plus" or "minus"). On a light palette,
    we load the *_dark.svg sibling so the glyph reads on the warm-white
    Presets row.
    """
    from core.resource_path import resource_path
    stem = f"{name}_dark" if _is_light_palette() else name
    return QIcon(str(resource_path(f"assets/{stem}.svg")))


def load_tinted_folder_icon(color: str, size: int = 22) -> QIcon:
    """The standard folder glyph tinted in an arbitrary accent ``color``.

    Repaints every opaque pixel of ``folder.png`` via SourceIn, preserving the
    icon's alpha mask (same trick :func:`load_folder_icon` uses for the
    light-theme recolour). Spectrum accents read on both themes, so no
    light/dark variant is needed. Used where a browse button should match its
    dialog's masthead accent rather than a tab-coded variant."""
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter

    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    phys = round(size * dpr)
    src_px = QPixmap(str(resource_path("assets/folder/folder.png")))
    if src_px.isNull():
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
    scaled = src_px.scaled(phys, phys,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    img = scaled.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(img)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(img.rect(), QColor(color))
    painter.end()
    out = QPixmap.fromImage(img)
    out.setDevicePixelRatio(dpr)
    return QIcon(out)


def load_magenta_folder_icon() -> QIcon:
    """The standard folder glyph tinted in the app's spectrum magenta — used by
    the "open an existing profile" button beside the built-in-presets star, so
    the two read as a matched pair (#70)."""
    from ui.styles import SPEC_MAGENTA
    return load_tinted_folder_icon(SPEC_MAGENTA, size=22)


class PatchGridButton(QToolButton):
    """A small grid-of-patches glyph button, painted in a given accent colour.

    Mirrors ``BuiltinPresetButton``'s painted-glyph approach (crisp on Retina,
    no PNG asset) and its 40×40 / ``#tooltip_btn`` styling, so it sits as a
    matched sibling beside the folder and star buttons. The 3×3 grid reads as a
    chart patch set / layout — used for the "load a chart layout" buttons on the
    Create Chart (magenta) and Print Chart (amber) tabs (#70, Knut)."""

    GRID_FRAC = 0.60   # glyph box as a fraction of the button
    GRID_N    = 3      # squares per side

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setObjectName("tooltip_btn")
        self.setFixedSize(QSize(40, 40))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_appearance(self, mode: str) -> None:
        pass  # accent colour is theme-independent — nothing to repaint

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)  # QSS background (incl. :hover) under the glyph
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) * self.GRID_FRAC
        gap  = side * 0.16
        cell = (side - (self.GRID_N - 1) * gap) / self.GRID_N
        x0   = (self.width()  - side) / 2.0
        y0   = (self.height() - side) / 2.0
        color = QColor(self._color)
        if not self._hover:
            color.setAlpha(230)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        rad = cell * 0.22
        for r in range(self.GRID_N):
            for c in range(self.GRID_N):
                x = x0 + c * (cell + gap)
                y = y0 + r * (cell + gap)
                p.drawRoundedRect(QRectF(x, y, cell, cell), rad, rad)
        p.end()


def set_folder_icon(btn: QPushButton, name: str) -> None:
    """Set a folder-glyph icon on `btn` and tag it for live theme refresh."""
    btn.setIcon(load_folder_icon(name))
    btn.setProperty("themed_folder_icon", name)


def set_preset_icon(btn: QPushButton, name: str) -> None:
    """Set a preset +/- icon on `btn` and tag it for live theme refresh."""
    btn.setIcon(load_preset_icon(name))
    btn.setProperty("themed_preset_icon", name)


def apply_themed_icons(root: QWidget) -> None:
    """Reload every theme-aware icon under `root`.

    Walks all QPushButtons and re-runs the appropriate loader for buttons
    tagged by set_folder_icon / set_preset_icon / make_browse_button. Call
    from MainWindow.apply_theme so palette-dependent icons repaint without
    requiring an app restart.
    """
    for btn in root.findChildren(QPushButton):
        folder_name = btn.property("themed_folder_icon")
        if folder_name:
            btn.setIcon(load_folder_icon(str(folder_name)))
            continue
        preset_name = btn.property("themed_preset_icon")
        if preset_name:
            btn.setIcon(load_preset_icon(str(preset_name)))


def tint_dialog_primary(dlg: "QWidget", color: str) -> None:
    """Stamp tab accent color onto every QPushButton#primary inside a dialog (v2 only).

    Safe to call on any dialog — no-op if no primary buttons are present.
    """
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    hover = "#{:02x}{:02x}{:02x}".format(int(r * 0.82), int(g * 0.82), int(b * 0.82))
    for btn in dlg.findChildren(QPushButton):
        if btn.objectName() == "primary":
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 1px solid {color};"
                f" color: #0a0a0a; font-weight: 700; }}"
                f"QPushButton:hover {{ background: {hover}; border-color: {hover}; }}"
            )


def load_refresh_icon(name: str) -> QIcon:
    """Load a colored refresh icon from assets/refresh/<name>.png.

    Falls back to the OS browser-reload icon if the file is not found.
    """
    from core.resource_path import resource_path
    from PyQt6.QtGui import QGuiApplication
    px = QPixmap(str(resource_path(f"assets/refresh/{name}.png")))
    if not px.isNull():
        dpr  = QGuiApplication.primaryScreen().devicePixelRatio()
        phys = round(20 * dpr)
        scaled = px.scaled(phys, phys,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return QIcon(scaled)
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)


def make_browse_button(
    parent: QWidget | None = None,
    tooltip: str = "Browse…",
    icon: str = "folder",
) -> QPushButton:
    """Create a standardised file-browse button with a folder icon.

    Pass the icon name (without path or extension) to select a colored variant,
    e.g. ``icon="folder_build"``.
    """
    btn = QPushButton(parent)
    btn.setObjectName("browse")
    btn.setFixedWidth(36)
    btn.setToolTip(tooltip)
    btn.setIcon(load_folder_icon(icon))
    btn.setProperty("themed_folder_icon", icon)
    btn.setIconSize(QSize(20, 20))
    return btn


def replace_log_line(
    log: QPlainTextEdit,
    prev_text: str | None,
    new_text: str | None,
) -> str | None:
    """Replace a single tracked status line in a QPlainTextEdit log, in place.

    Removes ``prev_text``'s block (if still present) along with exactly one
    adjacent block separator — the trailing one when anything follows, otherwise
    the leading one — so no blank line is left wherever the line sits. Then
    appends ``new_text`` when it is non-empty. Returns the text now being tracked
    (``new_text`` or ``None``), to store for the next call.

    Lets a tab show only the most recent of a recurring notice (e.g. the detected
    instrument) instead of stacking identical lines as files are reloaded.
    """
    if prev_text:
        found = log.document().find(prev_text)
        if not found.isNull():
            block = found.block()
            keep = QTextCursor.MoveMode.KeepAnchor
            cursor = QTextCursor(log.document())
            if block.next().isValid():
                cursor.setPosition(block.position())
                cursor.setPosition(block.next().position(), keep)
            elif block.previous().isValid():
                cursor.setPosition(block.position() - 1)
                cursor.setPosition(block.position() + len(block.text()), keep)
            else:
                cursor.setPosition(0)
                cursor.setPosition(len(block.text()), keep)
            cursor.removeSelectedText()
    if new_text:
        log.appendPlainText(new_text)
        log.ensureCursorVisible()
        return new_text
    return None


@dataclass

class ImageFileButton(QToolButton):
    """A small painted image-file glyph (frame + mountains + sun) in a given
    accent colour — sibling of :class:`PatchGridButton`, used for "load a
    TIFF image to print raw" on the Print Chart tab (#117, Knut)."""

    FRAC = 0.60

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self.setObjectName("tooltip_btn")
        self.setFixedSize(QSize(40, 40))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_appearance(self, mode: str) -> None:
        pass

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) * self.FRAC
        x0 = (self.width() - side) / 2.0
        y0 = (self.height() - side) / 2.0
        color = QColor(self._color)
        if not self._hover:
            color.setAlpha(230)
        pen = QPen(color)
        pen.setWidthF(1.6)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x0, y0, side, side), 2.0, 2.0)
        # mountains
        path = QPainterPath()
        path.moveTo(x0 + side * 0.10, y0 + side * 0.82)
        path.lineTo(x0 + side * 0.38, y0 + side * 0.45)
        path.lineTo(x0 + side * 0.55, y0 + side * 0.65)
        path.lineTo(x0 + side * 0.72, y0 + side * 0.38)
        path.lineTo(x0 + side * 0.90, y0 + side * 0.82)
        p.drawPath(path)
        # sun
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        r = side * 0.10
        p.drawEllipse(QRectF(x0 + side * 0.20, y0 + side * 0.16, 2 * r, 2 * r))
        p.end()

class GatedOption:
    """An option disabled when a tab's instrument/data gate is active.

    ``widgets`` are greyed out while the gate is active; ``neutralise`` clears the
    option in the collected params object right before the tool runs, so a flag
    enabled before the gate became active is never passed to colprof/profcheck.
    """
    widgets: list[QWidget] = field(default_factory=list)
    neutralise: Callable[[Any], None] = lambda params: None
