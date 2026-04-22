/**
 * Whoami store — the resolved user's role + pinned values.
 *
 * Loaded once at app boot (via <WhoamiProvider />). The client adapts:
 *   - role: 'admin' sees the skill selector + orb settings
 *   - role: 'user' with pinned_skill sees a locked read-only chip
 *   - pinned_viz overrides the active skill's viz on session start
 */

export interface Whoami {
  id: string;
  display_name: string;
  role: 'admin' | 'user';
  pinned_skill: string | null;
  pinned_viz: Record<string, unknown> | null;
  auth_source: 'infisical' | 'file' | 'empty';
}

type Listener = () => void;

let _snap: Whoami | null = null;
const _listeners = new Set<Listener>();

export const whoamiStore = {
  get: (): Whoami | null => _snap,
  set: (w: Whoami | null) => {
    _snap = w;
    _listeners.forEach((l) => l());
  },
  subscribe: (l: Listener): (() => void) => {
    _listeners.add(l);
    return () => {
      _listeners.delete(l);
    };
  },
};

/** Fetch /api/whoami and populate the store. Call at app boot. */
export async function loadWhoami(): Promise<Whoami | null> {
  try {
    const r = await fetch('/api/whoami');
    if (!r.ok) {
      console.warn('[whoami] /api/whoami failed:', r.status);
      whoamiStore.set(null);
      return null;
    }
    const w = (await r.json()) as Whoami;
    whoamiStore.set(w);
    return w;
  } catch (e) {
    console.warn('[whoami] fetch error:', e);
    whoamiStore.set(null);
    return null;
  }
}
