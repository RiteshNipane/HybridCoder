import React, { useEffect, useState } from 'react';
import { useStore } from '@/store';
import {
  AsciiLagoon,
  AsciiLagoonDay,
  AsciiLagoonNight,
  AsciiLagoonDolphin,
  AsciiLagoonManta,
  AsciiLagoonTurtle,
  AsciiLagoonSeahorse,
  AsciiLagoonHammerhead,
  AsciiLagoonTang,
  AsciiJellyfish,
  AsciiSharks,
  AsciiKoi,
  AsciiRobots,
  AsciiStarlings,
} from 'ascii-things/react';

const SCENE_MAP: Record<string, React.ComponentType<any>> = {
  lagoon: AsciiLagoon,
  'lagoon-day': AsciiLagoonDay,
  'lagoon-night': AsciiLagoonNight,
  dolphin: AsciiLagoonDolphin,
  manta: AsciiLagoonManta,
  turtle: AsciiLagoonTurtle,
  seahorse: AsciiLagoonSeahorse,
  hammerhead: AsciiLagoonHammerhead,
  tang: AsciiLagoonTang,
  jellyfish: AsciiJellyfish,
  sharks: AsciiSharks,
  koi: AsciiKoi,
  robots: AsciiRobots,
  starlings: AsciiStarlings,
};

/** Scenes that accept the `abyss` palette. The lagoon family and koi keep their own colours. */
const PALETTE_SCENES = new Set(['jellyfish', 'sharks', 'robots', 'starlings']);

/** How long one "life" of the scene lasts before it fades out and respawns. */
const LOOP_MS = 25_000;
const FADE_MS = 1_200;

export const AsciiBackground: React.FC = () => {
  const savedScene = useStore((s) => s.settings.asciiScene) as string | undefined;
  // Unknown / removed scenes (e.g. old 'city' or 'servers' settings) fall back to Lagoon.
  const asciiScene = savedScene === 'none' ? 'none' : savedScene && SCENE_MAP[savedScene] ? savedScene : 'lagoon';
  const asciiPaused = useStore((s) => s.settings.asciiPaused || false);
  const rawSpeed = useStore((s) => s.settings.asciiSpeed ?? 1);
  const speed = Math.min(Math.max(rawSpeed, 0.2), 2);

  const [isVisible, setIsVisible] = useState(!document.hidden);
  const [cycle, setCycle] = useState(0); // bumps every loop -> new seed + fresh spawn
  const [fading, setFading] = useState(false);
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });

  // Pause when the tab is hidden
  useEffect(() => {
    const onVis = () => setIsVisible(!document.hidden);
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
  }, []);

  // Track the real window size (debounced). The scene is re-created on resize so
  // the creatures are always spawned inside the full-size world, not a tiny one.
  useEffect(() => {
    let t: number;
    const onResize = () => {
      window.clearTimeout(t);
      t = window.setTimeout(
        () => setSize({ w: window.innerWidth, h: window.innerHeight }),
        250,
      );
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  // The loop: fade out -> respawn with a new seed -> fade in -> repeat.
  const active = !asciiPaused && isVisible && asciiScene !== 'none';
  useEffect(() => {
    if (!active) return;
    let t2: number | undefined;
    const t1 = window.setTimeout(() => {
      setFading(true);
      t2 = window.setTimeout(() => {
        setCycle((c) => c + 1);
        setFading(false);
      }, FADE_MS);
    }, LOOP_MS - FADE_MS);
    return () => {
      window.clearTimeout(t1);
      if (t2) window.clearTimeout(t2);
      setFading(false);
    };
  }, [cycle, active, asciiScene]);

  if (!active) {
    return (
      <div
        style={{ position: 'fixed', inset: 0, background: '#07080d', zIndex: 0, pointerEvents: 'none' }}
      />
    );
  }

  const key = `${asciiScene}-${cycle}-${size.w}x${size.h}`;
  // Explicit pixel size so the world is never measured at a default/tiny size.
  const common: any = {
    width: size.w,
    height: size.h,
    speed,
    ...(PALETTE_SCENES.has(asciiScene) ? { palette: 'abyss' } : {}),
    columns: 90,
    interactive: false,
    seed: cycle * 7 + 3,
    style: { display: 'block' },
  };

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
        overflow: 'hidden',
        background: '#07080d',
      }}
    >
      {/* Scene layer: wrapper stays mounted so the fade is smooth; the scene inside is re-keyed */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          opacity: fading ? 0 : 0.8,
          transition: `opacity ${FADE_MS}ms ease-in-out`,
        }}
      >
        <div key={key}>
          {(() => {
            const Scene = SCENE_MAP[asciiScene] ?? AsciiLagoon;
            return <Scene {...common} {...(asciiScene === 'jellyfish' ? { count: 7 } : {})} />;
          })()}
        </div>
      </div>

      {/* Vignette: keeps the centre calm so chat text stays readable */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'radial-gradient(ellipse at 50% 55%, rgba(7,8,13,0.72) 0%, rgba(7,8,13,0.35) 45%, rgba(7,8,13,0) 80%)',
        }}
      />
    </div>
  );
};
