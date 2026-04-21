/**
 * Authoritative field registry for orb params. Consumed by the
 * OrbSettingsPanel (mounts the UI) and the Randomize / Copy config
 * helpers (walk this list). Keys match VoiceOrb.basePreset; changing
 * one here without updating viz.js is a bug.
 */

export type ColorField = {
  kind: 'color';
  key: 'primaryEnergy' | 'secondaryEnergy';
  label: string;
  section: SectionId;
};

export type SliderField = {
  kind: 'slider';
  key: string;
  label: string;
  section: SectionId;
  min: number;
  max: number;
  step: number;
};

export type FieldSpec = ColorField | SliderField;

export type SectionId = 'color' | 'energy' | 'motion' | 'fractal' | 'perf';

export const SECTIONS: Array<{ id: SectionId; label: string }> = [
  { id: 'color',   label: 'Color' },
  { id: 'energy',  label: 'Energy' },
  { id: 'motion',  label: 'Motion' },
  { id: 'fractal', label: 'Fractal' },
  { id: 'perf',    label: 'Performance' },
];

export const FIELDS: FieldSpec[] = [
  { kind: 'color',  key: 'primaryEnergy',       label: 'Primary',        section: 'color' },
  { kind: 'color',  key: 'secondaryEnergy',     label: 'Secondary',      section: 'color' },

  { kind: 'slider', key: 'density',             label: 'Density',        section: 'energy',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'atmosphereGlow',      label: 'Glow',           section: 'energy',  min: 0.0, max: 5.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereLevel',     label: 'Halo thickness', section: 'energy',  min: 0.1, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'atmosphereScale',     label: 'Halo scale',     section: 'energy',  min: 1.0, max: 1.1,  step: 0.001 },
  { kind: 'slider', key: 'chromaticAberration', label: 'Aberration',     section: 'energy',  min: 0.0, max: 0.05, step: 0.001 },

  { kind: 'slider', key: 'speed',               label: 'Internal speed', section: 'motion',  min: 0.1, max: 3.0,  step: 0.1   },
  { kind: 'slider', key: 'orbRotation',         label: 'Auto-rotation',  section: 'motion',  min: 0.0, max: 1.0,  step: 0.01  },
  { kind: 'slider', key: 'internalAnim',        label: 'Anim speed',     section: 'motion',  min: 0.0, max: 2.0,  step: 0.01  },

  { kind: 'slider', key: 'fractalIters',        label: 'Iterations',     section: 'fractal', min: 2,    max: 12,   step: 1     },
  { kind: 'slider', key: 'fractalScale',        label: 'Scale',          section: 'fractal', min: 0.3,  max: 1.5,  step: 0.01  },
  { kind: 'slider', key: 'fractalDecay',        label: 'Decay',          section: 'fractal', min: -25,  max: -5,   step: 0.1   },
  { kind: 'slider', key: 'smoothness',          label: 'Smoothness',     section: 'fractal', min: 0.0,  max: 0.15, step: 0.001 },
  { kind: 'slider', key: 'asymmetry',           label: 'Asymmetry',      section: 'fractal', min: 0.0,  max: 1.0,  step: 0.01  },

  { kind: 'slider', key: 'dpr',                 label: 'Resolution',     section: 'perf',    min: 0.1,  max: 2.0,  step: 0.1   },
];

export const PALETTE_NAMES = ['Aurora', 'Ember', 'Citrus', 'Forest', 'Noir'] as const;
export type PaletteName = (typeof PALETTE_NAMES)[number];

// ---- Formatting helpers -------------------------------------------------

export function formatValue(v: number, step: number): string {
  if (Number.isInteger(step)) return String(Math.round(v));
  const decimals = step < 0.01 ? 3 : step < 0.1 ? 2 : step < 1 ? 1 : 0;
  return Number(v).toFixed(decimals);
}

export function randomHex(): string {
  return '#' + Math.floor(Math.random() * 0x1000000).toString(16).padStart(6, '0');
}

export function randomSliderValue(spec: SliderField): number {
  const steps = Math.max(1, Math.round((spec.max - spec.min) / spec.step));
  const n = Math.floor(Math.random() * (steps + 1));
  const v = spec.min + n * spec.step;
  return Number.isInteger(spec.step) ? Math.round(v) : Number(v.toFixed(4));
}

export function randomizeAll(): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  for (const f of FIELDS) {
    out[f.key] = f.kind === 'color' ? randomHex() : randomSliderValue(f);
  }
  return out;
}

export function formatPresetValue(v: unknown, spec: FieldSpec): string {
  if (spec.kind === 'color') return `'${String(v)}'`;
  if (Number.isInteger(spec.step)) return String(Math.round(Number(v)));
  return String(Number(v));
}

export function formatPresetBlock(params: Record<string, unknown>, name = 'NewPreset'): string {
  const body = FIELDS.map((s) => `    ${s.key}: ${formatPresetValue(params[s.key], s)},`).join('\n');
  return `  ${name}: {\n${body}\n  },`;
}
