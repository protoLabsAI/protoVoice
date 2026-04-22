import { useEffect, useRef } from 'react';
import { VoiceOrb } from './viz';
import { orbInstance } from './instance';
import { loadPalette, loadParams } from './storage';
import {
  usePipecatClient,
  usePipecatClientMediaTrack,
  usePipecatClientTransportState,
} from '@pipecat-ai/client-react';
import { useVoiceStateSelector } from '../../voice/hooks';
import type { VoiceState } from '../../voice/state';

const BUILTIN_PALETTES = new Set(['Aurora', 'Ember', 'Citrus', 'Forest', 'Noir']);

/**
 * Imperative Three.js mount wrapped in a React component.
 *
 * The `VoiceOrb` class (ported verbatim from static/viz.js) owns a
 * canvas inside the container div we hand it. We:
 *   1. instantiate once on mount (guard StrictMode double-invoke)
 *   2. attach the bot's audio track as reactive input
 *   3. drive state transitions from the derived voice store
 *   4. tear down on unmount
 */
export function OrbCanvas() {
  const hostRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<VoiceOrb | null>(null);

  const client = usePipecatClient();
  const transport = usePipecatClientTransportState();
  const botTrack = usePipecatClientMediaTrack('audio', 'bot');
  const localTrack = usePipecatClientMediaTrack('audio', 'local');
  const state: VoiceState = useVoiceStateSelector((s) => s.state);

  const onDoubleClick = () => {
    if (!client) return;
    const disconnected = transport === 'disconnected' || transport === 'initialized' || transport === 'error';
    if (disconnected) client.connect().catch((err) => console.error('[orb] connect error:', err));
  };

  // Instantiate once, clean up on unmount. StrictMode invokes the effect
  // twice in dev — the cleaned sentinel prevents double-init.
  useEffect(() => {
    if (!hostRef.current) return;
    let cleaned = false;
    // Hydrate palette + params from localStorage BEFORE construction so the
    // first rendered frame matches what the user had last. Settings panel
    // (mounted only when drawer opens) used to own this, which meant the
    // saved style wouldn't apply until the drawer was opened at least once.
    const savedPalette = loadPalette();
    const initialPreset = savedPalette && BUILTIN_PALETTES.has(savedPalette) ? savedPalette : 'Aurora';
    const orb = new VoiceOrb(hostRef.current, { preset: initialPreset });
    const savedParams = loadParams();
    if (savedParams) {
      for (const [k, v] of Object.entries(savedParams)) orb.setParam(k, v);
    }
    orbRef.current = orb;
    orbInstance.set(orb);
    // Expose for dev-console tinkering, same as static/index.html did.
    (window as unknown as { __orb: VoiceOrb }).__orb = orb;
    return () => {
      if (cleaned) return;
      cleaned = true;
      orb.dispose?.();
      orbRef.current = null;
      orbInstance.set(null);
    };
  }, []);

  // Pipe the bot's audio track into the orb for level-reactive bloom.
  // VoiceOrb kind names are 'remote' (bot) / 'local' (user mic).
  useEffect(() => {
    if (!orbRef.current || !botTrack) return;
    const stream = new MediaStream([botTrack]);
    orbRef.current.attachStream?.(stream, 'remote');
  }, [botTrack]);

  useEffect(() => {
    if (!orbRef.current || !localTrack) return;
    const stream = new MediaStream([localTrack]);
    orbRef.current.attachStream?.(stream, 'local');
  }, [localTrack]);

  // Drive the 4-state machine from RTVI-derived voiceStore.
  useEffect(() => {
    orbRef.current?.setState?.(state);
  }, [state]);

  return (
    <div
      ref={hostRef}
      onDoubleClick={onDoubleClick}
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
      }}
    />
  );
}
