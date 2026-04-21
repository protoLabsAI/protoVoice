import { useSyncExternalStore } from 'react';
import { orbInstance } from '../orb/instance';
import type { VoiceOrb } from '../orb/viz';

/**
 * Subscribe to the active VoiceOrb instance. Returns null until the
 * orb mounts (StrictMode: may flip to null briefly between double-
 * invokes in dev; UI should tolerate that).
 */
export function useOrbInstance(): VoiceOrb | null {
  return useSyncExternalStore(orbInstance.subscribe, orbInstance.get, orbInstance.get);
}
