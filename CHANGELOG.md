# Changelog

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
