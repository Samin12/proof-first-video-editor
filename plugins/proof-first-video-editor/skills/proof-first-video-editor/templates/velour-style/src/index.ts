/**
 * Velour style package: public API (proof-first-video-editor).
 * From a job scene: import { VelourCanvas, BrowserCard, Highlight, C, ... } from "../../../styles/velour/src";
 */
export { C, FONT, MONO, velour, EASE_OUT, EASE_IN_OUT, EASE_IN, prog, mix, geoZoom, compact, money, type VelourTheme } from "./theme";
export { JOB_START_MS, DRIFT_PX_PER_SEC, DOT_PITCH, driftAt } from "./phase";
export {
  VelourCanvas,
  PANEL,
  PANEL_MODE,
  PANEL_SCALE,
  Reveal,
  Pop,
  FadeOut,
  Headline,
  Kicker,
  Pill,
  CountUp,
  MediaFill,
  MediaFrame,
  BrowserCard,
  SourcePill,
  Highlight,
  Phone,
  NumberCard,
  Checklist,
  Stamp,
  ArrowTo,
  CodeChip,
  type MediaSpec,
} from "./components";
