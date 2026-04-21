import type { VoiceOrb } from './viz';

/**
 * Cross-plugin singleton for the active VoiceOrb. OrbCanvas sets it on
 * mount / clears on unmount; settings / presets plugins read from it.
 *
 * Read-heavy code path (settings panel updating on every drag of a
 * slider) shouldn't re-render on changes, so we expose both a direct
 * `get()` and a subscribe pattern for cases that *do* need reactivity.
 */

let _orb: VoiceOrb | null = null;
const listeners = new Set<() => void>();

export const orbInstance = {
  get: () => _orb,
  set(orb: VoiceOrb | null) {
    _orb = orb;
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
};
