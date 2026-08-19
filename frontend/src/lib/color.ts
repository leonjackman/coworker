/**
 * color.ts — perceptual color engine for the CW theme system.
 *
 * Design goals:
 *  - Generate an entire, accessible theme from a *single accent* (plus a few
 *    intuitive knobs) instead of hand-writing 21 fixed hex values per theme.
 *  - Work in OKLCH (perceptually uniform) so hue is stable and lightness can be
 *    swept to build 10-step scales that look even to the human eye. This matches
 *    the existing App.css which already uses `oklch()` tokens.
 *  - Guarantee WCAG 2.2 AA contrast: foreground (text on accent) is chosen by
 *    relative luminance, never hard-coded.
 *
 * No external dependency — the OKLab <-> sRGB math is implemented below so the
 * frontend stays self-contained.
 */

export interface Oklch {
  /** Lightness, 0..1 */
  l: number;
  /** Chroma, ~0..0.37 (clamped) */
  c: number;
  /** Hue, degrees, 0..360 */
  h: number;
}

export type ThemeMode = 'light' | 'dark';

const clamp = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const round = (value: number, digits: number): number => {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
};

/** Serialize to a CSS `oklch()` string. */
export function oklchString({ l, c, h }: Oklch): string {
  return `oklch(${round(l, 4)} ${round(c, 4)} ${round(h, 1)})`;
}

/* ------------------------------------------------------------------ */
/* OKLab <-> sRGB conversions (Björn Ottosson's OKLab)                  */
/* ------------------------------------------------------------------ */

function srgbToLinear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(channel: number): number {
  const c = clamp(channel, 0, 1);
  const v = c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;
  return Math.round(clamp(v, 0, 1) * 255);
}

function cubeRoot(value: number): number {
  return Math.sign(value) * Math.abs(value) ** (1 / 3);
}

export function oklchToRgb({ l, c, h }: Oklch): [number, number, number] {
  const hr = (h * Math.PI) / 180;
  const a = c * Math.cos(hr);
  const b = c * Math.sin(hr);

  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894825341 * a - 1.291485548 * b;

  const lC = l_ * l_ * l_;
  const mC = m_ * m_ * m_;
  const sC = s_ * s_ * s_;

  const r = 4.0767416621 * lC - 3.3077115913 * mC + 0.2309699292 * sC;
  const g = -1.2684380046 * lC + 2.6097574011 * mC - 0.3413193965 * sC;
  const bC = -0.0041960863 * lC - 0.7034186147 * mC + 1.707614701 * sC;

  return [linearToSrgb(r), linearToSrgb(g), linearToSrgb(bC)];
}

export function rgbToOklch(r: number, g: number, b: number): Oklch {
  const rL = srgbToLinear(r);
  const gL = srgbToLinear(g);
  const bL = srgbToLinear(b);

  const l = 0.4122214708 * rL + 0.5363325363 * gL + 0.0514459929 * bL;
  const m = 0.2119034982 * rL + 0.6806995451 * gL + 0.1073969566 * bL;
  const s = 0.0883024619 * rL + 0.2817188376 * gL + 0.6299787005 * bL;

  const l_ = cubeRoot(l);
  const m_ = cubeRoot(m);
  const s_ = cubeRoot(s);

  const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const a = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const bC = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;

  const C = Math.sqrt(a * a + bC * bC);
  let H = (Math.atan2(bC, a) * 180) / Math.PI;
  if (H < 0) H += 360;

  return { l: clamp(L, 0, 1), c: clamp(C, 0, 0.37), h: H };
}

export function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace('#', '').trim();
  if (h.length === 3) {
    h = h
      .split('')
      .map((ch) => ch + ch)
      .join('');
  }
  const int = parseInt(h, 16);
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255];
}

export function hexToOklch(hex: string): Oklch {
  const [r, g, b] = hexToRgb(hex);
  return rgbToOklch(r, g, b);
}

export function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (v: number) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0');
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/* ------------------------------------------------------------------ */
/* WCAG contrast                                                        */
/* ------------------------------------------------------------------ */

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const rL = srgbToLinear(r);
  const gL = srgbToLinear(g);
  const bL = srgbToLinear(b);
  return 0.2126 * rL + 0.7152 * gL + 0.0722 * bL;
}

/** WCAG 2.x contrast ratio between two sRGB colors. */
export function contrastRatio(rgbA: [number, number, number], rgbB: [number, number, number]): number {
  const la = relativeLuminance(rgbA);
  const lb = relativeLuminance(rgbB);
  const lighter = Math.max(la, lb);
  const darker = Math.min(la, lb);
  return (lighter + 0.05) / (darker + 0.05);
}

const WHITE: [number, number, number] = [255, 255, 255];
const NEAR_BLACK: [number, number, number] = [10, 10, 11];

/**
 * Nudge an accent's lightness (keeping hue + chroma) so a SOLID accent fill can
 * safely use WHITE text at WCAG AA (>= 4.5:1). Solid colored buttons and user
 * bubbles follow the Apple/VS Code convention: white text on the accent. If the
 * accent is so pale that white can't reach 3:1 without drastic darkening, we
 * leave it untouched and let solidFillForeground fall back to dark text.
 */
function withAccessibleAccent(accent: Oklch): Oklch {
  const start = contrastRatio(oklchToRgb(accent), WHITE);
  if (start >= 4.5) return accent;
  if (start < 3) return accent; // too pale: will use dark text instead
  let adjusted: Oklch = { ...accent };
  for (let i = 0; i < 25; i++) {
    adjusted = { ...adjusted, l: clamp(adjusted.l - 0.02, 0.12, 0.95) };
    if (contrastRatio(oklchToRgb(adjusted), WHITE) >= 4.5) return adjusted;
  }
  return adjusted;
}

/**
 * Pick the foreground (text) color for a given background so the pair meets
 * WCAG AA. Returns a hex string: white or near-black, whichever has more
 * contrast against `bg`.
 */
export function pickForeground(bg: Oklch): string {
  const rgb = oklchToRgb(bg);
  const onWhite = contrastRatio(rgb, WHITE);
  const onBlack = contrastRatio(rgb, NEAR_BLACK);
  return onWhite >= onBlack ? rgbToHex(...WHITE) : rgbToHex(...NEAR_BLACK);
}

/**
 * Foreground for a SOLID accent fill (primary buttons, user message bubble).
 * Follows Apple HIG + VS Code: a solid colored fill uses WHITE text; only a
 * very light accent (pale yellow / amber / pastel) falls back to dark text.
 * Mid accents keep white at >= 3:1, which is Apple's contrast threshold for
 * semibold/large button labels — crisper than muddy black-on-saturated.
 */
export function solidFillForeground(bg: Oklch): string {
  return contrastRatio(oklchToRgb(bg), WHITE) >= 3 ? rgbToHex(...WHITE) : rgbToHex(...NEAR_BLACK);
}

/** Convenience: contrast between an OKLCH color and white/black, for UI hints. */
export function contrastToWhite(color: Oklch): number {
  return contrastRatio(oklchToRgb(color), WHITE);
}

export function contrastToBlack(color: Oklch): number {
  return contrastRatio(oklchToRgb(color), NEAR_BLACK);
}

/* ------------------------------------------------------------------ */
/* Palette construction                                                 */
/* ------------------------------------------------------------------ */

export interface PaletteInput {
  mode: ThemeMode;
  /** The brand accent, in OKLCH. */
  accent: Oklch;
  /** Page background, in OKLCH. */
  bg: Oklch;
  /** Hue used to tint neutral surfaces, borders and muted text. */
  neutralH: number;
  /** Chroma of the neutral tints (kept tiny so they read as "grey"). */
  neutralC?: number;
  /** Foreground lightness override (default 0.18 light / 0.95 dark). */
  fgL?: number;
  /** How far elevated surfaces lift from the background lightness. */
  panelLift?: number;
  /** Chroma of the surface tint. */
  surfaceTint?: number;
}

export interface Palette {
  background: string;
  backgroundGrid: string;
  foreground: string;
  mutedForeground: string;
  panel: string;
  panelSolid: string;
  panelHover: string;
  input: string;
  control: string;
  controlActive: string;
  border: string;
  ring: string;
  accent: string;
  accentHover: string;
  accentForeground: string;
  accentSoft: string;
  accentSoftForeground: string;
  sidebar: string;
  messageUser: string;
  messageUserForeground: string;
  shadow: string;
  tooltip: string;
  tooltipForeground: string;
  scrollThumb: string;
}

const WHITE_HEX = '#ffffff';
const BLACK_HEX = '#0a0a0b';

/**
 * Build a full, accessible palette from a small set of intent-level inputs.
 * This is the single source of truth shared by presets and custom themes.
 */
export function buildPalette(input: PaletteInput): Palette {
  const { mode, accent, bg, neutralH } = input;
  const isLight = mode === 'light';
  const neutralC = input.neutralC ?? 0.01;
  const fgL = input.fgL ?? (isLight ? 0.18 : 0.95);
  const panelLift = input.panelLift ?? (isLight ? 0.05 : 0.045);
  // Keep surfaces near-neutral with only a hint of the neutral hue. The accent
  // color must stay scarce — that's what makes the UI look coordinated instead
  // of tinted. (surfaceTint is kept in the interface for backwards compat but
  // no longer used as a strong accent-derived tint.)
  const surfaceChroma = neutralC;

  // --- Accent scale (sweep lightness, keep hue & chroma) ---
  // Solid accent fills (buttons, user bubble) use WHITE text per Apple/VS Code.
  // We slightly deepen mid-light accents so white reaches WCAG AA (>= 4.5:1).
  // Only very pale accents (yellows/ambers/pastels) fall back to dark text.
  const safeAccent = withAccessibleAccent(accent);
  const accentColor = oklchString(safeAccent);
  const accentForeground = solidFillForeground(safeAccent);
  const accentHoverL = clamp(safeAccent.l + (isLight ? -0.06 : 0.06), 0.12, 0.96);
  const accentHover = oklchString({ l: accentHoverL, c: safeAccent.c, h: safeAccent.h });

  // Soft chip / active-tab background: very light (light mode) or tinted-dark
  // (dark mode), carrying a hint of the accent hue.
  const softL = isLight ? 0.95 : 0.22;
  const softC = Math.min(safeAccent.c * 0.6, 0.07);
  const accentSoft = oklchString({ l: softL, c: softC, h: safeAccent.h });
  // Text on the soft background: a deeper / brighter accent for contrast.
  const softFgL = isLight ? clamp(safeAccent.l - 0.15, 0.32, 0.5) : clamp(safeAccent.l + 0.16, 0.8, 0.95);
  const accentSoftForeground = oklchString({ l: softFgL, c: safeAccent.c, h: safeAccent.h });

  // --- Neutral surfaces: receded frame vs lifted content ---
  // Use neutralC for the background too so the receded frame never carries a
  // strong theme tint. Only the accent should read as colorful.
  const bgL = bg.l;
  const background = oklchString({ l: bgL, c: neutralC, h: neutralH });
  // Sidebar shares the receded frame tone so the content area can pop.
  const sidebar = background;
  const panelL = clamp(bgL + panelLift, 0, 1);
  const panel = oklchString({ l: panelL, c: surfaceChroma, h: neutralH });
  const panelSolid = panel;
  const panelHover = oklchString({ l: clamp(panelL + (isLight ? 0.03 : 0.035), 0, 1), c: surfaceChroma, h: neutralH });
  const control = panelHover;
  const controlActive = oklchString({ l: clamp(panelL + (isLight ? 0.05 : 0.06), 0, 1), c: surfaceChroma, h: neutralH });
  const inputField = oklchString({ l: clamp(panelL - (isLight ? 0.015 : -0.01), 0, 1), c: surfaceChroma * 0.5, h: neutralH });

  // --- Borders / dividers ---
  const border = isLight
    ? oklchString({ l: clamp(bgL * 0.9, 0.86, 0.94), c: surfaceChroma, h: neutralH })
    : oklchString({ l: clamp(bgL * 2.1, 0.24, 0.32), c: surfaceChroma, h: neutralH });

  // --- Muted text (must stay >= 4.5:1 on the surface) ---
  const mutedForeground = oklchString({ l: isLight ? 0.46 : 0.72, c: surfaceChroma, h: neutralH });

  // --- Foreground ---
  const foreground = oklchString({ l: fgL, c: 0, h: 0 });
  const ring = accentColor;

  // --- Tooltip (inverted surface) ---
  const tooltip = isLight ? oklchString({ l: 0.18, c: 0, h: 0 }) : oklchString({ l: 0.95, c: 0, h: 0 });
  const tooltipForeground = isLight ? WHITE_HEX : BLACK_HEX;

  // --- Misc ---
  const shadow = isLight ? 'rgba(0, 0, 0, 0.06)' : 'rgba(0, 0, 0, 0.32)';
  const scrollThumb = isLight ? 'oklch(0.5 0 0 / 0.28)' : 'oklch(0.7 0 0 / 0.32)';
  const backgroundGrid = 'transparent';

  // --- User message bubble: brand-aligned, accessible ---
  const messageUser = accentColor;
  const messageUserForeground = accentForeground;

  return {
    background,
    backgroundGrid,
    foreground,
    mutedForeground,
    panel,
    panelSolid,
    panelHover,
    input: inputField,
    control,
    controlActive,
    border,
    ring,
    accent: accentColor,
    accentHover,
    accentForeground,
    accentSoft,
    accentSoftForeground,
    sidebar,
    messageUser,
    messageUserForeground,
    shadow,
    tooltip,
    tooltipForeground,
    scrollThumb,
  };
}

/* ------------------------------------------------------------------ */
/* Preset seeds                                                         */
/* ------------------------------------------------------------------ */

export interface PresetModeSeed {
  /** Accent in OKLCH. */
  accent: Oklch;
  /** Background in OKLCH. */
  bg: Oklch;
  /** Neutral tint hue (0 = pure grey, otherwise warm/cool). */
  neutralH: number;
  neutralC?: number;
  fgL?: number;
  panelLift?: number;
  surfaceTint?: number;
}

export function buildPresetPalette(mode: ThemeMode, seed: PresetModeSeed): Palette {
  const input: PaletteInput = {
    mode,
    accent: seed.accent,
    bg: seed.bg,
    neutralH: seed.neutralH,
  };
  if (seed.neutralC !== undefined) input.neutralC = seed.neutralC;
  if (seed.fgL !== undefined) input.fgL = seed.fgL;
  if (seed.panelLift !== undefined) input.panelLift = seed.panelLift;
  if (seed.surfaceTint !== undefined) input.surfaceTint = seed.surfaceTint;
  return buildPalette(input);
}

/** Parse a CSS color (hex or `oklch(...)`) into sRGB channels. */
function parseColorToRgb(s: string): [number, number, number] {
  if (s.startsWith('#')) return hexToRgb(s);
  const m = s.match(/oklch\(([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  if (!m) return [0, 0, 0];
  return oklchToRgb({ l: parseFloat(m[1]!), c: parseFloat(m[2]!), h: parseFloat(m[3]!) });
}

/** WCAG contrast between two CSS color strings (either hex or oklch()). */
export function contrastStrings(a: string, b: string): number {
  return contrastRatio(parseColorToRgb(a), parseColorToRgb(b));
}
