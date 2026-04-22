/**
 * Orb parameter broadcaster. The settings panel calls applyParam /
 * applyPreset instead of orb.setParam / orb.setPreset directly, so
 * multiple orb instances (e.g. main + mobile preview) can stay in sync.
 *
 * The main orb instance is still the single source of truth for
 * getParams(); listeners only observe side-effects going out.
 */

import { orbInstance } from './instance';

export type ParamListener = (key: string, value: unknown) => void;
export type PresetListener = (name: string) => void;

const paramListeners = new Set<ParamListener>();
const presetListeners = new Set<PresetListener>();

/** Set a param on the main orb and broadcast to all subscribers. */
export function applyParam(key: string, value: unknown): void {
  orbInstance.get()?.setParam(key, value);
  paramListeners.forEach((l) => l(key, value));
}

/** Set a palette preset on the main orb and broadcast. */
export function applyPreset(name: string): void {
  orbInstance.get()?.setPreset(name);
  presetListeners.forEach((l) => l(name));
}

export function subscribeParam(l: ParamListener): () => void {
  paramListeners.add(l);
  return () => {
    paramListeners.delete(l);
  };
}

export function subscribePreset(l: PresetListener): () => void {
  presetListeners.add(l);
  return () => {
    presetListeners.delete(l);
  };
}
