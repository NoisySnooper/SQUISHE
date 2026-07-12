# Code-audit backlog (2026-07-06)

A 33-agent audit reviewed app.py and every module. The confirmed bugs and
all zero-risk fixes were applied the same day (see git diff once committed).
This file is the remainder: real findings, deliberately not applied yet
because they move code around in a working GUI. Each is safe to do in an
isolated batch with the selftest + pytest + screenshot routine.

## Unverified bug candidates (verify first, then fix)

1. Tick/label "edited" flags trip on any key. `_mark_tick_edited` /
   `_mark_label_edited` fire on `<KeyRelease>`, so pressing an arrow or
   Ctrl+C in an auto-filled box marks it user-edited; the box then stops
   auto-syncing and a stale tick spacing sticks after the next zoom.
   Fix: only set the flag when the text actually changed.
2. `_style_colorbar` wraps its whole body in one try/except pass; a single
   blank numeric box (e.g. colorbar tick size) silently skips label text,
   tick count, and border styling. Fix: per-field parsing with fallbacks.
3. `_show_readout_table` clamps to the nearest edge point, so a readout at
   2000 nm silently reports the 1066 nm value. Fix: show "-" when the miss
   distance exceeds a few nm.
4. `_draw_inspect`'s twinx absorbance axis is never themed: dark mode shows
   black label/ticks on dark background. Fix: style ax2 in `_cosmetics_2d`.

## Deduplication / structure (all judged safe by the audit)

- Single-source the section order: derive `_reorder_sections`' list from
  `_tabspec` instead of maintaining 20 titles twice.
- `_set_limit_boxes` helper for the paste in `_on_release` +
  `_capture_zoom_limits`.
- Extract the vertical/horizontal reference-marker block (~23 lines x2).
- Merge `_grid_row` / `_style_row` widget-row builders.
- 3D camera default (elev 22, azim -60, zoom 1.0) is hardcoded in 4 places;
  hoist to constants.
- `_best_raw_channel` helper for the two dict-comprehension copies in
  `_finish_run`.
- One `_float_or(var, default)` method replacing ~7 local helpers and
  inline try/excepts.
- wf3d edge-color resolution pasted 3x; extract `_resolve_rgb`.
- `_start_worker` helper for the thread/queue scaffolding shared by `_run`
  and `_load_previous`.
- `_interval_ticks` helper shared by the two marker fillers.
- Rename reused group-frame locals in `_build_right` ('lg' reused for two
  groups; 'fg'/'d' contents interleaved) and build each group contiguously.
- After the applied dead-code deletions, `rev` (both draw functions) and
  `n` (`_draw_overlay` only) are likely unused; verify and delete.
- engine.py: straighten the single-iteration `for arep in [...]` loop;
  remove the unreachable second pressure parse; consolidate the result-dict
  construction shared by `process_group` / `load_processed_folder`.
- defringe.py: one `_absorbance()` helper so the log10 sign convention has
  a single source (also used by app.py `_notch_result`).
- smoothing.py: rewrite `_sg_chunks`' three-branch parameter choice as
  explicit cases (documents that a NaN midpoint takes the right-side
  params).

## Docs-only leftovers

- `_poll_run` <-> engine.py log-line coupling note also belongs next to
  the `log()` call in engine.run.
