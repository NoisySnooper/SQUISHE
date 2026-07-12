# SQUISHE design system  (Direction C "Bauhaus Lab", matured)
# Approved by Nhan 2026-07-12. Name = SQUISHE for now; the name lives in ONE
# place (BRAND dict, app.py top) so a rename is a two-line change.
# Rule zero: every theme keeps working; the plot DATA colors stay scientific
# (batlow & friends); NUKE stays red everywhere.

## 1. Brand core
- Name: SQUISHE  = Spectral QUick-look for In-Situ High-pressure Experiments
- Wordmark: lowercase "squishe" + accent-colored period: squishe.
  (period color = triad ac2 of the active theme)
- Voice: workshop, not laboratory brochure. Blunt labels, no filler.
- Mark (app icon + About): two black anvil bars pressing an accent-colored
  sample dot, on the theme paper. Geometry only, no gradients.
- BRAND dict in app.py: name, wordmark, dot, expansion, subtitle, org.
  APP_TITLE and About derive from it. Rename = edit BRAND only.

## 2. Typography
- Face: Jost (OFL; the modern Futura homage). Shipped in fonts/ as static
  weights instanced from the Google Fonts variable file:
  Jost-Regular(400) / Jost-Medium(500) / Jost-SemiBold(600) / Jost-Bold(700)
  + OFL.txt license. ~61 KB each.
- Loading: AddFontResourceExW(..., FR_PRIVATE) at startup (no install,
  vanishes with the process); matplotlib registration via
  font_manager.addfont. PROVEN 2026-07-12: Tk resolves "Jost",
  "Jost Medium", "Jost SemiBold"; mpl findfont returns Jost-Regular.ttf.
- Fallback chain when fonts/ is missing: Century Gothic -> Bahnschrift ->
  Segoe UI (module global UI_FONT resolves once at startup).
- Scale (family UI_FONT unless noted):
    wordmark 15 SemiBold lowercase     section title 10 Bold
    body/controls 9 Regular            hints 8 Regular #888-ish
    tab active 9 Bold / inactive 9     log & data table stay Consolas 9
- Named fonts TkDefaultFont/TkTextFont/TkMenuFont/TkHeadingFont get
  family=UI_FONT so every ttk widget follows without touching sv_ttk.
- Plot default font.family = UI_FONT (journal presets still override with
  publisher requirements; that always wins).

## 3. Color: the triad system
Each theme gets THREE brand accents + one structural ink. Semantic colors
(success green, error red, NUKE red) are separate and never restyled.
Roles:
  ac1 PRIMARY   Run button, Data table button, dialog CTAs, active-tab
                underline, links/focus
  ac2 SIGNAL    wordmark dot, section carets + geo markers, drag accents
  ac3 HIGHLIGHT preset star, quick-access title, small highlights
  ink STRUCTURE 2px section rules, strong borders (light themes: near-black
                ink; dark themes: near-paper light)

  theme      base   ac1        ac2        ac3        ink        notes
  light      light  #1D3EC0    #E4581C    #B98A0E    #171310    bg -> paper #F1EEE6
  dark       dark   #5B78F2    #FF7A45    #EDBB4A    #E8E4DA
  black      dark   #6C86FF    #FF8149    #F2BE3A    #EFEBE0    true black stays
  forest     light  #2E7D32    #E4581C    #C9A227    #1B2E1B    keeps its green
  rose       light  #C2185B    #8A63D2    #DDA523    #3A1B2A    rose/violet/gold
  ocean      dark   #26C6DA    #F2984B    #7FD1AE    #DBEEFB    teal/coral/foam
  solarized  light  #268BD2    #CB4B16    #B58900    #586E75    native solarized triad
  rainbow    dark   #C64FD0    #E4581C    #E3A917    #ECECEF    prism banner stays

- The old single-accent hooks (_raw_banner_accent, _recolor_accents) are
  redirected through the triad (_brand()): ac1 feeds the old accent role.
- Light theme becomes the Bauhaus paper: bg #F1EEE6, fields stay white,
  plot page stays white (publication neutrality).

## 4. Components
- Header: [anvil mark 18px] squishe. |  subtitle 8 caps  |  version chip ->
  About. NUKE unchanged. Wordmark dot + mark dot recolor with theme.
- Section headers (_group): caret in ac2, title 10 Bold ink, then a 2px ink
  rule (replaces the 1px ttk separator). Phase 2 adds an 8px filled square
  marker in ac2 before the title.
- Tabs: active = Bold + 3px ac1 underline; inactive muted; close x
  hover-only (already shipped); "+" unchanged.
- Primary buttons: Run / Data table / "Use this profile" become brand
  buttons: flat tk.Button, bg ac1, white text, hover = 12% darker,
  disabled = muted. (sv_ttk's image-drawn Accent blue can't be recolored;
  we own these three instead. Other buttons stay sv_ttk.)
- Chips/toasts: status chip keeps semantic colors; toast stays ink-on-dark;
  preset star + quick-access title tint ac3.
- Known compromise: sv_ttk native glyphs (toggle switch, checkboxes,
  scrollbars) keep the Fluent look and its blue. Full recolor would need a
  forked theme; parked (AUDIT_BACKLOG).

## 5. Iconography (Phase 2)
- Language: filled geometric shapes, 2.2px flat-cap strokes, one accent
  fill max per icon. Run = ac2 triangle; folder, save, table, camera,
  orbit = ink strokes. Drawn programmatically with PIL at 2x, cached per
  theme, recolored on theme switch.
- App icon: redraw icon.png/ico as the anvil mark on paper roundrect
  (light) with ac2 dot; keep batlow strip nod on the About card only.

## 6. Plot identity (Phase 3)
- Figure CHROME follows the theme (bg, spines, fonts = UI_FONT); DATA
  colors remain the scientific colormaps untouched.
- "squishe style": thin spines, ticks in, 2px ink axis on Bauhaus light.
- Journal presets always override everything for publication.

## 7. Motion
- One-shot only, honoring reduce_motion: active-tab underline slides in;
  toasts fade (already). Nothing loops.

## 8. Phases
- P1 DONE 2026-07-12 (sha ad8d21d9fa45): BRAND dict + font pipeline +
  named fonts + UI_FONT swap + triads wired + paper light + brand buttons
  (Run / Data table / Use profile) + wordmark header.
- P2 DONE 2026-07-12 (sha 8d319fe184f7): _make_icons() PIL set (theme-
  tinted, regenerated in _apply_brand) on Run/Data/Load-prev/toolbar/
  Name-format(gear, neutral bold style now)/header mark; Pan-Zoom-Save
  icon-only squares; section ac2 square markers + muted carets;
  quick-access title ac3; About card (mark_lg + wordmark + expansion +
  batlow strip); icon.png/.ico redrawn as the anvil mark.
- P3 DONE 2026-07-12: plot identity satisfied by Jost + theme-following
  chrome (no extra rc changes, journal presets untouched); fonts/ + new
  icons shipped into the signed-runtime package (selftested, Defender
  clean, re-zipped 96.7 MB) and added to beamline_tool.spec datas;
  rename dry-run PASSED (two-line BRAND edit rebrands title, wordmark,
  About).
- DEEP PASS B1-B4 DONE 2026-07-12 (sha 8323113a277e) after review:
  B1 button metrics normalized; scroll pages painted chrome (collapsed
  void fixed); left-panel + panel-bar buttons iconized; Windows CAPTION
  painted per theme via DWM (re-applied on <Map>; dialogs too).
  B2 Fluent-blue EVICTED: sv_ttk sprite pixels hue-remapped to ac1 per
  theme (cached originals, 0.13s/switch) — checks, radios, switches,
  sliders, focus all follow the triad now.
  B3 every section carries its own 12px geometric glyph in ac2.
  B4 left LabelFrame headers use the marker language; recents links and
  the drawer title wear ac3.
- Remaining polish candidates: tab underline motion, chips audit.
