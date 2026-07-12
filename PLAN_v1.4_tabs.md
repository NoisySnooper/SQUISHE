# PLAN v1.4 — Multi-tab sessions, data drawer, and approved UX items
# Handoff for the implementing session (Opus). Written 2026-07-07 by the
# Fable session that shipped v1.3.1. Nhan has approved this scope.
#
# ===================== PROGRESS (2026-07-07, Opus) =====================
# DONE + swapped (app.py sha 9dd7fd0b4fac, APP_VERSION v1.4.0-dev, 33 tests):
#   Phase 1  session core (state-swap; methods namespaced _tab_*/_new_session
#            /_switch_session/_close_session/_capture_session/_store_active;
#            loader is _tab_load because _load_session was already the Open-
#            project feature). tests/test_sessions.py (5 tests).
#   Phase 2  tab strip UI: plain-tk browser tabs, active=bold+accent underline,
#            x close, + new, double-click rename, middle-click close,
#            Ctrl+T/Ctrl+W/Ctrl+Tab/Ctrl+Shift+Tab; run-blocking via _run_busy;
#            auto-name active tab from input folder on run; NUKE = all tabs ->
#            one blank; theme-follows via _sync_tabs in _recolor_tk.
#   Phase 4  data drawer: bottom collapsible (Data toggle + Ctrl+D), Treeview
#            with the 6 CSV cols + defringed/smoothed toggles, trace selector,
#            Copy all (TSV) + Ctrl+C selection, Open in Excel (os.startfile),
#            follows active tab. State via self._drawer_shown (NOT
#            winfo_ismapped -- unreliable under withdrawn/min roots).
#   Phase 6 (partial)  log + guide fonts Consolas 8 -> 9.
#   Phase 5  recents: _push_recent_run -> settings["recent_runs"] (cap 8,
#            dedup by path); clickable blank-state bar (_recent_bar, shown when
#            results empty + recents exist) -> _load_run_folder (factored out
#            of _load_previous, no dialog). Drag-and-drop SKIPPED: tkinterdnd2
#            not installed (wrap in try/except when added; needs pip + a
#            PyInstaller hook at release).
#   Phase 7  provenance sidecars: engine.file_sha1 + engine.write_provenance
#            (pure, tested). app _provenance(target, kind, params, files).
#            Wired: reduction run (_reduction.provenance.json, in worker,
#            locals-only), _save_plot, _batch_export, _export_defringed,
#            _export_smoothed, _export_dlist. 33rd test in test_engine_run.
#   Phase 8  audit four-pack ALL fixed: tick-edit value guard (_tick_autofill),
#            colorbar per-field parse, readout >5nm miss -> '-', inspect twinx
#            themed.
# GOTCHAS:
#  - this Windows Store Python cannot create a 2nd tk.Tk() root; GUI test
#    modules must reuse `tk._default_root or tk.Tk()` (test_legend makes it).
#  - structural checks that call _finish_run/_save_settings write through the
#    LIVE .quicklook_settings.json (recent_runs, in_dir, out_dir). Clean it
#    after testing, or better: point SETTINGS_PATH at a temp file in tests.
#
# ALSO DONE (2026-07-07, later, sha 1a0f62a5cf97):
#   Data drawer reworked into a ttk.PanedWindow (vertical): canvas in the top
#     pane, drawer add()/forget() as a resizable bottom pane -> draggable
#     height. _toggle_drawer uses pw.add/forget + _update_data_btn.
#   Data toggle MOVED off the tab strip to a prominent Accent.TButton at the
#     bottom-right of the plot bar (text flips "Data table"/"Hide data").
#   Phase 6 slim toolbar DONE: _build_plot_toolbar hides NavigationToolbar2Tk
#     (kept for pan/zoom/mode) and shows Home/Pan/Zoom/Save; Pan/Zoom reflect
#     active mode via Accent style (_sync_toolbar_buttons).
#   Phase 3 status DONE: _update_status shows "tab: <name>" (also called from
#     _finish_run after auto-rename so it is not stale).
#
# NOT YET DONE:
#   Phase 5b drag-and-drop: needs `pip install tkinterdnd2` AND the root to be
#     created as TkinterDnD.Tk() (launcher/run.bat change) -- can't verify
#     without the dep, so left out rather than add blind. Wire+test when the
#     dep is installed.
# =======================================================================

## 0. Hard rules (non-negotiable, learned the hard way)

- app.py sha256[:12] at handoff: **cbaeb972d08d**. Verify BEFORE any edit;
  if moved, re-baseline. NEVER assume.
- Dev loop per batch: copy app.py -> app_dev.py, edit app_dev.py, then
  `python app_dev.py --selftest` must print SELFTEST OK, then
  `python -m pytest tests -q` (currently 27, you will ADD tests),
  then structural one-shot (instantiate App on withdrawn root, assert),
  then screenshot probe (WINDOW MUST BE -topmost; a probe once captured
  the user's private email because the app opened behind his browser —
  delete any mis-capture immediately), then sha-guarded swap.
- NEVER put bare tk.Labels inside sv_ttk Card.TFrame or ttk.LabelFrame
  containers and expect background config to work: sv_ttk draws those
  interiors from theme images that IGNORE style.configure. Diagnose color
  issues by PIL pixel-sampling, not style lookups (lookups lie). Plain
  tk.Frame/ttk.Frame honor configured colors. (This cost three rounds.)
- Ellipsis and any non-ASCII in new source: use \uXXXX escapes (house
  style). The Edit tool decodes escape sequences in tool params, so write
  literal chars in edits and normalize to escapes with a bytes-level
  python pass afterward (see the session logs pattern), or avoid them.
- Launch for the user = run.bat only. Never auto-launch. No git commits
  without Nhan's explicit word. NUKE button stays (he likes the joke).
- Line numbers below are approximate. RE-GREP EVERY ANCHOR.

## 1. Scope (approved by Nhan)

A. Browser-style multi-tab sessions in the middle panel (the headline).
B. Excel-style raw-data viewer ("data drawer", design below).
C. Recent runs on the blank state + drag-and-drop folder to load (item 6).
D. Slim plot toolbar + log readability (item 7).
E. Verify + fix the four unverified audit bug candidates (item 8,
   AUDIT_BACKLOG.md section 1).
F. Provenance sidecars on every export (item 3).

Explicitly OUT of scope (parked/rejected — do not build): Simple/Advanced
mode (rejected), live watch mode (item 13, awaits Matt/Kanani meeting),
DPI audit (item 15), structural refactor backlog (AUDIT_BACKLOG section
2), renaming/branding, cross-tab overlay plotting (future idea only).

## 2. THE architectural decision: state-swap, not widget-per-tab

Do NOT create one Figure/canvas/right-panel per tab. The app is a single
5.1k-line widget tree bound to ~100 tk Variables; duplicating it per tab
is memory-heavy and structurally impossible without a rewrite.

Instead: ONE shared widget tree; a tab = a stored SESSION STATE that is
swapped in and out. The machinery already exists: `_snapshot()` /
`_preset_registry()` / the preset-restore path with `self._restoring`
already serialize and reapply every registered control (this is how undo
and presets work). A session is:

```python
session = {
    "name": str,                # tab label, user-renamable
    "results": list,            # engine result dicts (the data)
    "source_dir": str,          # input folder (per-tab!)
    "out_dir": str,             # output folder (per-tab!)
    "last_out_dir": str,
    "snapshot": dict,           # _snapshot() output: registry + traces
                                #   + dvars + smooth_params
    "undo": list, "redo": list, # per-tab undo/redo stacks
    "smooth_cache": dict, "notch_cache": dict,   # swap whole dicts
    "run_state": (text, color), # status chip (see _last_run_state)
    "skipped_count": int,
}
```

`App.sessions: list`, `App.active: int`.

Switch = `_store_active()` (snapshot current into sessions[active]) then
`_load_session(i)`:
  1. self._restoring = True
  2. apply snapshot via the same code path presets use (registry sets;
     grep `_apply_snapshot` / the preset Load handler and REUSE it)
  3. self.results = s["results"]; swap caches; in_var/out_var set
  4. `_build_trace_checks()` then reapply stored trace/dvar booleans
     (persist VALUES as {label: bool}; the widgets are rebuilt each time)
  5. undo/redo stacks swapped; `_set_run_state(*s["run_state"])`
  6. self._restoring = False; `_redraw()`
Axis limits, 3D camera, colormap etc. are registry vars, so they travel
with the snapshot for free. VERIFY that in_var/out_var are NOT in the
registry (presets deliberately exclude folders) and carry them in the
session dict explicitly.

Run-in-progress rule: DISABLE the tab strip while `_run_thread` is alive
(simplest correct answer to "worker finishes into which tab?"). The
alternative (routing _finish_run into the originating session object) is
allowed if you're careful, but blocking is fine for v1.4.

NUKE semantics: stays global — closes all tabs, leaves one blank tab.
Confirm dialog text must say so.

## 3. Tab strip UI

Location: a slim strip at the top of the middle pane, replacing/joining
the "Plot Area" header row. Build it from PLAIN tk widgets (tk.Frame +
tk.Label) recolored from `_theme_palette()` via the `_content_labels` /
`_recolor_tk` mechanisms — NOT ttk.Notebook (it wants to own child
frames; we have one shared child) and NOT Card/LabelFrame (image-fill
trap, rule 0).

Visual: flat tabs, active tab = brighter text + 2px accent underline
(tk.Frame height=2 colored with the theme accent), inactive = muted fg.
Each tab: label text + a small ✕ (hidden until hover is fine, or always
shown small). Rightmost: a "+" button (new blank tab).

Interactions:
- click tab -> activate (store/load swap)
- double-click label -> inline rename: place() a tk.Entry over the label,
  Enter/FocusOut commits, Escape cancels
- middle-click -> close tab
- ✕ -> close tab; closing the last tab creates a fresh blank one
- "+" -> new blank session: empty results, snapshot = current DEFAULTS
  (`self._defaults` exists, grep it), name "New tab N"
- default tab name on run/load = basename of the source folder
- keyboard: Ctrl+Tab next tab, Ctrl+W close (respect `_typing_in_box()`)

Tabs are session-local (not persisted across launches) in v1.4. The
recent-runs blank state (Sec. 5) covers relaunch continuity.

## 4. Data drawer (the raw-data viewer)

Design (Nhan asked for a design; this is it, approved in principle):
a COLLAPSIBLE BOTTOM DRAWER under the plot, browser-devtools style. Zero
clutter when closed; one click when needed; fits the browser metaphor.

- Toggle: a small "Data" button at the right end of the tab strip +
  Ctrl+D. Drawer default height ~35% of the plot pane (a ttk.PanedWindow
  sash between canvas and drawer gives free resizing).
- Content: ttk.Treeview in grid mode, columns exactly matching the CSV
  schema: Wavelength_nm | Wavenumber_cm-1 | Absorbance | Dark |
  Background | Sample, plus toggleable computed columns (defringed A,
  smoothed A) via two checkbuttons in the drawer header.
- Trace selector: a combobox in the drawer header listing the active
  tab's traces (sync it with the Inspect selection when Inspect mode is
  on). One trace at a time — that IS the Excel-view unit.
- Treeview handles ~5-6k rows without virtualization tricks; insert rows
  in one loop, values formatted %.6g.
- Copy: Ctrl+C on selection -> TSV to clipboard (pastes straight into
  Excel). "Copy all" button in the header.
- "Open in Excel" button: write the trace to a temp CSV (scratch temp
  dir) and `os.startfile(path)` — delegates heavy analysis to actual
  Excel, which is the optimal UX answer to "excel style without
  cluttering the program".
- Comparison across tabs is served by: switch tab -> drawer follows the
  active tab. (A future "pin trace for diff" is noted, not built.)
- Theme: Treeview is ttk and follows sv_ttk; the drawer frame is plain.

## 5. Recent runs + drag-and-drop (approved item 6)

Blank state upgrade: when a tab has no results, place() a small tk.Frame
over the canvas (centered, palette-colored — plain tk, rule 0) showing:
the existing quickstart text (move it from the matplotlib empty-state
text into this frame OR keep mpl text and add the frame below it — take
whichever reads better in the probe) plus up to 5 clickable recent
entries: "folder-name  (19 traces, 0-53.7 GPa, 2d ago)". Clicking loads
that folder via the `_load_previous` path into the ACTIVE tab.

Recents source: extend `_push_recent`-style persistence with a new
settings key "recent_runs": list of {path, n, pmin, pmax, when} appended
by `_finish_run`. Cap 8, dedupe by path. Destroy/hide the placed frame
whenever results become non-empty (hook `_finish_run` and `_redraw`'s
empty branch).

Drag-and-drop: OPTIONAL dependency `tkinterdnd2` (pip). Wrap in
try/except ImportError; if absent, feature silently missing (log one
line). Register the root as a DND target for folders; on drop:
- if folder contains *_absorbance.csv -> `_load_previous` flow into the
  active tab;
- else if it contains vis_* raw files -> set in_var, focus the Run
  button, log "folder looks raw: press Run";
- else messagebox "nothing loadable here".
PyInstaller build later needs the tkinterdnd2 hook — note it in the spec
when release time comes; do not touch the spec now.

## 6. Slim toolbar + log readability (approved item 7)

- KEEP the NavigationToolbar2Tk INSTANCE (code checks
  `self.nav_toolbar.mode` in `_toolbar_active()`, grep it) but hide its
  widget (`pack_forget`). Build a slim ttk strip: Home (calls
  `_reset_view`), Pan toggle (`self.nav_toolbar.pan()`), Zoom toggle
  (`self.nav_toolbar.zoom()`), Save (`_save_plot`), and keep the cursor
  readout label. Toggle buttons must reflect toolbar.mode state (poll on
  click; the mode string is "pan/zoom" or "zoom rect").
- The app already has drag-zoom / wheel-zoom / right-click reset, so this
  strip is deliberately minimal.
- Log Text: Consolas 8 -> 9.5 (grep `("Consolas", 8)` — TWO uses: log and
  guide/notes; bump both), and add ipady/padding for breathing room.

## 7. Provenance sidecars (approved item 3)

New helper in app.py (or engine.py if you prefer — engine is unit-tested,
put the pure part there):

```python
def write_provenance(target_path, kind, payload):  # engine.py, pure
    """Write <target>.provenance.json next to an export."""
```
Payload always includes: tool version (APP_VERSION), timestamp ISO,
kind ("absorbance_csv" | "notch_csv" | "smoothed_csv" | "dlist" |
"plot" | "batch_png"), source folder, and kind-specific params:
- notch: width_frac, nt band, pvalue_max, per-file detected nt/pvalue
- smoothed: full smooth_params dict
- plot/batch: figure size, dpi, preset name, colormap, mode
- all CSV kinds: list of {filename, sha1} of the files written
Call sites (grep each): engine.run's write loop (per-run one sidecar for
the whole folder, not per CSV — one `_reduction.provenance.json`),
`write_notch_csv` callers, `_export_smoothed`, `_export_dlist`,
`_save_plot`, `_batch_export`. Add engine tests for the pure writer.

## 8. Audit bug candidates (approved item 8)

Verify each against the code FIRST (they are unverified findings —
verifiers died; do not fix blind). From AUDIT_BACKLOG.md section 1:
1. `_mark_tick_edited`/`_mark_label_edited` fire on any KeyRelease ->
   value-aware guard (only flag when text actually changed).
2. `_style_colorbar` single blanket try/except -> per-field parsing.
3. `_show_readout_table` clamps out-of-range wavelengths to the edge ->
   show "-" beyond a few nm miss.
4. `_draw_inspect` twinx axis unthemed in dark/black -> style ax2 with
   `_mpl_colors`/`_axis_text_colors` (probe in black theme to verify).

## 9. Build order, sizing, and gates

Phase 1 (M): Session core WITHOUT UI. Sessions list, store/load swap,
  hardwired second session in a structural test. GATE: new
  tests/test_sessions.py — create A (load real Y04_Arch29 reduced folder
  from Manuscript_2026/reduced), snapshot, new blank B, load different
  folder, switch A<->B, assert: results identity, a changed registry var
  (e.g. colormap) round-trips per tab, trace toggle round-trips, undo
  stacks isolated. Headless-skippable like test_legend.py.
Phase 2 (M): Tab strip UI + rename/close/+ + run-blocking. Probe: light
  AND black screenshots, 3 tabs, one renamed.
Phase 3 (S): Wire per-tab folders, chip, status bar tab name, NUKE-all.
Phase 4 (M): Data drawer. Probe with data; test copy-TSV string format.
Phase 5 (S-M): Recents + DnD (optional dep).
Phase 6 (S): Toolbar + log.
Phase 7 (S): Provenance + engine tests.
Phase 8 (S-M): Audit four-pack.
Bump APP_VERSION to v1.4.0-dev at Phase 1; Nhan decides release timing.
Each phase ends with the full validation loop and a sha-guarded swap;
update the project memory sha line every swap.

## 10. Key greps (verified to exist at cbaeb972d08d; positions WILL move)

- `_preset_registry`, `_snapshot`, `_push_undo`, `self._restoring`
- preset apply path: grep `def _apply_snapshot` or the preset Load command
- `_finish_run`, `_build_trace_checks`, `_load_previous`, `_run`
- `_theme_palette`, `_recolor_tk`, `_content_labels`, `_lbl`
- `_toolbar_active`, `nav_toolbar`, `_typing_in_box`
- `("Consolas", 8)` (two), `_push_recent`, `_defaults`
- middle pane build: grep `Plot Area` and `FigureCanvasTkAgg`
