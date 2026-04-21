import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useOrbInstance } from './useOrbInstance';
import {
  FIELDS,
  PALETTE_NAMES,
  SECTIONS,
  formatValue,
  formatPresetBlock,
  randomHex,
  randomSliderValue,
  type FieldSpec,
  type PaletteName,
  type SliderField,
} from './fields';
import {
  clearParams,
  loadCustom,
  loadPalette,
  loadParams,
  savePalette,
  saveParams,
  saveCustom,
  type CustomPresetMap,
} from './storage';

export function OrbSettingsPanel() {
  const orb = useOrbInstance();
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [palette, setPalette] = useState<PaletteName>('Aurora');
  const [customMap, setCustomMap] = useState<CustomPresetMap>({});
  const [customName, setCustomName] = useState<string>('');
  const [copyLabel, setCopyLabel] = useState<string>('Copy config');
  const hydratedRef = useRef(false);

  // Debounced save of params to localStorage on every change.
  const saveTimerRef = useRef<number | null>(null);
  const scheduleSave = useCallback((p: Record<string, unknown>) => {
    if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => saveParams(p), 250);
  }, []);

  // Hydrate UI + orb once on first mount where orb is available.
  useEffect(() => {
    if (!orb || hydratedRef.current) return;
    hydratedRef.current = true;

    const savedPalette = loadPalette();
    const savedParams = loadParams();

    if (savedPalette && (PALETTE_NAMES as readonly string[]).includes(savedPalette)) {
      setPalette(savedPalette as PaletteName);
      orb.setPreset(savedPalette);
    }
    if (savedParams) {
      for (const [k, v] of Object.entries(savedParams)) orb.setParam(k, v);
    }
    setParams(orb.getParams());
    setCustomMap(loadCustom());
  }, [orb]);

  const applyParam = useCallback(
    (key: string, value: unknown) => {
      orb?.setParam(key, value);
      setParams((prev) => {
        const next = { ...prev, [key]: value };
        scheduleSave(next);
        return next;
      });
    },
    [orb, scheduleSave],
  );

  const onPaletteChange = (name: string) => {
    if (!orb) return;
    setPalette(name as PaletteName);
    orb.setPreset(name);
    setParams(orb.getParams());
    savePalette(name);
    clearParams(); // palette becomes the new baseline
  };

  const onReset = () => {
    if (!orb) return;
    orb.setPreset(palette);
    setParams(orb.getParams());
    clearParams();
    setCustomName('');
  };

  const onRandomize = () => {
    if (!orb) return;
    const next: Record<string, unknown> = {};
    for (const spec of FIELDS) {
      const v = spec.kind === 'color' ? randomHex() : randomSliderValue(spec);
      orb.setParam(spec.key, v);
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
      orb.setPreset(payload.palette);
    }
    for (const [k, v] of Object.entries(payload.params ?? {})) orb.setParam(k, v);
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
      {/* Preset section */}
      <section className="space-y-3">
        <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">Preset</div>

        <div className="space-y-1.5">
          <Label htmlFor="palette" className="text-xs text-zinc-400">Palette</Label>
          <Select value={palette} onValueChange={onPaletteChange}>
            <SelectTrigger id="palette" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PALETTE_NAMES.map((name) => (
                <SelectItem key={name} value={name}>{name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {Object.keys(customMap).length > 0 && (
          <div className="space-y-1.5">
            <Label htmlFor="custom" className="text-xs text-zinc-400">Saved</Label>
            <div className="flex gap-2">
              <Select value={customName || undefined} onValueChange={onLoadCustom}>
                <SelectTrigger id="custom" className="flex-1">
                  <SelectValue placeholder="—" />
                </SelectTrigger>
                <SelectContent>
                  {Object.keys(customMap).sort().map((name) => (
                    <SelectItem key={name} value={name}>{name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant="ghost"
                size="icon"
                onClick={onDeleteCustom}
                disabled={!customName}
                aria-label="Delete saved preset"
                className="h-9 w-9"
              >
                ×
              </Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onSaveAs} disabled={!orb}>Save as…</Button>
          <Button size="sm" variant="ghost" onClick={onRandomize} disabled={!orb}>Randomize</Button>
          <Button size="sm" variant="ghost" onClick={onCopy} disabled={!orb}>{copyLabel}</Button>
          <Button size="sm" variant="ghost" onClick={onReset} disabled={!orb}>Reset to palette</Button>
        </div>
      </section>

      {/* Field sections */}
      {SECTIONS.map((s) => (
        <SettingsSection
          key={s.id}
          title={s.label}
          fields={FIELDS.filter((f) => f.section === s.id)}
          params={params}
          onChange={applyParam}
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
    <section className="space-y-3">
      <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">{title}</div>
      <div className="space-y-3">
        {fields.map((f) => {
          const v = params[f.key];
          return f.kind === 'color' ? (
            <ColorRow key={f.key} field={f} value={String(v ?? '#000000')} onChange={onChange} />
          ) : (
            <SliderRow key={f.key} field={f} value={Number(v ?? f.min)} onChange={onChange} />
          );
        })}
      </div>
    </section>
  );
}

function ColorRow({
  field,
  value,
  onChange,
}: {
  field: FieldSpec & { kind: 'color' };
  value: string;
  onChange: (key: string, value: unknown) => void;
}) {
  const onSwatch = (v: string) => {
    if (!/^#[0-9a-fA-F]{6}$/.test(v)) return;
    onChange(field.key, v.toLowerCase());
  };
  return (
    <div className="space-y-1.5">
      <Label htmlFor={`orb-${field.key}`} className="text-xs text-zinc-400">{field.label}</Label>
      <div className="flex gap-2 items-center">
        <input
          id={`orb-${field.key}`}
          type="color"
          value={value}
          onChange={(e) => onSwatch(e.target.value)}
          className="h-9 w-12 cursor-pointer rounded border border-border bg-transparent"
        />
        <Input
          type="text"
          value={value.toLowerCase()}
          onChange={(e) => onSwatch(e.target.value)}
          className="flex-1 h-9 font-mono text-xs"
        />
      </div>
    </div>
  );
}

function SliderRow({
  field,
  value,
  onChange,
}: {
  field: SliderField;
  value: number;
  onChange: (key: string, value: unknown) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={`orb-${field.key}`} className="text-xs text-zinc-400">{field.label}</Label>
        <span className="font-mono text-xs text-zinc-500">{formatValue(value, field.step)}</span>
      </div>
      <Slider
        id={`orb-${field.key}`}
        min={field.min}
        max={field.max}
        step={field.step}
        value={[value]}
        onValueChange={(vals) => onChange(field.key, vals[0])}
      />
    </div>
  );
}
