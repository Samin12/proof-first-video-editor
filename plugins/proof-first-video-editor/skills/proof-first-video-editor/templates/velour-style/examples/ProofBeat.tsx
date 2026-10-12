/**
 * Example: one proof beat and one concept beat, the pattern most BEHIND jobs repeat. Not registered as a job; copy the
 * parts you need into src/jobs/<job id>/Scene.tsx (imports there are "../../../styles/velour/src").
 *
 *   f0   "<claim>": a real screenshot in a browser card (URL bar = the real domain), source pill under it
 *   f42  "<the number>": ONE highlight on the number in the screenshot, 3 frames before the word
 *   f150 the card leaves BEFORE the next beat enters; "<concept>": a word-by-word headline + a checklist on the words
 *
 * The image path is relative to the kit's public/ folder (copy proofs to public/<video>/proof/<job id>/).
 */
import React from "react";
import { BrowserCard, Checklist, FadeOut, Headline, Highlight, Kicker, SourcePill, VelourCanvas } from "../src";

const at = (frame: number, lead = 3) => frame - lead;

export const ProofBeat: React.FC = () => (
  <VelourCanvas>
    <FadeOut at={150}>
      <BrowserCard url="example.com/pricing" src="my-video/proof/anim-xxxx/pricing-crop.png" x={260} y={110} w={1400} h={760}>
        <Highlight x={612} y={318} w={236} h={92} kind="box" delay={at(45)} />
      </BrowserCard>
      <SourcePill domain="example.com" captured="Oct 12, 2026" delay={12} style={{ position: "absolute", left: 260, top: 960 }} />
    </FadeOut>
    <Kicker text="How it works" delay={at(160)} style={{ position: "absolute", left: 140, top: 150 }} />
    <Headline text="Three steps, no code" accent={["three"]} delay={at(163)} style={{ position: "absolute", left: 140, top: 210 }} />
    <Checklist items={["Record", "Cut", "Publish"]} x={140} y={420} at={[at(190), at(215), at(240)]} />
  </VelourCanvas>
);
