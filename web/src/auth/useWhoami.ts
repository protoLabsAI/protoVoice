import { useSyncExternalStore } from 'react';
import { whoamiStore, type Whoami } from './whoami';

/**
 * Subscribe to the /api/whoami snapshot. Returns null until the boot
 * fetch completes.
 *
 * Helper selectors for the common UI gates:
 *   - `isAdmin(whoami)` — true for role=admin (including the synthetic
 *     single-user fallback, which runs as admin so dev isn't locked
 *     out).
 *   - `isSkillLocked(whoami)` — true when a non-admin user has a
 *     pinned_skill set; UI should hide skill switchers.
 *   - `isVizLocked(whoami)` — true when a non-admin user has a
 *     pinned_viz OR a pinned_skill (since the pinned skill's own viz
 *     is also locked against user edits).
 */
export function useWhoami(): Whoami | null {
  return useSyncExternalStore(whoamiStore.subscribe, whoamiStore.get, whoamiStore.get);
}

export function isAdmin(w: Whoami | null): boolean {
  return !!w && w.role === 'admin';
}

export function isSkillLocked(w: Whoami | null): boolean {
  return !!w && w.role !== 'admin' && !!w.pinned_skill;
}

export function isVizLocked(w: Whoami | null): boolean {
  return !!w && w.role !== 'admin' && (!!w.pinned_viz || !!w.pinned_skill);
}
