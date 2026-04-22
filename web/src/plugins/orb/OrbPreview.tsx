import { useEffect, useRef } from 'react';
import { VoiceOrb } from './viz';
import { orbInstance } from './instance';
import { subscribeParam, subscribePreset } from './broadcast';
import { loadPalette } from './storage';
import { useVoiceStateSelector } from '../../voice/hooks';
import type { VoiceState } from '../../voice/state';

const BUILTIN = new Set(['Aurora', 'Ember', 'Citrus', 'Forest', 'Noir']);

/**
 * Secondary VoiceOrb for the mobile drawer — mirrors the main orb's
 * parameters via the broadcast bus, so users can watch settings take
 * effect while the main orb is occluded by the full-width drawer.
 *
 * Kept cheap:
 *   - small fixed canvas (driven by the host div size)
 *   - doesn't register as orbInstance — stays read-only
 *   - audio stream intentionally not piped in; this is about visible
 *     settings changes, not level-reactive bloom
 */
export function OrbPreview() {
  const hostRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<VoiceOrb | null>(null);
  const state: VoiceState = useVoiceStateSelector((s) => s.state);

  useEffect(() => {
    if (!hostRef.current) return;
    let cleaned = false;

    const savedPalette = loadPalette();
    const initialPreset = savedPalette && BUILTIN.has(savedPalette) ? savedPalette : 'Aurora';
    const orb = new VoiceOrb(hostRef.current, { preset: initialPreset });

    // Sync from main orb's current state so the preview matches.
    const main = orbInstance.get();
    if (main) {
      for (const [k, v] of Object.entries(main.getParams())) orb.setParam(k, v);
    }

    // Subsequent changes flow through the broadcast bus.
    const unsubParam = subscribeParam((k, v) => orb.setParam(k, v));
    const unsubPreset = subscribePreset((name) => orb.setPreset(name));

    orbRef.current = orb;
    return () => {
      if (cleaned) return;
      cleaned = true;
      unsubParam();
      unsubPreset();
      orb.dispose?.();
      orbRef.current = null;
    };
  }, []);

  useEffect(() => {
    orbRef.current?.setState?.(state);
  }, [state]);

  // Caller owns the sized container. We fill it with a Three.js canvas.
  return <div ref={hostRef} className="absolute inset-0" />;
}
