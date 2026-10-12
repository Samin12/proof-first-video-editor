/**
 * Velour theme: the dark proof-first palette, the type stack and the house easing.
 * Self-contained (imports only from remotion), so it compiles against any animation-kit engine version.
 * Colour names are SEMANTIC: ink = primary text (light), paper = a raised card on the dark canvas.
 * Never hardcode hexes in a scene: use C.*.
 */
import { Easing, interpolate } from "remotion";

export const C = {
  bg: "#121212", // the canvas (matches scripts/borumi/canvas.py and the Borumi canvas image)
  panel: "#181716", // inside the left panel of a BEHIND render
  paper: "#1E1D1B", // raised card / chip surface
  paperEdge: "#0C0C0B",
  dark: "#2A2825", // a second, lighter card tone
  darkSoft: "#33302B",
  line: "#34322E", // hairlines, empty tracks
  ink: "#F4F1EC", // primary text (LIGHT)
  inkSoft: "#D9D4CC",
  muted: "#A39C91", // secondary text
  faint: "#6E685F", // tertiary text
  accent: "#E2774E", // terracotta: the ONE subject colour
  accentDeep: "#C9643F",
  accentSoft: "#3A241A", // soft accent tint (fills, ghost numbers)
  green: "#46B482",
  red: "#E5564A",
  gold: "#E0B04F",
  white: "#FFFFFF", // text on the accent
  black: "#000000",
} as const;

export const FONT = "'Inter', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif";
export const MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace";

export const velour = {
  color: C,
  font: { display: FONT, ui: FONT, mono: MONO },
  shadow: {
    card: "0 40px 80px -30px rgba(0,0,0,0.85), 0 12px 28px -12px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.08)",
    soft: "0 18px 40px -18px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.07)",
    panel: "0 20px 20px rgba(0,0,0,0.39), inset 0 0 0 1px rgba(255,255,255,0.07)",
  },
  radius: { card: 28, chip: 999, media: 22 },
  /** Minimum legible sizes in STAGE pixels (the stage is scaled x0.768 into the panel). */
  minText: { body: 36, label: 28 },
} as const;

export type VelourTheme = typeof velour;

/** House easing: a confident ease-out (fast start, long settle) and a symmetric ease-in-out. */
export const EASE_OUT = Easing.bezier(0.16, 1, 0.3, 1);
export const EASE_IN_OUT = Easing.bezier(0.65, 0, 0.35, 1);
export const EASE_IN = Easing.bezier(0.7, 0, 0.84, 0);

/** Clamped 0..1 progress of `frame` over [start, start + dur] with an easing. */
export const prog = (frame: number, start: number, dur: number, easing: (t: number) => number = EASE_OUT): number =>
  interpolate(frame, [start, start + Math.max(1, dur)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });

/** Linear map of a 0..1 progress onto [a, b]. */
export const mix = (t: number, a: number, b: number): number => a + (b - a) * t;

/** Geometric zoom z0 * (z1/z0)^t: the only zoom that reads as constant speed. */
export const geoZoom = (t: number, z0: number, z1: number): number => z0 * Math.pow(z1 / z0, t);

const trimZeros = (s: string) => s.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");

/** Compact numbers: 4760000 -> "4.76M", 23000 -> "23K", 1500 -> "1.5K". */
export const compact = (n: number, digits = 2): string => {
  const a = Math.abs(n);
  if (a >= 1e9) return `${trimZeros((n / 1e9).toFixed(digits))}B`;
  if (a >= 1e6) return `${trimZeros((n / 1e6).toFixed(digits))}M`;
  if (a >= 1e4) return `${trimZeros((n / 1e3).toFixed(0))}K`;
  if (a >= 1e3) return `${trimZeros((n / 1e3).toFixed(1))}K`;
  return `${Math.round(n)}`;
};

/** "$2,000" style money formatting (whole units). */
export const money = (n: number, currency = "$"): string => `${currency}${Math.round(n).toLocaleString("en-US")}`;
