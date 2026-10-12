/**
 * Velour components: the building blocks of a proof-first scene.
 *
 * Every motion is frame-pure (a function of useCurrentFrame()): no CSS transitions, no Math.random, no Date.
 * Coordinates are STAGE pixels at 1920x1080. Media `src` values are paths relative to the kit's public/ folder
 * (e.g. "my-video/proof/anim-xxxx/pricing-crop.png") or http(s)/data URLs.
 *
 * The contract the proof-first-video-editor skill relies on (design-visuals.js, coverage.py, the checkers):
 *   - VelourCanvas paints the #121212 canvas with the drifting 40 px dot grid, in phase with PROJECT time
 *     (JOB_START_MS[composition id] or the startMs prop), and in PANEL_MODE scales the 1920x1080 stage uniformly into
 *     the left panel PANEL = { x 19.2, y 19.2, w 1473.75, h 1041.6, r 19.2 } (PANEL_SCALE = 0.7676).
 *   - transparent: a plain transparent full frame in FINAL-frame pixels (FRONT jobs: ProRes 4444 overlays).
 *   - panel={false}: the canvas full frame, no panel, no scaling (pieces with no camera beside them).
 *   - A VelourCanvas nested inside another renders plain (never nest canvases on purpose).
 */
import React from "react";
import {
  AbsoluteFill,
  Img,
  Loop,
  OffthreadVideo,
  Sequence,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { C, EASE_IN_OUT, EASE_OUT, FONT, MONO, geoZoom, mix, prog, velour } from "./theme";
import { DOT_PITCH, JOB_START_MS, driftAt } from "./phase";

const resolveSrc = (src: string): string => (/^(https?:|data:)/.test(src) ? src : staticFile(src));
const isVideoSrc = (src: string): boolean => /\.(mp4|mov|webm|m4v)$/i.test(src);

/** Opacity of an element that enters at `delay` and (optionally) leaves at `out`. */
const inOut = (frame: number, delay: number, dur: number, out?: number, outDur = 8): number =>
  prog(frame, delay, dur) * (out === undefined ? 1 : 1 - prog(frame, out, outDur, EASE_IN_OUT));

/* ------------------------------------------------------------------------------------------------- canvas */

export const PANEL = { x: 19.2, y: 19.2, w: 1473.75, h: 1041.6, r: 19.2 } as const;
export const PANEL_MODE = true;
export const PANEL_SCALE = PANEL.w / 1920;
const InPanel = React.createContext(false);

const DotLayer: React.FC<{ off: number; color?: string; pitch?: number; dot?: number }> = ({
  off,
  color = "rgb(70,70,70)",
  pitch = DOT_PITCH,
  dot = 2.1,
}) => (
  <div
    style={{
      position: "absolute",
      left: -pitch,
      top: -pitch,
      right: -pitch,
      bottom: -pitch,
      backgroundImage: `radial-gradient(circle, ${color} ${dot}px, rgba(0,0,0,0) ${dot + 0.8}px)`,
      backgroundSize: `${pitch}px ${pitch}px`,
      backgroundPosition: `${-pitch / 2}px ${-pitch / 2}px`,
      transform: `translate(${off % pitch}px, ${off % pitch}px)`,
    }}
  />
);

export const VelourCanvas: React.FC<{
  /** transparent full frame (FRONT jobs): no canvas, no panel, no scaling; coordinates are final-frame pixels */
  transparent?: boolean;
  /** false = the canvas full frame without the panel (no camera beside it, e.g. a pause between sections) */
  panel?: boolean;
  /** a soft warm glow in the middle of the panel, for a dramatic beat */
  glow?: boolean;
  /** explicit project start (ms) of this render; default JOB_START_MS[composition id], else 0 */
  startMs?: number;
  children?: React.ReactNode;
  /** extra style for the panel box (or the full frame when panel is off) */
  style?: React.CSSProperties;
}> = ({ transparent = false, panel = PANEL_MODE, glow = false, startMs, children, style }) => {
  const nested = React.useContext(InPanel);
  const frame = useCurrentFrame();
  const { fps, id } = useVideoConfig();
  const start = startMs ?? JOB_START_MS[id] ?? 0;
  const off = driftAt(start + (frame / fps) * 1000);
  const base: React.CSSProperties = { fontFamily: FONT, color: C.ink };
  if (nested) {
    return <AbsoluteFill style={{ ...base, ...style }}>{children}</AbsoluteFill>;
  }
  const glowLayer = glow ? (
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundImage: "radial-gradient(ellipse 90% 75% at 50% 40%, rgba(226,119,78,0.12) 0%, rgba(18,18,18,0) 60%)",
      }}
    />
  ) : null;
  if (transparent) {
    return (
      <AbsoluteFill style={{ ...base, background: "transparent", overflow: "hidden", ...style }}>
        <InPanel.Provider value={true}>{children}</InPanel.Provider>
      </AbsoluteFill>
    );
  }
  if (!panel) {
    return (
      <AbsoluteFill style={{ ...base, background: C.bg, overflow: "hidden", ...style }}>
        <DotLayer off={off} />
        {glowLayer}
        <InPanel.Provider value={true}>{children}</InPanel.Provider>
      </AbsoluteFill>
    );
  }
  const innerH = 1080 * PANEL_SCALE;
  return (
    <AbsoluteFill style={{ ...base, background: C.bg, overflow: "hidden" }}>
      <DotLayer off={off} />
      <div
        style={{
          position: "absolute",
          left: PANEL.x,
          top: PANEL.y,
          width: PANEL.w,
          height: PANEL.h,
          borderRadius: PANEL.r,
          overflow: "hidden",
          background: C.panel,
          boxShadow: velour.shadow.panel,
          ...style,
        }}
      >
        <DotLayer off={off * 0.8} color="rgb(42,42,40)" pitch={32} />
        {glowLayer}
        <div
          style={{
            position: "absolute",
            left: 0,
            top: (PANEL.h - innerH) / 2,
            width: 1920,
            height: 1080,
            transform: `scale(${PANEL_SCALE})`,
            transformOrigin: "0 0",
          }}
        >
          <InPanel.Provider value={true}>{children}</InPanel.Provider>
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------------------------------------- motion */

/** Entrance (rise + de-blur + settle) and optional exit at frame `out`. Position it with `style`. */
export const Reveal: React.FC<{
  delay?: number;
  dur?: number;
  y?: number;
  blur?: number;
  out?: number;
  outDur?: number;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}> = ({ delay = 0, dur = 12, y = 24, blur = 8, out, outDur = 8, style, children }) => {
  const frame = useCurrentFrame();
  const t = prog(frame, delay, dur);
  const o = inOut(frame, delay, dur, out, outDur);
  return (
    <div
      style={{
        opacity: o,
        transform: `translateY(${mix(t, y, 0)}px) scale(${mix(t, 0.985, 1)})`,
        filter: blur > 0 && t < 1 ? `blur(${mix(t, blur, 0)}px)` : undefined,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

/** A springy pop for chips, stamps and markers. */
export const Pop: React.FC<{ delay?: number; out?: number; style?: React.CSSProperties; children?: React.ReactNode }> = ({
  delay = 0,
  out,
  style,
  children,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 14, stiffness: 180, mass: 0.8 } });
  return (
    <div style={{ opacity: inOut(frame, delay, 5, out), transform: `scale(${mix(s, 0.6, 1)})`, ...style }}>{children}</div>
  );
};

/** Fades its children out from frame `at` (use it to clear a beat before the next one enters). */
export const FadeOut: React.FC<{ at: number; dur?: number; style?: React.CSSProperties; children?: React.ReactNode }> = ({
  at,
  dur = 8,
  style,
  children,
}) => {
  const frame = useCurrentFrame();
  return <div style={{ opacity: 1 - prog(frame, at, dur, EASE_IN_OUT), ...style }}>{children}</div>;
};

/* ---------------------------------------------------------------------------------------------- type */

/** A headline that builds word by word. Words listed in `accent` are drawn in the accent colour. */
export const Headline: React.FC<{
  text: string;
  delay?: number;
  stagger?: number;
  size?: number;
  weight?: number;
  color?: string;
  accent?: string[];
  maxWidth?: number;
  align?: "left" | "center";
  out?: number;
  style?: React.CSSProperties;
}> = ({ text, delay = 0, stagger = 3, size = 96, weight = 800, color = C.ink, accent = [], maxWidth = 1600, align = "left", out, style }) => {
  const frame = useCurrentFrame();
  const words = text.split(/\s+/).filter(Boolean);
  const hot = new Set(accent.map((w) => w.toLowerCase()));
  const fade = out === undefined ? 1 : 1 - prog(frame, out, 8, EASE_IN_OUT);
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: size,
        fontWeight: weight,
        lineHeight: 1.06,
        letterSpacing: "-0.02em",
        color,
        maxWidth,
        textAlign: align,
        opacity: fade,
        ...style,
      }}
    >
      {words.map((w, i) => {
        const t = prog(frame, delay + i * stagger, 10);
        const bare = w.replace(/[^\p{L}\p{N}%$]/gu, "").toLowerCase();
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              marginRight: "0.26em",
              opacity: t,
              transform: `translateY(${mix(t, 0.35 * size, 0)}px)`,
              color: hot.has(bare) ? C.accent : undefined,
            }}
          >
            {w}
          </span>
        );
      })}
    </div>
  );
};

/** A small letter-spaced label above a headline. */
export const Kicker: React.FC<{ text: string; delay?: number; color?: string; size?: number; out?: number; style?: React.CSSProperties }> = ({
  text,
  delay = 0,
  color = C.accent,
  size = 30,
  out,
  style,
}) => {
  const frame = useCurrentFrame();
  const t = prog(frame, delay, 10);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 700,
        letterSpacing: "0.16em",
        textTransform: "uppercase",
        color,
        opacity: inOut(frame, delay, 10, out),
        ...style,
      }}
    >
      <div style={{ width: mix(t, 0, 44), height: 5, borderRadius: 3, background: color }} />
      {text}
    </div>
  );
};

/** A rounded chip. solid = accent fill, soft = raised card, outline = ink border. */
export const Pill: React.FC<{
  text: string;
  delay?: number;
  variant?: "solid" | "soft" | "outline";
  size?: number;
  out?: number;
  style?: React.CSSProperties;
}> = ({ text, delay = 0, variant = "soft", size = 30, out, style }) => {
  const look: Record<string, React.CSSProperties> = {
    solid: { background: C.accent, color: C.white },
    soft: { background: C.paper, color: C.ink, boxShadow: velour.shadow.soft },
    outline: { background: "transparent", color: C.ink, border: `3px solid ${C.ink}` },
  };
  return (
    <Pop delay={delay} out={out} style={{ display: "inline-block", ...style }}>
      <div
        style={{
          fontFamily: FONT,
          fontSize: size,
          fontWeight: 600,
          padding: `${Math.round(size * 0.35)}px ${Math.round(size * 0.75)}px`,
          borderRadius: 999,
          whiteSpace: "nowrap",
          ...look[variant],
        }}
      >
        {text}
      </div>
    </Pop>
  );
};

/** A number that counts from `from` (0 or another SOURCED value: never an invented start) to `to`. */
export const CountUp: React.FC<{
  to: number;
  from?: number;
  delay?: number;
  dur?: number;
  format?: (n: number) => string;
  prefix?: string;
  suffix?: string;
  size?: number;
  weight?: number;
  color?: string;
  out?: number;
  style?: React.CSSProperties;
}> = ({ to, from = 0, delay = 0, dur = 30, format, prefix = "", suffix = "", size = 160, weight = 800, color = C.ink, out, style }) => {
  const frame = useCurrentFrame();
  const v = mix(prog(frame, delay, dur), from, to);
  const shown = format ? format(v) : Math.round(v).toLocaleString("en-US");
  return (
    <div
      style={{
        fontFamily: FONT,
        fontSize: size,
        fontWeight: weight,
        letterSpacing: "-0.03em",
        lineHeight: 1,
        color,
        fontVariantNumeric: "tabular-nums",
        opacity: inOut(frame, delay, 8, out),
        ...style,
      }}
    >
      {prefix}
      {shown}
      {suffix}
    </div>
  );
};

/* ------------------------------------------------------------------------------------------------- media */

export type MediaSpec = {
  src: string;
  kind?: "image" | "video";
  /** video: first source frame to play */
  startFrom?: number;
  /** video: loop the clip every N frames (for clips shorter than the hold) */
  loopFrames?: number;
  playbackRate?: number;
  objectPosition?: string;
  /** "contain" for screen recordings and screenshots (no UI text cut off); "cover" for photos and B-roll */
  fit?: "cover" | "contain";
  filter?: string;
};

/** The media itself (image or MUTED video), filling its parent. */
export const MediaFill: React.FC<MediaSpec & { startAt?: number; zoom?: number; origin?: string }> = ({
  src,
  kind,
  startFrom = 0,
  loopFrames,
  playbackRate = 1,
  objectPosition = "50% 50%",
  fit = "contain",
  filter,
  startAt = 0,
  zoom = 1,
  origin = "50% 50%",
}) => {
  const style: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: fit,
    objectPosition,
    transform: zoom !== 1 ? `scale(${zoom})` : undefined,
    transformOrigin: origin,
    filter,
  };
  const video = kind ? kind === "video" : isVideoSrc(src);
  if (!video) return <Img src={resolveSrc(src)} style={style} />;
  const v = <OffthreadVideo src={resolveSrc(src)} muted startFrom={startFrom} playbackRate={playbackRate} style={style} />;
  return (
    <Sequence from={startAt} layout="none">
      {loopFrames ? (
        <Loop durationInFrames={loopFrames} layout="none">
          {v}
        </Loop>
      ) : (
        v
      )}
    </Sequence>
  );
};

/** Slow geometric push of a media card from `delay` to the end of the composition. */
const usePush = (delay: number, push: [number, number]): number => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return geoZoom(prog(frame, delay, Math.max(1, durationInFrames - delay), EASE_IN_OUT), push[0], push[1]);
};

/** A rounded media card (16:9 by default) with shadow, entrance and a slow push (max ~4%). */
export const MediaFrame: React.FC<
  MediaSpec & {
    x: number;
    y: number;
    w: number;
    h?: number;
    delay?: number;
    out?: number;
    push?: [number, number];
    radius?: number;
    style?: React.CSSProperties;
    children?: React.ReactNode;
  }
> = ({ x, y, w, h, delay = 0, out, push = [1, 1.03], radius = velour.radius.media, style, children, ...media }) => {
  const zoom = usePush(delay, push);
  return (
    <Reveal delay={delay} out={out} style={{ position: "absolute", left: x, top: y, width: w, height: h ?? Math.round((w * 9) / 16) }}>
      <div
        style={{
          position: "relative",
          width: "100%",
          height: h ?? Math.round((w * 9) / 16),
          borderRadius: radius,
          overflow: "hidden",
          background: C.black,
          boxShadow: velour.shadow.card,
          ...style,
        }}
      >
        <MediaFill {...media} zoom={zoom} />
        {children}
      </div>
    </Reveal>
  );
};

/**
 * A real screenshot in a browser card: a title bar with the three window dots and a URL bar showing the real domain,
 * then the image. `children` are drawn over the IMAGE AREA (same pixel box), e.g. a <Highlight/> on the number the
 * creator says: measure the target in a still of the render, not in the source image.
 */
export const BrowserCard: React.FC<{
  url: string;
  src: string;
  x: number;
  y: number;
  w: number;
  /** height of the image area (default 16:10 of w) */
  h?: number;
  delay?: number;
  out?: number;
  push?: [number, number];
  fit?: "cover" | "contain";
  objectPosition?: string;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}> = ({ url, src, x, y, w, h, delay = 0, out, push = [1, 1.02], fit = "cover", objectPosition = "50% 0%", style, children }) => {
  const zoom = usePush(delay, push);
  const imgH = h ?? Math.round((w * 10) / 16);
  const bar = 64;
  return (
    <Reveal delay={delay} out={out} style={{ position: "absolute", left: x, top: y, width: w }}>
      <div style={{ borderRadius: 22, overflow: "hidden", background: C.paper, boxShadow: velour.shadow.card, ...style }}>
        <div style={{ height: bar, display: "flex", alignItems: "center", gap: 12, padding: "0 22px", background: C.dark }}>
          {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
            <div key={c} style={{ width: 16, height: 16, borderRadius: 8, background: c }} />
          ))}
          <div
            style={{
              marginLeft: 18,
              flex: 1,
              height: 40,
              borderRadius: 20,
              background: C.paper,
              color: C.inkSoft,
              fontFamily: FONT,
              fontSize: 26,
              display: "flex",
              alignItems: "center",
              padding: "0 22px",
              whiteSpace: "nowrap",
              overflow: "hidden",
            }}
          >
            {url}
          </div>
        </div>
        <div style={{ position: "relative", width: w, height: imgH, overflow: "hidden", background: C.white }}>
          <MediaFill src={src} kind="image" fit={fit} objectPosition={objectPosition} zoom={zoom} origin="50% 0%" />
          <div style={{ position: "absolute", inset: 0, transform: `scale(${zoom})`, transformOrigin: "50% 0%" }}>{children}</div>
        </div>
      </div>
    </Reveal>
  );
};

/** The source line under every proof: "<domain> · captured <Mon D, YYYY>". */
export const SourcePill: React.FC<{ domain: string; captured: string; delay?: number; out?: number; style?: React.CSSProperties }> = ({
  domain,
  captured,
  delay = 0,
  out,
  style,
}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 22px",
        borderRadius: 999,
        background: C.paper,
        color: C.muted,
        fontFamily: FONT,
        fontSize: 28,
        fontWeight: 500,
        boxShadow: velour.shadow.soft,
        opacity: inOut(frame, delay, 10, out),
        ...style,
      }}
    >
      <div style={{ width: 10, height: 10, borderRadius: 5, background: C.accent }} />
      {domain} · captured {captured}
    </div>
  );
};

/**
 * ONE highlight on one spoken target, drawn on at `delay` (2-4 frames before the word).
 * ring = an ellipse around it, box = a rounded rectangle, underline = a line under it, marker = a translucent swipe.
 * x/y/w/h = the TARGET's box in the parent's pixels; `pad` leaves a margin so the mark never touches the text.
 */
export const Highlight: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  delay?: number;
  dur?: number;
  kind?: "ring" | "box" | "underline" | "marker";
  color?: string;
  stroke?: number;
  pad?: number;
  out?: number;
}> = ({ x, y, w, h, delay = 0, dur = 10, kind = "box", color = C.accent, stroke = 6, pad = 10, out }) => {
  const frame = useCurrentFrame();
  const t = prog(frame, delay, dur);
  const o = out === undefined ? 1 : 1 - prog(frame, out, 8, EASE_IN_OUT);
  const L = x - pad;
  const T = y - pad;
  const W = w + 2 * pad;
  const H = h + 2 * pad;
  if (kind === "marker") {
    return (
      <div
        style={{
          position: "absolute",
          left: L,
          top: T,
          width: W * t,
          height: H,
          borderRadius: 8,
          background: color,
          opacity: 0.3 * o * (t > 0 ? 1 : 0),
        }}
      />
    );
  }
  const svgBox = { left: L - stroke, top: T - stroke, width: W + 2 * stroke, height: H + 2 * stroke };
  let shape: React.ReactNode;
  let len: number;
  if (kind === "ring") {
    const rx = W / 2 + stroke;
    const ry = H / 2 + stroke;
    len = Math.PI * (3 * (rx + ry) - Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));
    shape = <ellipse cx={svgBox.width / 2} cy={svgBox.height / 2} rx={rx - stroke} ry={ry - stroke} />;
  } else if (kind === "underline") {
    len = W;
    shape = <line x1={stroke} y1={svgBox.height - stroke} x2={stroke + W} y2={svgBox.height - stroke} />;
  } else {
    len = 2 * (W + H);
    shape = <rect x={stroke} y={stroke} width={W} height={H} rx={12} ry={12} />;
  }
  return (
    <svg style={{ position: "absolute", overflow: "visible", opacity: o, ...svgBox }} width={svgBox.width} height={svgBox.height}>
      <g
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={len}
        strokeDashoffset={len * (1 - t)}
      >
        {shape}
      </g>
    </svg>
  );
};

/** A 9:16 phone frame playing a vertical clip (or showing a vertical screenshot). */
export const Phone: React.FC<
  MediaSpec & { x: number; y: number; h?: number; delay?: number; out?: number; push?: [number, number] }
> = ({ x, y, h = 760, delay = 0, out, push = [1, 1.03], ...media }) => {
  const zoom = usePush(delay, push);
  const w = Math.round((h * 9) / 19.5);
  const bezel = Math.round(h * 0.018);
  return (
    <Reveal delay={delay} out={out} style={{ position: "absolute", left: x, top: y, width: w, height: h }}>
      <div
        style={{
          width: w,
          height: h,
          borderRadius: h * 0.07,
          background: "#0B0B0B",
          padding: bezel,
          boxSizing: "border-box",
          boxShadow: velour.shadow.card,
        }}
      >
        <div style={{ width: "100%", height: "100%", borderRadius: h * 0.06, overflow: "hidden", background: C.black }}>
          <MediaFill fit="cover" {...media} zoom={zoom} />
        </div>
      </div>
    </Reveal>
  );
};

/* ------------------------------------------------------------------------------------------------- cards */

/** A numbered card (steps, reasons, levels). */
export const NumberCard: React.FC<{
  n: number | string;
  title: string;
  body?: string;
  x: number;
  y: number;
  w?: number;
  delay?: number;
  out?: number;
  active?: boolean;
}> = ({ n, title, body, x, y, w = 520, delay = 0, out, active = true }) => (
  <Reveal delay={delay} out={out} style={{ position: "absolute", left: x, top: y, width: w }}>
    <div
      style={{
        borderRadius: velour.radius.card,
        background: active ? C.paper : C.dark,
        boxShadow: velour.shadow.card,
        padding: "34px 38px",
        fontFamily: FONT,
      }}
    >
      <div style={{ fontSize: 72, fontWeight: 800, color: active ? C.accent : C.faint, lineHeight: 1 }}>{n}</div>
      <div style={{ marginTop: 18, fontSize: 44, fontWeight: 700, color: C.ink, lineHeight: 1.12 }}>{title}</div>
      {body ? <div style={{ marginTop: 12, fontSize: 36, fontWeight: 500, color: C.muted, lineHeight: 1.3 }}>{body}</div> : null}
    </div>
  </Reveal>
);

/** Items that arrive one by one, each ticked in the accent colour. */
export const Checklist: React.FC<{
  items: string[];
  x: number;
  y: number;
  w?: number;
  delay?: number;
  /** frame of each item (overrides delay + i * stagger), so each lands on its word */
  at?: number[];
  stagger?: number;
  size?: number;
  out?: number;
}> = ({ items, x, y, w = 1100, delay = 0, at, stagger = 18, size = 44, out }) => {
  const frame = useCurrentFrame();
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, fontFamily: FONT }}>
      {items.map((it, i) => {
        const d = at && at[i] !== undefined ? at[i] : delay + i * stagger;
        const t = prog(frame, d, 10);
        return (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 24,
              marginBottom: Math.round(size * 0.55),
              opacity: inOut(frame, d, 10, out),
              transform: `translateX(${mix(t, -24, 0)}px)`,
            }}
          >
            <div
              style={{
                width: size * 1.1,
                height: size * 1.1,
                borderRadius: size * 0.3,
                background: C.accent,
                color: C.white,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: size * 0.7,
                fontWeight: 800,
                flex: "none",
              }}
            >
              ✓
            </div>
            <div style={{ fontSize: size, fontWeight: 600, color: C.ink, lineHeight: 1.2 }}>{it}</div>
          </div>
        );
      })}
    </div>
  );
};

/** A rotated stamp ("FREE", "OPEN SOURCE") that pops on. Only for words the creator says. */
export const Stamp: React.FC<{ text: string; x: number; y: number; delay?: number; size?: number; color?: string; rotate?: number; out?: number }> = ({
  text,
  x,
  y,
  delay = 0,
  size = 48,
  color = C.accent,
  rotate = -6,
  out,
}) => (
  <Pop delay={delay} out={out} style={{ position: "absolute", left: x, top: y }}>
    <div
      style={{
        transform: `rotate(${rotate}deg)`,
        border: `6px solid ${color}`,
        borderRadius: 14,
        padding: `${Math.round(size * 0.2)}px ${Math.round(size * 0.5)}px`,
        color,
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 900,
        letterSpacing: "0.08em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {text}
    </div>
  </Pop>
);

/** An arrow that draws itself from `from` to `to` (stage pixels), slightly curved, with a head at the end. */
export const ArrowTo: React.FC<{
  from: [number, number];
  to: [number, number];
  delay?: number;
  dur?: number;
  color?: string;
  width?: number;
  curve?: number;
  out?: number;
}> = ({ from, to, delay = 0, dur = 14, color = C.accent, width = 6, curve = 0.18, out }) => {
  const frame = useCurrentFrame();
  const t = prog(frame, delay, dur);
  const o = out === undefined ? 1 : 1 - prog(frame, out, 8, EASE_IN_OUT);
  const [x1, y1] = from;
  const [x2, y2] = to;
  const mx = (x1 + x2) / 2 - (y2 - y1) * curve;
  const my = (y1 + y2) / 2 + (x2 - x1) * curve;
  const len = Math.hypot(mx - x1, my - y1) + Math.hypot(x2 - mx, y2 - my);
  const ang = Math.atan2(y2 - my, x2 - mx);
  const head = 22;
  const p1 = [x2 - head * Math.cos(ang - 0.45), y2 - head * Math.sin(ang - 0.45)];
  const p2 = [x2 - head * Math.cos(ang + 0.45), y2 - head * Math.sin(ang + 0.45)];
  return (
    <svg style={{ position: "absolute", left: 0, top: 0, overflow: "visible", opacity: o }} width={1920} height={1080}>
      <path
        d={`M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`}
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeLinecap="round"
        strokeDasharray={len}
        strokeDashoffset={len * (1 - t)}
      />
      <path
        d={`M ${p1[0]} ${p1[1]} L ${x2} ${y2} L ${p2[0]} ${p2[1]}`}
        fill="none"
        stroke={color}
        strokeWidth={width}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={prog(frame, delay + dur - 3, 4)}
      />
    </svg>
  );
};

/** Monospace text (a command, a file name) on a raised card. */
export const CodeChip: React.FC<{ text: string; delay?: number; size?: number; out?: number; style?: React.CSSProperties }> = ({
  text,
  delay = 0,
  size = 36,
  out,
  style,
}) => (
  <Reveal delay={delay} out={out} style={{ display: "inline-block", ...style }}>
    <div
      style={{
        fontFamily: MONO,
        fontSize: size,
        color: C.ink,
        background: C.paper,
        borderRadius: 14,
        padding: `${Math.round(size * 0.4)}px ${Math.round(size * 0.6)}px`,
        boxShadow: velour.shadow.soft,
        whiteSpace: "pre",
      }}
    >
      {text}
    </div>
  </Reveal>
);
