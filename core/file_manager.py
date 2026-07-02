"""Working-folder management for ChromIQ sessions.

The folder layout owned by this module:

    work_dir/                          # one per project (target name)
      project.json                     # manifest (schema_version, current_run, runs[])
      cal/                             # optional, shared across runs
        calibration.cal
        calibration.ti1 / .ti2 / .ti3 / .icc
        calibration_NN.tif             # NN = page index
        calibration.cht / .ps
        meta.json
      exports/                         # external-tool exports (i1Profiler etc.)
        i1profiler.txt
        i1profiler.pxf
      runs/
        run1/                          # one folder per profile build
          chart.ti1 / .ti2 / .cht / .ps / .channels.json
          chart_NN.tif
          reads/                       # only when averaging used
            read1.ti3 / read2.ti3 ...
          measurement.ti3              # canonical measurement (single or averaged)
          preconditioning.ti3 / .icc   # only when run was promoted from a parent
          merged.ti3                   # only when ti3_merge runs (refinement on)
          profile.icc
          meta.json
        run2/ ...

The role of every file is encoded in its filename within a single folder; the
folder names disambiguate between runs and between session-level vs. run-level
artefacts. There are no prefix/suffix conventions left to remember.

All path construction in the app must go through ``Project`` / ``Run`` /
``Calibration``. String-concatenating paths anywhere else is a code smell.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_ILLEGAL = re.compile(r"[^\w\-.]+", re.UNICODE)
_TRAIL   = re.compile(r"^[._-]+|[._-]+$")   # also a trailing "-" from an empty descriptive-prefix tail

# Extensions ChromIQ itself generates during a session. A user-entered target
# name (or a loaded file's stem) must never carry one of these: the name is
# used verbatim as the working-folder name, so a name ending in e.g. ".icm"
# poisons every derived path.
_WORKFILE_EXTS = frozenset({
    ".icc", ".icm", ".mpp",
    ".ti1", ".ti2", ".ti3",
    ".tif", ".tiff",
    ".cal",
})

# Inside a Run.reads_dir, files are read1.ti3, read2.ti3, …
_NEW_READ_RE = re.compile(r"^read(\d+)$")


# ---------------------------------------------------------------------------
# Manifest dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProjectManifest:
    """The contents of ``project.json``."""
    schema_version: int = 1
    created_at: str = ""
    target_name: str = ""
    current_run: str = "run1"
    runs: list[str] = field(default_factory=lambda: ["run1"])

    @classmethod
    def fresh(cls, target_name: str) -> "ProjectManifest":
        return cls(
            schema_version=1,
            created_at=datetime.now().isoformat(timespec="seconds"),
            target_name=target_name,
            current_run="run1",
            runs=["run1"],
        )

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectManifest":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class RunMeta:
    """The contents of ``runs/runN/meta.json``."""
    run_id: str = ""
    created_at: str = ""
    parent_run: str | None = None
    instrument: str = ""
    paper: str = ""
    averaging_enabled: bool = False
    averaging_method: str = "mean"
    averaging_read_count: int = 0
    # Opt-in: keep scanner-recognition files (.cht + .cie) for this chart, rebuilt
    # from the measurement whenever it's finalised (#97). Off unless the user ticks
    # the "All Stripes Read" checkbox; only meaningful for engine charts.
    scanner_target_enabled: bool = False
    preconditioning_source_run: str | None = None
    # Set to "merged.ti3" when a refinement merge ran; otherwise the canonical
    # measurement carries the (project-name) chart stem.
    profile_built_from: str = ""
    status: str = "in_progress"          # in_progress | complete
    # TI2 layout editor only: the printtarg layout knobs (a LayoutOptions dict)
    # the chart was rendered with + its file basename, so reopening the chart in
    # the editor restores the panel exactly as saved. printtarg discards these
    # once a chart is rendered, and they can't be recovered from the .ti2 alone.
    # The main app never sets or reads these — they stay None / "" for its runs.
    editor_layout: dict | None = None
    editor_basename: str = ""
    # TI2 layout editor only: the "creation recipe" — the New chart / Add window
    # state (_collect_gen_state: source mode, colour-set generators, instrument /
    # paper, layout) that produced the chart. Distinct from editor_layout (the
    # printtarg layout the Create Chart tab can edit): editor_layout is reloaded
    # when the chart reopens in the editor, while editor_recipe is reloaded into
    # the New chart / Add windows so the design can be tweaked / recreated.
    editor_recipe: dict | None = None

    @classmethod
    def fresh(cls, run_id: str, parent: str | None = None) -> "RunMeta":
        return cls(
            run_id=run_id,
            created_at=datetime.now().isoformat(timespec="seconds"),
            parent_run=parent,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RunMeta":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Calibration — shared across all runs in a project
# ---------------------------------------------------------------------------

class Calibration:
    """The ``cal/`` folder. One calibration set is shared by every run."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    @property
    def stem(self) -> str:
        """File stem for calibration artefacts: ``<project>-cal``.

        Named after the project (so printtarg stamps it on the printed sheet)
        with a ``-cal`` marker so a printed calibration target is
        distinguishable from the profiling chart, which shares the project name.
        """
        return f"{self._root.name}-cal"

    @property
    def dir(self) -> Path:                    return self._root / "cal"
    @property
    def cal_path(self) -> Path:               return self.dir / f"{self.stem}.cal"
    @property
    def ti1(self) -> Path:                    return self.dir / f"{self.stem}.ti1"
    @property
    def ti2(self) -> Path:                    return self.dir / f"{self.stem}.ti2"
    @property
    def ti3(self) -> Path:                    return self.dir / f"{self.stem}.ti3"
    @property
    def icc(self) -> Path:                    return self.dir / f"{self.stem}.icc"
    @property
    def cht(self) -> Path:                    return self.dir / f"{self.stem}.cht"
    @property
    def ps(self) -> Path:                     return self.dir / f"{self.stem}.ps"
    @property
    def channels_json(self) -> Path:          return self.dir / f"{self.stem}.channels.json"
    @property
    def meta_path(self) -> Path:              return self.dir / "meta.json"

    def chart_tiffs(self) -> list[Path]:
        # `<stem>*.tif` matches both single-page <stem>.tif and multi-page
        # <stem>_NN.tif (see Run.chart_tiffs for the rationale).
        if not self.dir.exists():
            return []
        out: set[Path] = set()
        for pattern in (f"{self.stem}*.tif", f"{self.stem}*.TIF", f"{self.stem}*.tiff"):
            out.update(self.dir.glob(pattern))
        return sorted(out)

    def exists(self) -> bool:
        """True when at least one calibration artefact is on disk."""
        return self.cal_path.exists() or self.ti3.exists()

    def ensure_dir(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def reset(self) -> None:
        """Wipe all calibration artefacts (delete ``cal/``)."""
        if self.dir.exists():
            shutil.rmtree(self.dir)
            log.debug("Calibration reset: removed %s", self.dir)


# ---------------------------------------------------------------------------
# Run — one profile build
# ---------------------------------------------------------------------------

class Run:
    """A single profile-build attempt under ``runs/<id>/``.

    Holds chart artefacts, measurement(s), optional pre-conditioning seed, and
    the built profile. All path construction lives here — callers never build
    filenames by string concatenation.
    """

    def __init__(self, project: "Project | None", run_id: str,
                 dir_override: Path | None = None) -> None:
        self._project = project
        self._run_id = run_id
        self._dir_override = dir_override

    @classmethod
    def for_dir(cls, run_dir: Path) -> "Run":
        """A project-less Run bound to an explicit folder.

        Useful where only path operations on a known run directory are needed
        (e.g. the Measure tab deriving the run from the chart's .ti1 parent)
        without threading the whole Project through. Project-dependent
        operations (new_run seeding) aren't available on such a Run.
        """
        return cls(None, run_dir.name, dir_override=run_dir)

    # ---- identity & dir
    @property
    def id(self) -> str:                      return self._run_id
    @property
    def dir(self) -> Path:
        if self._dir_override is not None:
            return self._dir_override
        return self._project.runs_root / self._run_id

    @property
    def stem(self) -> str:
        """Chart file stem = the (sanitised) project folder name.

        The run dir is ``<project>/runs/<id>``, so the project folder is
        ``dir.parents[1]``. Using the project name as the stem means printtarg
        stamps it on the printed sheet, the built ICC is self-identifying, and
        Finder shows it — while the per-run folder still removes the need for
        any state-encoding prefix/suffix. Derived from the folder so it works
        for both project-backed and Run.for_dir instances.
        """
        return self.dir.parents[1].name

    # ---- chart artefacts (regenerated by chart_creator)
    # chartread/colprof are stem-coupled (reading <stem>.ti2 → <stem>.ti3 →
    # <stem>.icc), so the whole chart chain shares the project-name stem. The
    # per-run folder removes the need for prefixes/suffixes; reads/ and the
    # role files (merged/preconditioning/calibrated) stay role-named.
    @property
    def chart_ti1(self) -> Path:              return self.dir / f"{self.stem}.ti1"
    @property
    def chart_ti2(self) -> Path:              return self.dir / f"{self.stem}.ti2"
    @property
    def chart_cht(self) -> Path:              return self.dir / f"{self.stem}.cht"
    @property
    def chart_ps(self) -> Path:               return self.dir / f"{self.stem}.ps"
    @property
    def chart_channels_json(self) -> Path:    return self.dir / f"{self.stem}.channels.json"

    def chart_tiffs(self) -> list[Path]:
        """All chart page bitmaps in this run, sorted.

        Matches both single-page `<stem>.tif` (printtarg's output for one page)
        and multi-page `<stem>_NN.tif` — the glob is `<stem>*.tif`, mirroring
        chart_creator._printtarg_done. Using `<stem>_*.tif` (underscore) would
        silently miss single-page charts.
        """
        if not self.dir.exists():
            return []
        out: set[Path] = set()
        for pattern in (f"{self.stem}*.tif", f"{self.stem}*.TIF", f"{self.stem}*.tiff"):
            out.update(self.dir.glob(pattern))
        return sorted(out)

    # ---- measurements
    # The canonical measurement is ``<stem>.ti3`` — chartread is stem-coupled
    # (reading ``<stem>.ti2`` produces ``<stem>.ti3``). Per-read averaging
    # snapshots live in reads/readN.ti3 and are averaged back into <stem>.ti3.
    @property
    def measurement_ti3(self) -> Path:        return self.dir / f"{self.stem}.ti3"
    @property
    def reads_dir(self) -> Path:              return self.dir / "reads"

    def reads(self) -> list[Path]:
        """Sorted list of reads/readN.ti3 files."""
        if not self.reads_dir.exists():
            return []
        found: list[tuple[int, Path]] = []
        for f in self.reads_dir.glob("read*.ti3"):
            m = _NEW_READ_RE.match(f.stem)
            if m:
                found.append((int(m.group(1)), f))
        found.sort(key=lambda t: t[0])
        return [f for _, f in found]

    def next_read_index(self) -> int:
        reads = self.reads()
        if not reads:
            return 1
        nums = [int(_NEW_READ_RE.match(f.stem).group(1)) for f in reads]
        return max(nums) + 1

    def next_read_path(self) -> Path:
        return self.reads_dir / f"read{self.next_read_index()}.ti3"

    def clear_reads(self) -> None:
        if self.reads_dir.exists():
            shutil.rmtree(self.reads_dir)

    def promote_measurement_to_read(self) -> Path:
        """Move ``chart.ti3`` to the next ``reads/readN.ti3`` slot.

        Used when the user clicks "Measure again to average" — the just-finished
        measurement becomes the first (or next) input to averaging.
        Returns the new path.
        """
        if not self.measurement_ti3.exists():
            raise FileNotFoundError(
                f"Nothing to promote: {self.measurement_ti3} does not exist"
            )
        self.reads_dir.mkdir(parents=True, exist_ok=True)
        dst = self.next_read_path()
        shutil.move(str(self.measurement_ti3), str(dst))
        log.info("Promoted measurement to %s", dst.name)
        return dst

    # ---- pre-conditioning (set when this run was created from a parent)
    @property
    def preconditioning_ti3(self) -> Path:    return self.dir / "preconditioning.ti3"
    @property
    def preconditioning_icc(self) -> Path:    return self.dir / "preconditioning.icc"

    def has_preconditioning(self) -> bool:
        return self.preconditioning_ti3.exists() and self.preconditioning_icc.exists()

    # ---- build-time merge output (only when chromiq_refinement is on)
    # merged.ti3 = average -m of chart.ti3 + preconditioning.ti3, fed to
    # colprof to build merged.icc. The clean chart.ti3 stays untouched for
    # Check/Refine (Architecture D).
    @property
    def merged_ti3(self) -> Path:             return self.dir / "merged.ti3"
    @property
    def merged_icc(self) -> Path:             return self.dir / "merged.icc"

    # ---- profile output
    # colprof reading <stem>.ti3 writes <stem>.icc (stem-coupled). When a merge
    # ran, the deliverable is merged.icc instead — see built_profile_icc().
    @property
    def profile_icc(self) -> Path:            return self.dir / f"{self.stem}.icc"

    def built_profile_icc(self) -> Path:
        """The profile a user should treat as the run's output.

        ``merged.icc`` when a pre-conditioning merge produced one, else the
        plain ``chart.icc``.
        """
        return self.merged_icc if self.merged_icc.exists() else self.profile_icc

    # ---- applycal output (calibration baked into a built profile)
    @property
    def calibrated_icc(self) -> Path:         return self.dir / "calibrated.icc"

    # ---- meta
    @property
    def meta_path(self) -> Path:              return self.dir / "meta.json"

    def load_meta(self) -> RunMeta:
        if not self.meta_path.exists():
            return RunMeta.fresh(self._run_id)
        return RunMeta.from_dict(json.loads(self.meta_path.read_text(encoding="utf-8")))

    def save_meta(self, meta: RunMeta) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")

    # ---- lifecycle
    def ensure_dir(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir

    def reset_chart_artefacts(self) -> None:
        """Wipe chart files + reads + measurement + merged + profile.

        Preserves ``preconditioning.*`` and ``meta.json`` so the run's identity
        and pre-conditioning seed survive a chart re-generation.
        """
        s = self.stem
        for name in (
            f"{s}.ti1", f"{s}.ti2", f"{s}.cht", f"{s}.ps",
            f"{s}.channels.json",
            f"{s}.ti3",                  # the measurement (chartread output)
            f"{s}.icc",                  # the profile (colprof output)
            "merged.ti3", "merged.icc",  # build-time refinement merge outputs
            "calibrated.icc",            # applycal output
        ):
            p = self.dir / name
            if p.exists():
                try:
                    p.unlink()
                except OSError as exc:
                    log.warning("Could not delete %s: %s", p, exc)
        for tiff in self.chart_tiffs():
            try:
                tiff.unlink()
            except OSError as exc:
                log.warning("Could not delete %s: %s", tiff, exc)
        self.clear_reads()


# ---------------------------------------------------------------------------
# Project — the work_dir root
# ---------------------------------------------------------------------------

_PROJECT_README_TEMPLATE = """\
ChromIQ project: {name}

Where to find things you might want:

  runs/run1/{name}.icc              ← your built ICC profile
                                      (install this, share this)

  runs/run1/{name}_01.tif           ← the printable chart, page 1
  runs/run1/{name}_02.tif           ← page 2 (if multi-page)

  runs/run1/{name}.ti2              ← chart layout (for re-measuring)
  runs/run1/{name}.ti3              ← measurements (chartread output)

  cal/{name}-cal.cal                ← calibration curves (if you made one)
  exports/{name}-i1profiler.pxf     ← for i1Profiler (if you exported)


Other files and folders you may see, and why:

  project.json                      ChromIQ's project manifest. Read on start-up
                                    to find out which run is current.

  runs/runN/                        One folder per profile build. Each is
                                    self-contained; the highest N is the
                                    "current" one.

  runs/runN/meta.json               Per-run info (created_at, parent run,
                                    averaging method, etc.).

  runs/runN/{name}.ti1              Chart definition (targen output), fed to
                                    printtarg. You don't normally touch it.

  runs/runN/{name}.channels.json    Ink-channel sidecar — lets ChromIQ identify
                                    inks when re-opening a chart later.

  runs/runN/reads/readN.ti3         Per-read measurements when you use
                                    "Read again & average". They get averaged
                                    back into {name}.ti3 when you finish.

  runs/runN/preconditioning.ti3     Copies of the parent run's measurement and
  runs/runN/preconditioning.icc     profile, created when you click "Use as
                                    pre-conditioning profile" on a finished
                                    build. ChromIQ uses them to refine the
                                    next chart.

  runs/runN/merged.ti3              Build-time merge of {name}.ti3 +
  runs/runN/merged.icc              preconditioning.ti3 (ChromIQ-style
                                    refinement). colprof builds from
                                    merged.ti3; on install you still get the
                                    clean {name}.icc name.

  runs/runN/calibrated.icc          applycal output — the run's profile with
                                    the calibration .cal baked in.

  cal/                              Calibration target (optional; shared by
                                    every run in this project).

  cal/{name}-cal.ti1 / .ti2 / _NN.tif    The calibration chart (same shape as
                                         a run's chart, with the "-cal" marker
                                         so a printed sheet is distinguishable
                                         from the profiling chart).

  cal/{name}-cal.ti3                The calibration measurement.

  cal/{name}-cal.cal                Calibration curves (printcal output) — the
                                    file applycal bakes into your profile.

  exports/                          External-tool exports.

  exports/{name}-i1profiler.txt     For i1Profiler (RGB / CMYK only).
  exports/{name}-i1profiler.pxf     For i1Profiler (always written).

  Where are my files.txt            This file. Informational only — ChromIQ
                                    does not read or update it after creating
                                    it. Edit or delete it freely.
"""


class Project:
    """A working-folder project. Owns ``project.json`` and all runs."""

    MANIFEST = "project.json"
    README   = "Where are my files.txt"

    def __init__(self, root: Path, manifest: ProjectManifest) -> None:
        self._root = root
        self._manifest = manifest

    # ---- identity
    @property
    def root(self) -> Path:                   return self._root
    @property
    def target_name(self) -> str:             return self._manifest.target_name
    @property
    def runs_root(self) -> Path:              return self._root / "runs"
    @property
    def exports_dir(self) -> Path:            return self._root / "exports"
    @property
    def calibration(self) -> Calibration:    return Calibration(self._root)
    @property
    def manifest_path(self) -> Path:          return self._root / self.MANIFEST
    @property
    def readme_path(self) -> Path:            return self._root / self.README

    # ---- manifest I/O
    @classmethod
    def create(cls, root: Path, target_name: str) -> "Project":
        """Create a fresh project at ``root`` with ``run1`` prepared."""
        manifest = ProjectManifest.fresh(target_name)
        proj = cls(root, manifest)
        proj._root.mkdir(parents=True, exist_ok=True)
        proj.runs_root.mkdir(parents=True, exist_ok=True)
        run = proj.current_run()
        run.ensure_dir()
        run.save_meta(RunMeta.fresh("run1"))
        proj.save_manifest()
        proj.write_readme()
        log.info("Created project at %s", root)
        return proj

    @classmethod
    def load(cls, root: Path) -> "Project":
        mp = root / cls.MANIFEST
        if not mp.exists():
            raise FileNotFoundError(f"No project manifest at {mp}")
        data = json.loads(mp.read_text(encoding="utf-8"))
        proj = cls(root, ProjectManifest.from_dict(data))
        # Backfill the README for projects created before it shipped — and
        # rewrite a 0-byte file, which is exactly the artefact a pre-fix Windows
        # build left behind: write_readme crashed mid-write (UnicodeEncodeError
        # encoding the template's arrows under the cp1252 default), leaving the
        # file created but empty. Never touch a non-empty file — the user is
        # free to edit theirs.
        rp = proj.readme_path
        if not rp.exists() or rp.stat().st_size == 0:
            proj.write_readme()
        return proj

    @classmethod
    def create_or_load(cls, root: Path, target_name: str) -> "Project":
        if (root / cls.MANIFEST).exists():
            return cls.load(root)
        return cls.create(root, target_name)

    def save_manifest(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(asdict(self._manifest), indent=2), encoding="utf-8")

    def write_readme(self) -> None:
        """Write a user-facing "Where are my files.txt" at the project root.

        Written by ``create`` for new projects and backfilled by ``load`` if
        absent. Never overwrites an existing file — the user is free to edit
        or delete it.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self.readme_path.write_text(
            _PROJECT_README_TEMPLATE.format(name=self.target_name),
            encoding="utf-8",
        )

    def rename(self, new_stem: str) -> None:
        """Relabel an in-place project from its current stem to ``new_stem``.

        Chart artefacts carry the project name as their file stem (see
        ``Run.stem`` / ``Calibration.stem``), so simply moving the project
        folder is not enough — the files inside would keep the old stem while
        every ``Run``/``Calibration`` path property now resolves to the new one,
        silently breaking the project. This renames every ChromIQ-generated file
        whose stem is the old name (across ``runs/``, ``cal/`` and ``exports/``),
        updates ``project.json`` and rewrites the README.

        ``self._root`` must already be at the new location (the folder move is
        the caller's job) — this fixes up the contents and the manifest. A
        no-op when ``new_stem`` equals the current name.
        """
        old_stem = self._manifest.target_name
        if not old_stem or not new_stem or new_stem == old_stem:
            return

        # Only rename files shaped like a ChromIQ artefact for this stem:
        #   <stem>[-cal|-i1profiler|-colours][_NN].<ext...>
        # so a user's own "<stem>-notes.txt" is left untouched, and structural
        # files (project.json, meta.json, the README) never match. The bare
        # extensions (.ti1/.ti2/.cht/.cie/…) match via the trailing \.[\w.]+$.
        protected = {self.MANIFEST, self.README, "meta.json"}
        tail_re = re.compile(r"(-cal|-i1profiler|-colours)?(_\d+)?\.[\w.]+$")

        for f in sorted(self._root.rglob("*")):
            if not f.is_file() or f.name in protected:
                continue
            if not f.name.startswith(old_stem):
                continue
            tail = f.name[len(old_stem):]
            if not tail_re.fullmatch(tail):
                continue
            dst = f.with_name(new_stem + tail)
            if dst.exists():
                log.warning("Rename target already exists, skipping: %s", dst)
                continue
            f.rename(dst)

        self._manifest.target_name = new_stem
        self.save_manifest()
        self.write_readme()
        log.info("Renamed project stem %s -> %s at %s", old_stem, new_stem, self._root)

    # ---- run access
    def run(self, run_id: str) -> Run:
        return Run(self, run_id)

    def current_run(self) -> Run:
        return Run(self, self._manifest.current_run)

    def all_runs(self) -> list[Run]:
        return [Run(self, rid) for rid in self._manifest.runs]

    def has_run(self, run_id: str) -> bool:
        return run_id in self._manifest.runs

    def set_current_run(self, run_id: str) -> None:
        if run_id not in self._manifest.runs:
            raise ValueError(f"Unknown run: {run_id}")
        self._manifest.current_run = run_id
        self.save_manifest()

    def new_run(self, *, preconditioning_from: Run | None = None) -> Run:
        """Create a new ``runN`` folder; if seeded with ``preconditioning_from``,
        copy the parent's ``profile.icc`` and ``measurement.ti3`` into the new
        run as ``preconditioning.icc`` / ``preconditioning.ti3``.

        Updates the manifest to make the new run current. Returns it.
        """
        run_id = f"run{self._next_run_index()}"
        new_run = Run(self, run_id)
        new_run.ensure_dir()

        meta = RunMeta.fresh(run_id)
        if preconditioning_from is not None:
            if not preconditioning_from.profile_icc.exists():
                raise FileNotFoundError(
                    f"Parent run {preconditioning_from.id} has no profile.icc"
                )
            if not preconditioning_from.measurement_ti3.exists():
                raise FileNotFoundError(
                    f"Parent run {preconditioning_from.id} has no measurement.ti3"
                )
            shutil.copy2(preconditioning_from.profile_icc, new_run.preconditioning_icc)
            shutil.copy2(preconditioning_from.measurement_ti3, new_run.preconditioning_ti3)
            meta.parent_run = preconditioning_from.id
            meta.preconditioning_source_run = preconditioning_from.id
            log.info(
                "New run %s seeded with preconditioning from %s",
                run_id, preconditioning_from.id,
            )

        new_run.save_meta(meta)
        self._manifest.runs.append(run_id)
        self._manifest.current_run = run_id
        self.save_manifest()
        return new_run

    def _next_run_index(self) -> int:
        n = 0
        for rid in self._manifest.runs:
            m = re.match(r"run(\d+)$", rid)
            if m:
                n = max(n, int(m.group(1)))
        return n + 1

    # ---- exports
    def ensure_exports_dir(self) -> Path:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        return self.exports_dir


# ---------------------------------------------------------------------------
# FileManager — thin wrapper holding target_name + settings, exposing a
# Project for the current working folder.
# ---------------------------------------------------------------------------

class FileManager:
    def __init__(self, settings: "AppSettings") -> None:
        self._settings = settings
        self._target_name: str = ""
        self._project: Project | None = None

    # ---- target name
    @staticmethod
    def strip_workfile_ext(name: str) -> str:
        """Strip any trailing ChromIQ work-file extension(s) from a target name.

        Handles stacked extensions ("chart.icm.ti3" -> "chart") so a name
        pasted from an existing generated file can't poison a new session.
        Dots that are not a known extension (e.g. "Pro.1000") are preserved.
        """
        s = name.strip()
        while True:
            stem, dot, ext = s.rpartition(".")
            if dot and ("." + ext.lower()) in _WORKFILE_EXTS:
                s = stem.rstrip()
                continue
            return s

    @staticmethod
    def _sanitise(name: str) -> str:
        s = name.strip().replace(" ", "-")
        s = _ILLEGAL.sub("_", s)
        s = _TRAIL.sub("", s)
        return s or "session"

    def set_target_name(self, name: str) -> None:
        cleaned = self.strip_workfile_ext(name)
        if not cleaned.strip():
            self._target_name = self._auto_name()
        else:
            self._target_name = self._sanitise(cleaned)
        # Invalidate cached Project — new name = different folder.
        self._project = None
        log.debug("Target name set to: %s", self._target_name)

    def get_target_name(self) -> str:
        if not self._target_name:
            self._target_name = self._auto_name()
        return self._target_name

    @classmethod
    def default_target_name(
        cls,
        printer: str = "Printer",
        paper: str = "Paper",
        papertype: str = "Type",
        instrument: str = "Instr",
    ) -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        parts = [printer, paper, papertype, instrument, ts]
        return "_".join(cls._sanitise(p) for p in parts)

    def _auto_name(self) -> str:
        return self.default_target_name()

    # ---- folder resolution
    def root_dir(self) -> Path:
        custom = self._settings.get("custom_output_path", "")
        return Path(custom) if custom else Path.home() / "ChromIQ"

    def working_dir(self) -> Path:
        return self.root_dir() / self.get_target_name()

    def preview_project_root(self, raw_name: str) -> Path | None:
        """Compute the project root for a not-yet-set target name.

        Used by UI live-validation (e.g. tab_chart's "is there a calibration
        file for this project?" check). Returns None if the cleaned name is
        empty.
        """
        cleaned = self.strip_workfile_ext(raw_name)
        if not cleaned.strip():
            return None
        return self.root_dir() / self._sanitise(cleaned)

    def ensure_folder(self) -> Path:
        d = self.working_dir()
        d.mkdir(parents=True, exist_ok=True)
        log.debug("Working dir: %s", d)
        return d

    # ---- project access (the new API)
    def project(self) -> Project:
        """Return the Project for the current target.

        Creates ``project.json`` + ``runs/run1/`` on first call for a target.
        Subsequent calls return the cached project (invalidated by
        ``set_target_name``).
        """
        if self._project is None:
            root = self.working_dir()
            self._project = Project.create_or_load(root, self.get_target_name())
        return self._project

    def rename_existing_project(self, old_name: str, new_name_raw: str) -> Path:
        """Move the project folder ``old_name`` to the sanitised ``new_name`` and
        fix every artefact stem + the manifest inside it.

        Used when the user changes the Output name after a first generate and
        chooses "rename". Makes the renamed project the current target. Returns
        the new root.

        Raises ``FileExistsError`` if a project already occupies the new name,
        and ``FileNotFoundError`` if ``old_name`` is not a project on disk.
        """
        root = self.root_dir()
        old_root = root / old_name
        new_root = self.preview_project_root(new_name_raw)
        if new_root is None:
            raise ValueError("Empty target name")
        if new_root == old_root:
            return old_root
        if not (old_root / Project.MANIFEST).exists():
            raise FileNotFoundError(old_root)
        if new_root.exists():
            raise FileExistsError(new_root)

        shutil.move(str(old_root), str(new_root))
        proj = Project.load(new_root)
        proj.rename(new_root.name)
        self._target_name = new_root.name
        self._project = proj
        return new_root

    def project_has_built_profile(self, name: str) -> bool:
        """True if a project ``name`` exists on disk and any run holds a built
        ICC profile (the deliverable). Used to block renaming a profile once it
        has been created — at that point the embedded ICC description is baked
        in, so the user copies it to a new name instead (#70, Knut).
        """
        root = self.preview_project_root(name)
        if root is None or not (root / Project.MANIFEST).exists():
            return False
        try:
            proj = Project.load(root)
        except Exception as exc:  # noqa: BLE001 — a corrupt manifest isn't fatal here
            log.warning("Could not inspect project '%s' for a built profile: %s",
                        name, exc)
            return False
        return any(r.built_profile_icc().exists() for r in proj.all_runs())

    def delete_project_folder(self, name: str) -> None:
        """Permanently delete a ChromIQ project folder.

        Guarded so a stray/empty name can never remove something unexpected: the
        folder must live directly under :meth:`root_dir` and contain a
        ``project.json``. Anything else is refused with a warning.
        """
        root = self.root_dir()
        target = root / name
        if target == root or target.parent != root:
            log.warning("Refusing to delete unsafe path: %s", target)
            return
        if not (target / Project.MANIFEST).exists():
            log.warning("Refusing to delete non-project folder: %s", target)
            return
        shutil.rmtree(target)
        log.info("Deleted project folder %s", target)

    def cwd_for_chart(self, *, cal_target: bool) -> Path:
        """Folder chart_creator must run targen/printtarg in.

        Calibration targets go to ``cal/`` (one calibration per project,
        shared across all runs). Normal chart generation goes to the
        current run's folder.
        """
        proj = self.project()
        return proj.calibration.ensure_dir() if cal_target else proj.current_run().ensure_dir()

    def chart_stem(self, *, cal_target: bool) -> str:
        """File stem chart_creator passes to targen/printtarg.

        Calibration targets resolve to ``<project>-cal``; profiling charts to
        the bare (sanitised) project name. printtarg prints this as the chart
        identifier on the page, so it must be descriptive (not the generic
        ``chart``/``calibration`` placeholders from the early redesign).
        """
        proj = self.project()
        return proj.calibration.stem if cal_target else proj.current_run().stem

