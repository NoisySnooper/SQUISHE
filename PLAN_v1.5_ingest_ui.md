# PLAN v1.5 -- flexible filename ingestion + 2D view controls + polish
# Baseline: app.py sha256[:12] = fa89cfac310f, v1.4.0-dev, 33 tests green.
# Written 2026-07-11. Execute phase by phase, each with the full validation
# gate. RE-VERIFY the sha before every batch; if moved, re-baseline.
#
# HARD RULES (same as v1.4 plan):
#  - sha-guarded loop: cp app.py app_dev.py -> edit app_dev -> selftest ->
#    structural one-shot -> topmost screenshot probe -> guarded swap -> pytest.
#  - NEVER auto-launch the app for the user (run.bat is theirs). Probes use
#    root.attributes("-topmost", True) ALWAYS.
#  - sv_ttk Card/LabelFrame interiors are image-drawn and ignore configure
#    backgrounds; use plain ttk.Frame; diagnose colors with PIL pixel probes.
#  - Non-ASCII in source: write literal, then bytes-normalize to \uXXXX.
#  - No commits without explicit permission from Nhan.
#
# PROGRESS: (update as phases land)
#   [x] Phase A  DONE 2026-07-11 (sha 8bd12df4b8d6): z-clip auto = full
#       range + "Clip Z spikes (99th pct)" checkbox (wf3d_clip99, registry,
#       one-time log note); colormap gap = always-packed empty fallback_lbl,
#       now pack/pack_forget on text; wheel no longer changes ANY control
#       (slider bind deleted; class guard TCombobox/TSpinbox/Spinbox/TScale/
#       Scale -> _on_wheel + "break"); guide font family+size row (persisted
#       guide_font_family/guide_font_size, _apply_guide_font).
#   [x] Phase B  DONE 2026-07-11 (sha ed1f5b89260b): legend Swatch option
#       (legend_swatch default "color box", _legend_handles patches at both
#       call sites, registry); View (2D) group (pan pad hold-to-repeat via
#       _bind_repeat, _pan2d/_zoom2d/_fit2d through _capture_zoom_limits,
#       zoom +/- with X/Y/both radio, Fit X/Y, arrows/+ - /0 hotkeys guarded
#       by _typing_in_box which now also covers Scale/Treeview/Listbox).
#       BONUS BUG FIX: the v1.4 "Axis" rename never updated _tabspec /
#       _reorder_sections, so the Axis group had silently fallen to the
#       Plot tab fallback; both lists fixed ("Axis" on Axes tab).
#   [x] Phase C  DONE 2026-07-11 (sha 0e051a3c5b1e, 42 tests): engine
#       naming profiles (BUILTIN_PROFILE sentinel -> classic parser
#       untouched; default_profile/validate_profile/parse_with_profile/
#       apply_override; scan_folder+run take profile+overrides;
#       tests/test_profiles.py 9 cases). App: "Name format:" button under
#       the input folder; teach-by-example dialog (profile dropdown +
#       Save as/Delete, grammar row, token chips from a real example, live
#       whole-folder preview green/red/blue, Fix selected per-file override
#       grid incl. resurrect+exclude, Use this profile). Settings:
#       name_profiles list, active_profile, name_overrides per folder key.
#       Run passes profile+overrides (read on main thread); skip hint in
#       _finish_run; PANEL_GUIDE "NAME FORMAT" section.
#       Gotcha: seq_sep="." + pressure decimal "." is ambiguous for names
#       like ...-2.5 with no segment (documented in engine comment; the
#       live preview exposes it).
#   [x] Phase D  DONE 2026-07-11 (sha f66719e80e3f, 42 tests):
#       D1 window title = active tab name (_update_title from _render_tabs;
#          generic "Session N" names keep APP_TITLE).
#       D2 header logo (subsampled _app_icon next to the title) + version
#          chip is clickable -> About.
#       D3 typography: section headers 10pt bold (caret matched).
#       D4 _toast() fading chip; hooked: Copy log / Copy figure / drawer
#          Copy TSV / Save plot.
#       D5 Find box gray placeholder "Find a setting…" (_ph_active guard in
#          _filter_sections; Esc clears).
#       D6 tabs: close x hover-only on inactive tabs (fg=bg trick, no width
#          jitter), names ellipsized >18 chars, padding compresses >6 tabs,
#          full-name tooltip. (Overflow arrows dropped for ellipsize+
#          compress: simpler, no canvas rearchitecture.)
#       D7 About dialog: icon + version + credits header + GitHub button.
#       D8 empty state: bold headline + steps + dim hints, spaced.
#       D9 plot font default DejaVu Sans -> Segoe UI (journal presets win).
#       D10 NUKE untouched.
#   [ ] Phase E  optional: defringed cols in primary CSV (Kanani ask)

## Phase A -- quick fixes
A1. 3D z-clip cuts data (user complaint: "most of the time").
    Cause: _wf3d auto Zmax = np.nanpercentile(allz, 99)  (app.py ~L5247).
    Fix: auto = true nanmax. Add "Clip Z spikes (99th pct)" checkbox in
    3D plot options, default OFF, in _preset_registry. When ON and it
    actually clips, log one line ("Z clipped at ...; N points above").
    Typed Z max still overrides everything. Update PANEL_GUIDE Z clip text.
A2. Colors & colormap box has a dead gap at the bottom.
    Diagnose with a PIL pixel probe + winfo_children walk of the group
    frame (suspects: empty fallback_lbl kept packed, a stray pady, or the
    collapsible body min-height). Remove the gap. Screenshot before/after.
A3. Mouse wheel changes control values; user wants it GONE everywhere.
    - Delete the per-slider nudge bind (sc.bind("<MouseWheel>", wheel),
      ~L3136) and the "Scroll to nudge" wording in its tooltip.
    - Neutralize ttk class defaults so wheel over a control scrolls the
      panel instead: for cls in ("TCombobox", "TSpinbox"):
      root.bind_class(cls, "<MouseWheel>", lambda e: (self._on_wheel(e),
      "break")[-1]).  _on_wheel resolves the canvas from cursor coords, so
      forwarding works unchanged.
    - Keep native wheel: plot canvas zoom, Text widgets (log/guide/notes),
      data-drawer Treeview, and the OPEN combobox dropdown list (that is a
      popdown Listbox, class binding untouched).
    - Structural test: wheel event over a combobox does not change its
      value (fire <MouseWheel> synthetically, assert var unchanged).
A4. Guide / notes font controls.
    Header row of Guide/notes gets: font family combobox (Consolas,
    Segoe UI, Arial, Calibri, Courier New) + size spinner 8..16.
    Applies to self.ref Text immediately; persisted as
    settings["guide_font_family"] / ["guide_font_size"] (global setting,
    not per-tab). Default stays Consolas 9.

## Phase B -- legend swatches + 2D view controls
B1. 2D legend = thick color boxes (match 3D).
    New "Swatch" combobox in Legend group: "color box" | "line",
    DEFAULT "color box" (user asked for it as the look).
    Implementation: helper _legend_handles(handles) -> when color-box mode,
    map each handle to matplotlib.patches.Patch(facecolor=color,
    edgecolor="none"); color from h.get_color() (Line2D) or
    h.get_facecolor() (collections/patches). Use in BOTH legend call sites
    (2D _legend_or_colorbar ~L4458 and 3D ~L5489) so the option is honored
    everywhere; 3D already looks like boxes so mode "line" there maps back
    to a Line2D of the poly edge color. In _preset_registry. Probe: 19-trace
    legend screenshot, boxes clearly visible at small sizes.
B2. "View (2D)" group on the Plot tab (below 2D plot options).
    3D has camera controls; 2D gets the equivalent. All actions set explicit
    limits (turning Auto off exactly like drag-zoom does), push undo, and
    respect flipped axes. Proposed controls (verify scope with Nhan):
      - Pan pad: 4 arrow buttons around a center "Fit" button (3x3 grid).
        One click nudges 15% of the current span; press-and-hold repeats
        via after(120) loop; Fit = fit both axes to shown data.
      - Zoom row: [-] [====slider====] [+]  zooming about the current
        center; slider 25%..400% of the fitted span (log scale); +/- step
        25%. Applies to the axis picked by a small "X / Y / both" choice,
        default both.
      - "Fit X" and "Fit Y" buttons for one-axis refits.
      - Keyboard (only when not typing in a box, reuse _typing_in_box):
        arrows pan, +/- zoom, 0 = fit both. Document in SHORTCUTS_TEXT + F1.
    Existing Reset view / Reset axes / drag-zoom / wheel-zoom untouched.
    Structural tests: nudge changes xlim by 15% span; zoom slider sets span;
    fit restores data bounds; keyboard guarded while typing.

## Phase C -- MAJOR: flexible filename ingestion
User problem: professor hates being forced into
vis_{DAC}_{Sample}[_{Pressure}][_bg|_s][_C|_D][_2|_3][.{seq}].
Goal: the user dictates how names are understood, with zero-click
back-compat for the current grammar, and a manual escape hatch so ANY
folder can be ingested even with no usable pattern.

Three layers:
C1. engine.py: profile-driven parsing (pure python, unit-testable).
    - NamingProfile = JSON-able dict:
        {"name": str,
         "prefix": "vis" | "" (token 0 requirement; "" = none),
         "sep": "_",
         "order": ["dac","sample","pressure","role","branch","rep"],
           (any subset, any order; missing field -> default),
         "pressure_decimal": "p" | "." | "," ,
         "pressure_unit_strip": ["gpa","kbar"]  (case-insensitive strip),
         "role_map": {"bg":"background","s":"sample","":"dark"}
           (editable keywords; "" = no role token present),
         "branch_tokens": {"c":"C","d":"D"},
         "seq_sep": "." ,   "seq_missing": 1,
         "defaults": {"pressure": 0.0, "role": "dark", "rep": 1}}
    - parse_with_profile(fname, profile) -> same record dict as
      parse_segment_filename (dac, sample, pressure_*, meas, rep, branch,
      seq, pdefault, raw | skip+reason). The builtin grammar becomes
      BUILTIN_PROFILE and parse_segment_filename delegates to it (behavior
      byte-identical; existing tests must stay green).
    - scan_folder(in_dir, profile=None, overrides=None): parse via profile,
      then apply overrides (dict raw_filename -> field patches from the
      review table) BEFORE grouping. Grouping logic unchanged.
    - Tests: ~10 pure pytest cases (reordered tokens, dash separator,
      "12.5GPa" pressure, role keyword remap, no-role folders, overrides,
      builtin equivalence on the June2026 real names).
C2. app.py: "Name format..." dialog (button on the left panel, next to the
    Input folder row). Teach-by-example builder:
    - Top: profile dropdown [22-IR-1 default (built-in), ...saved] +
      Save as... / Delete. Active profile stored in settings
      ("name_profiles" list + "active_profile"; per-session override
      travels with the tab like folders do).
    - Middle: an example filename from the current input folder (combobox
      of actual files). It is split live by the separator; each token gets
      a dropdown underneath: DAC / Sample / Pressure / Role / Branch /
      Replicate / Ignore. Below: separator entry, pressure decimal char,
      role keyword table (3 entries: background=, sample=, dark=),
      "missing pressure -> 0 GPa" and "no role token -> dark" checks.
    - Bottom: live match preview against the WHOLE folder: Treeview
      file -> DAC | sample | P(GPa) | role | branch | rep | seg, green rows
      = parsed, red rows = skipped with reason, header "matched 142/150".
      Nothing is written until Run.
C3. Manual override grid (escape hatch, same dialog, "Fix files" tab):
    per-file editable cells (role dropdown, pressure entry, group fields)
    for stubborn files; stored as overrides and passed to scan_folder.
    Guarantees ingestion of any naming scheme, worst case by hand.
    Run flow: Run uses active profile + overrides; skipped files are listed
    in the log with a "Name format..." hint. PANEL_GUIDE + QUICK_START and
    tooltips updated; provenance sidecar records profile name + overrides.

## Phase D -- UI/UX + aesthetic batch (each small; Nhan strikes what he
##            does not want)
D1. Window title = active tab name ("Y04_Arch29 - Beamline DAC Data Tool").
D2. App icon (16-20 px PNG) inline at the left of the header title text.
D3. Typography normalization: one scale (title 13 bold / section 10 bold /
    body 9 / hint 8 gray) applied everywhere; label column widths unified
    per group so entries align in a column.
D4. Toast feedback: small fading "Copied" / "Saved" chip near the button
    for Copy figure, Copy log, Copy TSV, Save plot (instead of log-only).
D5. Find box placeholder text "Find a setting..." + Esc clears it.
D6. Tab strip polish: close x only on hover/active tab, + button tooltip,
    left/right overflow arrows past ~8 tabs.
D7. About dialog: icon + version + short credits (Nhan, M. Diamond
    defringe) + repo link; version chip in header opens it.
D8. Empty-state (blank tab) card: icon + 3 shortcuts (Browse, Load
    previous, recents list already there) with consistent spacing.
D9. Plot defaults touch-up: default figure font = Segoe UI to match the
    app when no journal preset is active (journal presets still win).
D10. NUKE stays exactly as is (Nhan wants the joke prominent).

## Phase E -- optional (pending Kanani): append defringed columns to the
##            primary per-point CSV instead of the separate *_notch.csv.
##            Small engine/export change + provenance + 2 tests.

## Validation gate (every phase)
selftest OK -> structural one-shot (assert new behavior) -> pytest (33+new)
-> topmost screenshot probe(s) incl. black + light themes for UI phases ->
sha-guarded swap -> update this header + memory sha.
