# Changelog

## v1.2.1

- Colour extremes generator: the "per end" maximum is raised from 100 to
  200, matching Pastels and Highlights & shadows (Knut).
- Vendored modules synced from ChromIQ 3.13.0-beta.142 (last sync was
  beta.62): dozens of layout-engine, generator and editor improvements
  flow in — including the engine keeping exact chart geometry, the
  renderer fixes from the scanner-profiling work, and refreshed
  translations for all 13 languages.
- Two new built-in Scanner presets by Knut Georg Larsson (A4-6860p and
  Letter-6500p, two pages each) appear in the preset choices.

## v1.2.0

- Optional shuffled copy on save: a checkbox in the save dialog writes a
  second version of the chart into a "shuffled" subfolder, with the patch
  order re-arranged for the best possible contrast between neighbouring
  patches and between strips. Meant for i1Profiler, which measures an
  imported patch set exactly in file order and has no shuffle of its own.
  The main save keeps the designed order; the same chart always produces
  the same shuffled copy.
- The save prompt is a proper name + location dialog now (with Browse…)
  instead of a file browser. This fixes re-saving over an existing chart
  folder — the browser's button turned into "Open" and navigated into the
  folder instead of saving; now you get an overwrite confirmation. The
  last-used location is remembered.
- The save dialog and the main window's checkboxes and inputs use the
  editor's magenta accent (they fell back to cyan before), and the folder
  picker's navigation arrows are readable in dark mode.
- New strings are translated in all 12 languages.

## v1.1.0

- The "Seed from targen" and "Blank canvas" options are removed from the
  New chart window — the colour-set generators cover the same ground
  (targen was also the only feature that needed ArgyllCMS).
- Preferences: the ArgyllCMS location section is gone; with targen removed,
  the app never runs an Argyll binary, so nothing needs the path any more.
- The New-chart help text no longer describes the removed modes
  (in all 12 languages).
- Unchecked radio buttons are visible in dark mode again (their ring used a
  palette colour that vanished on the dark background).

## v1.0.1

- ChromIQ Patches branding throughout: masthead wordmark (with the ChromIQ
  "IQ" treatment), window title, and a standalone welcome tooltip.
- All standalone strings are now translated in every supported language
  (12 languages) via a repo-owned catalog overlay.
- Update popup, preferences (language / appearance / ArgyllCMS location with
  auto-detect), version number in the footer.
- README: screenshot added; export description corrected (no .cht/.ti2).

## v1.0.0

- First release of ChromIQ Patches — ChromIQ's chart-design tool as a
  standalone app, based on Knut Georg Larsson's original idea.
- Everything renders with the built-in ChromIQ layout engine (i1Pro strip
  geometry) — ArgyllCMS is never required to design, preview or save.
- Save / Export… writes the standalone deliverable: the .ti1 patch set,
  colour list, i1Profiler .txt/.pxf and the page TIFFs.
- Loading a .ti2 imports its patches and lays them out fresh with the engine.
- Preferences: language, appearance, ArgyllCMS location (optional, only for
  the targen option) — plus an update check against this repo's releases.

## v1.0.0-beta.1 (superseded by v1.0.0)

- First public cut — ChromIQ's chart-design tool as a standalone app,
  based on Knut Georg Larsson's original idea.
- Combinable patch generators with live counts and 3D RGB-cube preview.
- ChromIQ layout engine renders print-ready TIFF charts for i1Pro and
  ColorMunki — no ArgyllCMS required.
- Direct i1Profiler export (`.txt` + `.pxf`) and ArgyllCMS-compatible
  `.ti1`/`.ti2`/`.cht` output.
- Built-in preset line-up shared with ChromIQ (Knut's full-layout setups and
  the "by Pharmacist" targets).
- Engine modules vendored byte-identical from ChromIQ v3.13.0-beta.62.
