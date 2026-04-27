"""Lifted CSS for the dashboard.

This is the verbatim contents of dashboard/handoff/styles.css from the
approved redesign. DO NOT modify without re-validating against the
React prototype — the design tokens, density vars, accent variants,
and chip classes are all referenced by the templates and must stay
in sync. To update, replace the CSS string between the triple-quotes.
"""

CSS = r"""
/* Bull-Bot Dashboard — quant-terminal aesthetic
 * Tone: dense, professional, calm. Inspired by Bloomberg / OpenBB / serious desks.
 * - Near-black neutral with warm-cool tonal contrast (not navy-on-navy)
 * - Type: IBM Plex Sans for chrome, IBM Plex Mono for tabular numerals
 * - Accent: a sober "bull green" (#5fbf7a-ish in oklch) + a muted red for losses
 */

:root {
  /* Surfaces — neutral cool grays, low chroma */
  --bg-0: oklch(15% 0.005 250);   /* page */
  --bg-1: oklch(18% 0.006 250);   /* card */
  --bg-2: oklch(22% 0.007 250);   /* hover / nested */
  --bg-3: oklch(26% 0.008 250);   /* table row hover */
  --line: oklch(28% 0.008 250);   /* hairline */
  --line-strong: oklch(34% 0.01 250);

  /* Type */
  --fg-0: oklch(96% 0.005 250);   /* primary */
  --fg-1: oklch(78% 0.006 250);   /* secondary */
  --fg-2: oklch(58% 0.008 250);   /* tertiary / labels */
  --fg-3: oklch(45% 0.008 250);   /* disabled */

  /* Semantic */
  --pos: oklch(72% 0.16 145);     /* gains — bull green */
  --pos-soft: oklch(72% 0.16 145 / 0.14);
  --neg: oklch(64% 0.18 25);      /* losses — sober red */
  --neg-soft: oklch(64% 0.18 25 / 0.14);
  --warn: oklch(78% 0.13 75);     /* amber */
  --warn-soft: oklch(78% 0.13 75 / 0.14);
  --info: oklch(72% 0.10 230);    /* steel blue */
  --info-soft: oklch(72% 0.10 230 / 0.14);
  --accent: var(--pos);

  /* Phase chips */
  --phase-live: oklch(72% 0.10 230);
  --phase-paper: oklch(72% 0.16 145);
  --phase-discovering: oklch(78% 0.13 75);
  --phase-no-edge: oklch(58% 0.04 250);

  /* Density */
  --row-h: 32px;
  --pad-x: 14px;
  --pad-y: 10px;
  --radius: 4px;

  /* Type sizes */
  --t-xs: 10.5px;
  --t-sm: 12px;
  --t-md: 13px;
  --t-lg: 15px;
  --t-xl: 22px;
  --t-2xl: 30px;
}

/* Density modes (driven by tweaks) */
[data-density="comfortable"] {
  --row-h: 38px;
  --pad-x: 18px;
  --pad-y: 14px;
}
[data-density="compact"] {
  --row-h: 26px;
  --pad-x: 10px;
  --pad-y: 6px;
  --t-md: 12.5px;
}

/* Accent variants */
[data-accent="amber"]  { --accent: oklch(78% 0.13 75); }
[data-accent="cyan"]   { --accent: oklch(78% 0.10 210); }
[data-accent="violet"] { --accent: oklch(72% 0.13 295); }
[data-accent="green"]  { --accent: oklch(72% 0.16 145); }

/* Light mode (rare for quant tools, but offered as a tweak) */
[data-theme="light"] {
  --bg-0: oklch(98% 0.003 250);
  --bg-1: oklch(100% 0 0);
  --bg-2: oklch(96% 0.004 250);
  --bg-3: oklch(94% 0.005 250);
  --line: oklch(90% 0.005 250);
  --line-strong: oklch(82% 0.006 250);
  --fg-0: oklch(20% 0.01 250);
  --fg-1: oklch(38% 0.01 250);
  --fg-2: oklch(52% 0.012 250);
  --fg-3: oklch(68% 0.01 250);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }

body {
  background: var(--bg-0);
  color: var(--fg-0);
  font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
  font-size: var(--t-md);
  font-feature-settings: "ss01", "cv11";
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  min-height: 100vh;
}

.mono, .num {
  font-family: 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  font-feature-settings: "tnum", "zero";
  font-variant-numeric: tabular-nums;
}

.num.pos { color: var(--pos); }
.num.neg { color: var(--neg); }
.num.muted { color: var(--fg-2); }

/* ============ Header ============ */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 22px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  z-index: 50;
  backdrop-filter: blur(6px);
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--bg-0);
  border: 1px solid var(--line-strong);
  display: grid;
  place-items: center;
  position: relative;
  overflow: hidden;
}
.brand-mark::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(135deg, transparent 50%, var(--accent) 50%) right top / 8px 8px no-repeat,
    linear-gradient(45deg, transparent 50%, var(--accent) 50%) left bottom / 8px 8px no-repeat;
  opacity: 0.85;
}
.brand-mark::after {
  content: "B";
  font-family: 'IBM Plex Mono', monospace;
  font-weight: 600;
  font-size: 14px;
  color: var(--fg-0);
  position: relative;
  z-index: 1;
}
.brand-name {
  font-weight: 600;
  letter-spacing: 0.01em;
  font-size: 14px;
}
.brand-sub {
  color: var(--fg-2);
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  margin-left: 6px;
}
.header-meta {
  display: flex;
  align-items: center;
  gap: 18px;
  font-size: var(--t-sm);
  color: var(--fg-1);
}
.header-meta .item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--pos);
  box-shadow: 0 0 0 3px color-mix(in oklab, var(--pos) 18%, transparent);
}
.dot.warn { background: var(--warn); box-shadow: 0 0 0 3px color-mix(in oklab, var(--warn) 18%, transparent); }
.dot.neg { background: var(--neg); box-shadow: 0 0 0 3px color-mix(in oklab, var(--neg) 18%, transparent); }

/* Layout */
.layout {
  display: grid;
  grid-template-columns: 220px 1fr;
  min-height: calc(100vh - 53px);
}
.sidebar {
  background: var(--bg-1);
  border-right: 1px solid var(--line);
  padding: 14px 8px;
}
.nav-group {
  padding: 8px 12px 4px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-3);
  font-weight: 600;
}
.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 12px;
  border-radius: var(--radius);
  color: var(--fg-1);
  cursor: pointer;
  font-size: var(--t-md);
  user-select: none;
  border-left: 2px solid transparent;
  margin: 1px 0;
}
.nav-item:hover { background: var(--bg-2); color: var(--fg-0); }
.nav-item.active {
  background: var(--bg-2);
  color: var(--fg-0);
  border-left-color: var(--accent);
}
.nav-item .badge {
  background: var(--bg-3);
  color: var(--fg-1);
  border-radius: 10px;
  padding: 1px 7px;
  font-size: 10.5px;
  font-family: 'IBM Plex Mono', monospace;
}
.nav-item.active .badge { background: var(--bg-0); color: var(--fg-0); }
.nav-divider { height: 1px; background: var(--line); margin: 12px 8px; }

/* Main */
main {
  padding: 22px 26px 80px;
  max-width: 1480px;
}

.page-title-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.01em;
}
.page-sub { color: var(--fg-2); font-size: var(--t-sm); }

/* ============ KPI strip ============ */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  margin-bottom: 18px;
}
.kpi {
  background: var(--bg-1);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
}
.kpi .label {
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-2);
  font-weight: 500;
}
.kpi .value {
  font-family: 'IBM Plex Mono', monospace;
  font-size: var(--t-2xl);
  font-weight: 500;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
  line-height: 1.05;
}
.kpi .sub {
  font-size: 11.5px;
  color: var(--fg-2);
  display: flex;
  align-items: center;
  gap: 6px;
}
.kpi .sub .delta { font-family: 'IBM Plex Mono', monospace; }
.kpi .spark {
  position: absolute;
  right: 10px;
  bottom: 10px;
  opacity: 0.85;
}

/* ============ Cards ============ */
.card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  margin-bottom: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
.card-title {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fg-1);
}
.card-body { padding: 14px; }
.card-body.flush { padding: 0; }

/* Two-column layout */
.cols-2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; }
.cols-2-eq { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 1100px) {
  .cols-2, .cols-2-eq { grid-template-columns: 1fr; }
}

/* ============ Tables ============ */
table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--t-md);
}
thead th {
  text-align: left;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fg-2);
  font-weight: 600;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--bg-1);
  position: sticky;
  top: 0;
  white-space: nowrap;
}
tbody td {
  padding: var(--pad-y) var(--pad-x);
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
  height: var(--row-h);
}
tbody tr { transition: background 0.08s ease; }
tbody tr:hover { background: var(--bg-2); }
tbody tr.clickable { cursor: pointer; }
.t-right { text-align: right; }
.t-center { text-align: center; }

/* ============ Chips / badges ============ */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  font-family: 'IBM Plex Mono', monospace;
  border: 1px solid transparent;
}
.chip.live      { background: color-mix(in oklab, var(--phase-live) 14%, transparent); color: var(--phase-live); border-color: color-mix(in oklab, var(--phase-live) 30%, transparent); }
.chip.paper     { background: color-mix(in oklab, var(--phase-paper) 14%, transparent); color: var(--phase-paper); border-color: color-mix(in oklab, var(--phase-paper) 30%, transparent); }
.chip.discovering { background: color-mix(in oklab, var(--phase-discovering) 14%, transparent); color: var(--phase-discovering); border-color: color-mix(in oklab, var(--phase-discovering) 30%, transparent); }
.chip.no_edge   { background: color-mix(in oklab, var(--phase-no-edge) 18%, transparent); color: var(--fg-1); border-color: var(--line-strong); }
.chip.pass      { background: var(--pos-soft); color: var(--pos); border-color: color-mix(in oklab, var(--pos) 30%, transparent); }
.chip.fail      { background: var(--neg-soft); color: var(--neg); border-color: color-mix(in oklab, var(--neg) 30%, transparent); }
.chip.warn      { background: var(--warn-soft); color: var(--warn); border-color: color-mix(in oklab, var(--warn) 30%, transparent); }
.chip.open      { background: var(--info-soft); color: var(--info); border-color: color-mix(in oklab, var(--info) 30%, transparent); }
.chip.closed    { background: var(--bg-3); color: var(--fg-1); border-color: var(--line-strong); }

.tag {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9.5px;
  padding: 1px 5px;
  border-radius: 2px;
  border: 1px solid var(--line-strong);
  color: var(--fg-2);
  text-transform: lowercase;
  letter-spacing: 0.04em;
}
.tag.growth { color: oklch(78% 0.13 295); border-color: color-mix(in oklab, oklch(78% 0.13 295) 28%, transparent); }
.tag.income { color: var(--info); border-color: color-mix(in oklab, var(--info) 28%, transparent); }

/* ============ Buttons / segmented ============ */
.btn-row { display: flex; gap: 6px; align-items: center; }
.btn {
  background: var(--bg-2);
  border: 1px solid var(--line);
  color: var(--fg-1);
  padding: 5px 10px;
  border-radius: var(--radius);
  font-size: 11.5px;
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.btn:hover { background: var(--bg-3); color: var(--fg-0); }
.btn.primary { background: var(--accent); color: var(--bg-0); border-color: var(--accent); font-weight: 600; }
.btn.primary:hover { filter: brightness(1.06); }

.segmented {
  display: inline-flex;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 2px;
  gap: 2px;
}
.segmented button {
  background: transparent;
  border: none;
  color: var(--fg-1);
  padding: 4px 10px;
  font-size: 11.5px;
  border-radius: 3px;
  cursor: pointer;
  font-family: inherit;
}
.segmented button.active {
  background: var(--bg-0);
  color: var(--fg-0);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}
.segmented button:hover:not(.active) { color: var(--fg-0); }

/* ============ Position cards ============ */
.position-card {
  border: 1px solid var(--line);
  background: var(--bg-1);
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 10px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 18px;
  position: relative;
}
.position-card::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--info);
  border-radius: 2px 0 0 2px;
}
.position-card.pos::before { background: var(--pos); }
.position-card.neg::before { background: var(--neg); }
.position-card.closed { opacity: 0.78; }
.position-card.closed::before { background: var(--fg-3); }

.pos-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pos-ticker { font-weight: 700; font-size: 15px; letter-spacing: 0.02em; }
.pos-strat  { color: var(--fg-1); font-size: 12.5px; }
.pos-meta   { color: var(--fg-2); font-size: 11.5px; margin-top: 2px; }
.pos-rationale {
  border-left: 2px solid var(--line-strong);
  padding: 4px 0 4px 10px;
  margin-top: 8px;
  color: var(--fg-1);
  font-size: 12px;
  font-style: italic;
  grid-column: 1 / -1;
}
.pos-legs {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11.5px;
  color: var(--fg-1);
  margin-top: 6px;
  grid-column: 1 / -1;
}
.pos-pnl {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 17px;
  text-align: right;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.pos-pnl-pct { font-size: 11px; color: var(--fg-2); }

/* Progress bar (profit target) */
.progress {
  width: 100%;
  height: 4px;
  background: var(--bg-3);
  border-radius: 2px;
  overflow: hidden;
  margin-top: 8px;
  grid-column: 1 / -1;
}
.progress > div {
  height: 100%;
  background: var(--accent);
  transition: width 0.3s ease;
}
.progress.neg > div { background: var(--neg); }

/* ============ Charts ============ */
.equity-chart { width: 100%; height: 200px; display: block; }
.spark { display: block; }

/* ============ Activity feed ============ */
.activity-list { display: flex; flex-direction: column; }
.activity-item {
  display: grid;
  grid-template-columns: 60px 70px 1fr;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--line);
  align-items: baseline;
  font-size: 12.5px;
}
.activity-item:last-child { border-bottom: none; }
.activity-item .time { font-family: 'IBM Plex Mono', monospace; color: var(--fg-2); font-size: 11px; }
.activity-item .ticker { font-family: 'IBM Plex Mono', monospace; font-weight: 600; }
.activity-item .text { color: var(--fg-1); }
.activity-item .icon {
  width: 16px; height: 16px;
  display: inline-grid; place-items: center;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px;
  border-radius: 2px;
  margin-right: 6px;
}
.activity-item.fill .icon       { color: var(--info); }
.activity-item.exit .icon       { color: var(--fg-2); }
.activity-item.promotion .icon  { color: var(--pos); }
.activity-item.proposal .icon   { color: var(--accent); }
.activity-item.rejection .icon  { color: var(--neg); }
.activity-item.demotion .icon   { color: var(--warn); }

/* ============ Health checks ============ */
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 10px;
}
.health-card {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px 14px;
  position: relative;
  border-left: 3px solid var(--pos);
}
.health-card.warn { border-left-color: var(--warn); }
.health-card.fail { border-left-color: var(--neg); }
.health-card .h-name { font-weight: 600; font-size: 12.5px; margin-bottom: 4px; display: flex; align-items: center; justify-content: space-between; }
.health-card .h-detail { font-size: 11.5px; color: var(--fg-1); line-height: 1.5; }

/* Universe pipeline visualization */
.pipeline {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}
.pipeline-col {
  background: var(--bg-1);
  padding: 10px 12px;
  min-height: 200px;
}
.pipeline-col .col-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fg-2);
  font-weight: 600;
  margin-bottom: 8px;
}
.pipeline-col .count {
  font-family: 'IBM Plex Mono', monospace;
  background: var(--bg-2);
  padding: 1px 7px;
  border-radius: 8px;
  color: var(--fg-1);
  font-size: 10px;
}
.pipeline-tile {
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: 3px;
  padding: 8px 10px;
  margin-bottom: 6px;
  cursor: pointer;
  font-size: 12px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 8px;
}
.pipeline-tile:hover { border-color: var(--line-strong); background: var(--bg-3); }
.pipeline-tile .tile-ticker { font-weight: 700; letter-spacing: 0.02em; }
.pipeline-tile .tile-meta { font-size: 10.5px; color: var(--fg-2); }
.pipeline-tile .tile-pf {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  color: var(--fg-1);
}
.pipeline-tile .tile-bar {
  grid-column: 1 / -1;
  height: 2px;
  background: var(--bg-3);
  border-radius: 1px;
  margin-top: 4px;
  overflow: hidden;
}
.pipeline-tile .tile-bar > div { height: 100%; background: var(--accent); }
.pipeline-tile.pf-fail .tile-bar > div { background: var(--neg); }

/* ============ Cost charts ============ */
.bar-row {
  display: grid;
  grid-template-columns: 60px 1fr 70px;
  gap: 12px;
  align-items: center;
  padding: 5px 0;
  font-size: 12px;
}
.bar-row .bar-track {
  height: 10px;
  background: var(--bg-2);
  border-radius: 2px;
  overflow: hidden;
}
.bar-row .bar-fill {
  height: 100%;
  background: linear-gradient(90deg, color-mix(in oklab, var(--accent) 60%, transparent), var(--accent));
}
.bar-row .bar-amt {
  font-family: 'IBM Plex Mono', monospace;
  text-align: right;
  color: var(--fg-1);
}
.bar-row .bar-label {
  font-family: 'IBM Plex Mono', monospace;
  font-weight: 600;
}

/* ============ Filter bar ============ */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.filter-bar .label-sm {
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fg-2);
}

/* legend dots */
.legend { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--fg-2); }
.legend .sw { width: 8px; height: 8px; border-radius: 2px; }

/* Section subhead */
.subhead {
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fg-2);
  font-weight: 600;
  margin: 18px 0 8px;
}

/* Hide content scroll bars on inner panels but keep page scroll */
.scroll-y { overflow-y: auto; }

/* Tooltips for ticker rows (subtle) */
[title] { cursor: help; }
"""
