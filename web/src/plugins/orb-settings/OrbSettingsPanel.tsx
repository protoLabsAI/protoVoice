import { useCallback, useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { useOrbInstance } from './useOrbInstance';
import { FieldSlider } from './FieldSlider';
import { FieldColor } from './FieldColor';
import { PresetControls } from './PresetControls';
import {
  FIELDS,
  PALETTE_NAMES,
  SECTIONS,
  formatPresetBlock,
  randomHex,
  randomSliderValue,
  type FieldSpec,
  type PaletteName,
} from './fields';
import {
  clearParams,
  loadCustom,
  loadPalette,
  savePalette,
  saveParams,
  saveCustom,
  type CustomPresetMap,
} from '../orb/storage';
import { applyParam, applyPreset } from '../orb/broadcast';

export function OrbSettingsPanel() {
  const orb = useOrbInstance();
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [palette, setPalette] = useState<PaletteName>('Aurora');
  const [customMap, setCustomMap] = useState<CustomPresetMap>({});
  const [customName, setCustomName] = useState<string>('');
  const [copyLabel, setCopyLabel] = useState<string>('Copy config');
  const hydratedRef = useRef(false);

  // Debounced save on every change.
  const saveTimerRef = useRef<number | null>(null);
  const scheduleSave = useCallback((p: Record<string, unknown>) => {
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => saveParams(p), 250);
  }, []);

  // Sync panel UI with whatever the orb already has — the orb hydrates
  // itself from localStorage at mount (OrbCanvas), so the panel just
  // reads live state.
  useEffect(() => {
    if (!orb || hydratedRef.current) return;
    hydratedRef.current = true;

    const savedPalette = loadPalette();
    if (savedPalette && (PALETTE_NAMES as readonly string[]).includes(savedPalette)) {
      setPalette(savedPalette as PaletteName);
    }
    setParams(orb.getParams());
    setCustomMap(loadCustom());
  }, [orb]);

  const updateParam = useCallback(
    (key: string, value: unknown) => {
      applyParam(key, value);
      setParams((prev) => {
        const next = { ...prev, [key]: value };
        scheduleSave(next);
        return next;
      });
    },
    [scheduleSave],
  );

  const onPaletteChange = (name: string) => {
    if (!orb) return;
    setPalette(name as PaletteName);
    applyPreset(name);
    setParams(orb.getParams());
    savePalette(name);
    clearParams();
  };

  const onReset = () => {
    if (!orb) return;
    applyPreset(palette);
    setParams(orb.getParams());
    clearParams();
    setCustomName('');
  };

  const onRandomize = () => {
    if (!orb) return;
    const next: Record<string, unknown> = {};
    for (const spec of FIELDS) {
      const v = spec.kind === 'color' ? randomHex() : randomSliderValue(spec);
      applyParam(spec.key, v);
      next[spec.key] = v;
    }
    setParams(next);
    saveParams(next);
    setCustomName('');
  };

  const onCopy = async () => {
    if (!orb) return;
    const snippet = formatPresetBlock(orb.getParams(), 'NewPreset');
    let ok = false;
    try {
      await navigator.clipboard.writeText(snippet);
      ok = true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = snippet;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch {}
      ta.remove();
    }
    setCopyLabel(ok ? 'Copied' : 'Copy failed');
    window.setTimeout(() => setCopyLabel('Copy config'), 1200);
  };

  const onSaveAs = () => {
    if (!orb) return;
    const name = (window.prompt('Save preset as:') ?? '').trim();
    if (!name) return;
    const next: CustomPresetMap = {
      ...customMap,
      [name]: { palette, params: orb.getParams() },
    };
    setCustomMap(next);
    saveCustom(next);
    setCustomName(name);
  };

  const onLoadCustom = (name: string) => {
    if (!orb || !name) return;
    const payload = customMap[name];
    if (!payload) return;
    if (payload.palette) {
      setPalette(payload.palette as PaletteName);
      applyPreset(payload.palette);
    }
    for (const [k, v] of Object.entries(payload.params ?? {})) applyParam(k, v);
    setParams(orb.getParams());
    setCustomName(name);
    if (payload.palette) savePalette(payload.palette);
    saveParams(orb.getParams());
  };

  const onDeleteCustom = () => {
    if (!customName) return;
    if (!window.confirm(`Delete preset "${customName}"?`)) return;
    const next = { ...customMap };
    delete next[customName];
    setCustomMap(next);
    saveCustom(next);
    setCustomName('');
  };

  return (
    <div className="space-y-5">
      <PresetControls
        palette={palette}
        onPaletteChange={onPaletteChange}
        customMap={customMap}
        customName={customName}
        onLoadCustom={onLoadCustom}
        onDeleteCustom={onDeleteCustom}
        onSaveAs={onSaveAs}
        onRandomize={onRandomize}
        onCopy={onCopy}
        onReset={onReset}
        copyLabel={copyLabel}
        disabled={!orb}
      />

      {SECTIONS.map((s) => (
        <SettingsSection
          key={s.id}
          title={s.label}
          fields={FIELDS.filter((f) => f.section === s.id)}
          params={params}
          onChange={updateParam}
        />
      ))}
    </div>
  );
}

function SettingsSection({
  title,
  fields,
  params,
  onChange,
}: {
  title: string;
  fields: FieldSpec[];
  params: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  if (fields.length === 0) return null;
  return (
    <Panel title={title}>
      {fields.map((f) =>
        f.kind === 'color' ? (
          <FieldColor
            key={f.key}
            field={f}
            value={String(params[f.key] ?? '#000000')}
            onChange={(k, v) => onChange(k, v)}
          />
        ) : (
          <FieldSlider
            key={f.key}
            field={f}
            value={Number(params[f.key] ?? f.min)}
            onChange={(k, v) => onChange(k, v)}
          />
        ),
      )}
    </Panel>
  );
}
