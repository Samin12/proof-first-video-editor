/**
 * Drift phase of the dot grid, keyed by animation job id (= the Remotion composition id).
 *
 * JOB_START_MS["<job id>"] = the PROJECT time (ms) where that job's render is placed on the Borumi timeline. The
 * canvas drift is a function of project time, so a render that knows its start continues the dots of the drifting
 * canvas clips around it without a jump at the cut. design-visuals.js (prepare step) adds one entry per new job in
 * ONE edit; nothing else edits this file. A job without an entry drifts from phase 0 (VelourCanvas also accepts an
 * explicit `startMs` prop).
 */
export const JOB_START_MS: Record<string, number> = {
  // "anim-xxxx": 57800, // <video title>
};

/** Dot drift: 2.5 px/s diagonally on a 40 px grid, one full pitch every 16 s (scripts/borumi/canvas.py uses the same). */
export const DRIFT_PX_PER_SEC = 2.5;
export const DOT_PITCH = 40;
export const driftAt = (projectMs: number): number => ((projectMs / 1000) * DRIFT_PX_PER_SEC) % DOT_PITCH;
