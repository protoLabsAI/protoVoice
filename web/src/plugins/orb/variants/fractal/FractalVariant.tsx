import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import { useFrame, useThree } from '@react-three/fiber';
import { FractalMaterial, AtmosphereMaterial } from './materials';
import type { FractalPreset } from './presets';
import { useOrbState } from '../../useOrbState';
import { Envelope, rmsFromAnalyser } from '../../shared/envelope';
import {
  stateSnapshot,
  lerpSnapshot,
  type StateSnapshot,
} from '../../shared/stateSnapshot';
import { clamp01, easeInOutCubic, lerp } from '../../shared/math';
import {
  BREATH_AMP,
  BREATH_HZ_1,
  BREATH_HZ_2,
  CLICK_DECAY,
  DISP_ALPHA,
  DRAG_DAMP,
  DRAG_SENSITIVITY,
  DRAG_VEL_MAX,
  ENV_BOT,
  ENV_USER,
  LISTEN_MIN_DWELL_MS,
  MAX_DELTA_S,
  ROT_WRAP,
  ROTATION_SCALE,
  SPEAK_ENTER,
  SPEAK_EXIT,
  STATE_XFADE_MS,
  THINK_DWELL_MS,
  TIME_WRAP,
  ZOOM_LERP,
  ZOOM_MAX,
  ZOOM_MIN,
} from '../../shared/constants';
import type { VariantProps } from '../registry';
import type { VoiceState } from '../../../../voice/state';

type AnalyserBundle = {
  analyser: AnalyserNode;
  source: MediaStreamAudioSourceNode;
  buf: Uint8Array;
};

/**
 * The fractal variant — full parity with the original imperative
 * VoiceOrb class. Composes a ray-marched sphere + an atmosphere shell,
 * runs the 4-state machine + idle breath + drag-spin + click-bloom +
 * zoom all via useFrame against uniform refs.
 */
export function FractalVariant({ voiceState, botStream, localStream }: VariantProps) {
  const { camera, gl } = useThree();
  const meshRef = useRef<THREE.Mesh>(null);
  const atmosphereRef = useRef<THREE.Mesh>(null);
  const fractalMatRef = useRef<InstanceType<typeof FractalMaterial>>(null);
  const atmosphereMatRef = useRef<InstanceType<typeof AtmosphereMaterial>>(null);

  const { params } = useOrbState();
  const base = params as unknown as FractalPreset;

  // Keep a live ref to base so useFrame always reads the latest without
  // re-binding the frame loop every render.
  const baseRef = useRef<FractalPreset>(base);
  baseRef.current = base;

  // Audio context + envelopes live per-mount — one context per canvas so
  // two canvases (main + preview) don't fight over resumption state.
  const audioRef = useRef<{
    ctx: AudioContext | null;
    bot: AnalyserBundle | null;
    user: AnalyserBundle | null;
    envBot: Envelope;
    envUser: Envelope;
    disp: { bot: number; user: number };
  }>({
    ctx: null,
    bot: null,
    user: null,
    envBot: new Envelope(ENV_BOT),
    envUser: new Envelope(ENV_USER),
    disp: { bot: 0, user: 0 },
  });

  useEffect(() => {
    if (!botStream) return;
    const bundle = attachStream(audioRef.current, botStream, 'bot');
    audioRef.current.bot = bundle;
    return () => {
      try { bundle?.source.disconnect(); } catch {}
      audioRef.current.bot = null;
      audioRef.current.envBot.reset();
    };
  }, [botStream]);

  useEffect(() => {
    if (!localStream) return;
    const bundle = attachStream(audioRef.current, localStream, 'user');
    audioRef.current.user = bundle;
    return () => {
      try { bundle?.source.disconnect(); } catch {}
      audioRef.current.user = null;
      audioRef.current.envUser.reset();
    };
  }, [localStream]);

  useEffect(() => {
    return () => {
      const ctx = audioRef.current.ctx;
      if (ctx) { try { ctx.close(); } catch {} }
      audioRef.current.ctx = null;
    };
  }, []);

  // State machine — target snapshot + crossfade. Stored outside useFrame
  // so the closure sees the latest `voiceState` without re-binding.
  const stateRef = useRef<{
    active: VoiceState;
    snap: StateSnapshot;
    from: StateSnapshot;
    to: StateSnapshot;
    xfadeStart: number;
    xfadeActive: boolean;
    enteredMs: number;
    lastUserSpeechMs: number;
  }>(
    (() => {
      const snap = stateSnapshot('idle', base);
      return {
        active: 'idle',
        snap,
        from: snap,
        to: snap,
        xfadeStart: 0,
        xfadeActive: false,
        enteredMs: performance.now(),
        lastUserSpeechMs: -Infinity,
      };
    })(),
  );

  const beginCrossfade = (next: VoiceState) => {
    const cur = stateRef.current;
    cur.from = {
      ...cur.snap,
      primary: cur.snap.primary.clone(),
      secondary: cur.snap.secondary.clone(),
    };
    cur.to = stateSnapshot(next, baseRef.current);
    cur.xfadeStart = performance.now();
    cur.xfadeActive = true;
  };

  // When the authoritative voiceState prop changes, kick a crossfade.
  useEffect(() => {
    const cur = stateRef.current;
    if (voiceState !== cur.active) {
      cur.active = voiceState;
      cur.enteredMs = performance.now();
      beginCrossfade(voiceState);
    }
  }, [voiceState]);

  // When params (palette, sliders, etc.) change we re-run the crossfade
  // so the state-driven targets pick up the new base.
  useEffect(() => {
    beginCrossfade(stateRef.current.active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    base.primaryEnergy, base.secondaryEnergy, base.density, base.atmosphereGlow,
    base.speed, base.chromaticAberration, base.asymmetry, base.orbRotation,
  ]);

  // Apply immediate (non-state-driven) params directly to uniforms on change.
  useEffect(() => {
    const m = fractalMatRef.current;
    if (!m) return;
    m.uniforms.uFractalIters.value = base.fractalIters;
    m.uniforms.uFractalScale.value = base.fractalScale;
    m.uniforms.uFractalDecay.value = base.fractalDecay;
    m.uniforms.uSmoothness.value   = base.smoothness;
    m.uniforms.uInternalAnim.value = base.internalAnim;
  }, [
    base.fractalIters, base.fractalScale, base.fractalDecay,
    base.smoothness, base.internalAnim,
  ]);

  useEffect(() => {
    const m = atmosphereMatRef.current;
    if (!m) return;
    m.uniforms.uLevel.value = base.atmosphereLevel;
  }, [base.atmosphereLevel]);

  useEffect(() => {
    if (atmosphereRef.current) {
      atmosphereRef.current.scale.setScalar(base.atmosphereScale);
    }
  }, [base.atmosphereScale]);

  useEffect(() => {
    gl.setPixelRatio(base.dpr);
  }, [base.dpr, gl]);

  // Drag-spin state.
  const dragRef = useRef({
    dragging: false,
    holding: false,
    lastX: 0,
    lastY: 0,
    lastT: 0,
    velX: 0,
    velY: 0,
  });

  // Zoom target (camera Z), click bloom state.
  const zoomTargetRef = useRef(13);
  const clickDirRef = useRef(new THREE.Vector3(0, 0, 1));
  const clickStrengthRef = useRef(0);
  const startTimeMsRef = useRef(performance.now());
  const raycaster = useMemo(() => new THREE.Raycaster(), []);
  const ndc = useMemo(() => new THREE.Vector2(), []);
  const scratchCam = useMemo(() => new THREE.Vector3(), []);

  // Raycast a screen-space click to a point on the orb's surface.
  const pulseAt = (clientX: number, clientY: number) => {
    if (!meshRef.current) return;
    ndc.set(
      (clientX / window.innerWidth) * 2 - 1,
      -(clientY / window.innerHeight) * 2 + 1,
    );
    raycaster.setFromCamera(ndc, camera);
    const hits = raycaster.intersectObject(meshRef.current, false);
    if (!hits.length) {
      clickStrengthRef.current = 0;
      return;
    }
    const local = meshRef.current.worldToLocal(hits[0].point.clone()).normalize();
    clickDirRef.current.copy(local);
    clickStrengthRef.current = 1.0;
  };

  // Pointer handlers — bind to the canvas dom element. R3F's onPointerDown
  // on a mesh would raycast per-frame during drag, which is overkill.
  useEffect(() => {
    const dom = gl.domElement;
    dom.style.touchAction = 'none';

    const down = (e: PointerEvent) => {
      dom.setPointerCapture(e.pointerId);
      const d = dragRef.current;
      d.dragging = true;
      d.holding = true;
      d.lastX = e.clientX;
      d.lastY = e.clientY;
      d.lastT = performance.now();
      d.velX = 0;
      d.velY = 0;
      pulseAt(e.clientX, e.clientY);
    };
    const release = () => {
      const d = dragRef.current;
      if (!d.dragging) return;
      d.dragging = false;
      d.holding = false;
      d.velX = 0;
      d.velY = 0;
    };
    const move = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d.dragging) return;
      if (e.buttons === 0) { release(); return; }
      const nowMs = performance.now();
      const dt = Math.max(1, nowMs - d.lastT) / 1000;
      const dx = e.clientX - d.lastX;
      const dy = e.clientY - d.lastY;
      const orb = meshRef.current;
      if (orb) {
        orb.rotation.y += dx * DRAG_SENSITIVITY;
        orb.rotation.x += dy * DRAG_SENSITIVITY;
      }
      const clamp = (v: number, m: number) => Math.max(-m, Math.min(m, v));
      const instVy = clamp((dx * DRAG_SENSITIVITY) / dt, DRAG_VEL_MAX);
      const instVx = clamp((dy * DRAG_SENSITIVITY) / dt, DRAG_VEL_MAX);
      d.velY = lerp(d.velY, instVy, 0.5);
      d.velX = lerp(d.velX, instVx, 0.5);
      d.lastX = e.clientX;
      d.lastY = e.clientY;
      d.lastT = nowMs;
      pulseAt(e.clientX, e.clientY);
    };
    const up = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d.dragging) return;
      d.dragging = false;
      d.holding = false;
      try { dom.releasePointerCapture(e.pointerId); } catch {}
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const step = e.deltaY * 0.001 * Math.max(0.5, zoomTargetRef.current);
      zoomTargetRef.current = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomTargetRef.current + step));
    };
    const onBlur = () => release();

    dom.addEventListener('pointerdown', down);
    dom.addEventListener('pointermove', move);
    dom.addEventListener('pointerup', up);
    dom.addEventListener('pointercancel', up);
    dom.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onBlur);

    return () => {
      dom.removeEventListener('pointerdown', down);
      dom.removeEventListener('pointermove', move);
      dom.removeEventListener('pointerup', up);
      dom.removeEventListener('pointercancel', up);
      dom.removeEventListener('wheel', wheel);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onBlur);
    };
  }, [gl, camera, raycaster, ndc]);

  // The driver loop. Replaces VoiceOrb._tick() — same responsibilities:
  // audio envelopes, state machine (RTVI authoritative; envelope fallback
  // only when no voiceState is driving, which we always have here),
  // crossfade advance, idle breath, uniform composition, rotation, zoom,
  // click-bloom decay, float32 wrap.
  useFrame((_, rawDelta) => {
    const delta = Math.min(rawDelta, MAX_DELTA_S);
    const nowMs = performance.now();
    const audio = audioRef.current;
    const cur = stateRef.current;
    const frac = fractalMatRef.current;
    const atm = atmosphereMatRef.current;
    const mesh = meshRef.current;
    if (!frac || !atm || !mesh) return;

    // Audio envelopes → normalized [0,1] values.
    let bot = 0, user = 0;
    if (audio.bot) {
      const raw = rmsFromAnalyser(audio.bot.analyser, audio.bot.buf);
      bot = audio.envBot.update(raw);
    }
    if (audio.user) {
      const raw = rmsFromAnalyser(audio.user.analyser, audio.user.buf);
      user = audio.envUser.update(raw);
    }
    // Envelope-inferred state for the "bot mic" dwell tracker.
    if (user > (cur.active === 'listening' ? SPEAK_EXIT : SPEAK_ENTER)) {
      cur.lastUserSpeechMs = nowMs;
    }
    if (bot > (cur.active === 'speaking' ? SPEAK_EXIT : SPEAK_ENTER)) {
      // noop — voiceState prop is authoritative. Envelope just tracks.
    }
    if ((nowMs - cur.enteredMs) < LISTEN_MIN_DWELL_MS && cur.active === 'listening') {
      // noop — honored by the state hook outside useFrame.
    }
    if (nowMs - cur.lastUserSpeechMs < THINK_DWELL_MS) {
      // noop — handled by the voice bridge.
    }

    // Advance state crossfade.
    if (cur.xfadeActive) {
      const t = clamp01((nowMs - cur.xfadeStart) / STATE_XFADE_MS);
      const e = easeInOutCubic(t);
      cur.snap = lerpSnapshot(cur.from, cur.to, e);
      if (t >= 1) {
        cur.xfadeActive = false;
        cur.snap = cur.to;
      }
    }

    // Idle breath — two sines.
    const tSec = (nowMs - startTimeMsRef.current) / 1000;
    const breath = Math.sin(tSec * Math.PI * 2 * BREATH_HZ_1)
                 + 0.5 * Math.sin(tSec * Math.PI * 2 * BREATH_HZ_2);
    const breathNorm = breath * 0.5;

    // Display smoothing.
    audio.disp.bot  = lerp(audio.disp.bot,  bot,  DISP_ALPHA);
    audio.disp.user = lerp(audio.disp.user, user, DISP_ALPHA);
    const dBot  = audio.disp.bot;
    const dUser = audio.disp.user;

    // Compose uniforms: state-base + audio modulation.
    const s = cur.snap;
    frac.uniforms.uDensity.value   = s.density + dBot * 0.9;
    frac.uniforms.uAsymmetry.value = clamp01(s.asymmetry + dUser * 0.06);
    frac.uniforms.uPrimaryColor.value.copy(s.primary);
    frac.uniforms.uSecondaryColor.value.copy(s.secondary);
    frac.uniforms.uClickDir.value.copy(clickDirRef.current);
    frac.uniforms.uClickStrength.value = clickStrengthRef.current;
    atm.uniforms.uColor.value.copy(s.primary);
    atm.uniforms.uColorSecondary.value.copy(s.secondary);
    atm.uniforms.uGlow.value = s.glow + dBot * 1.1 + dUser * 0.35;
    atm.uniforms.uClickDir.value.copy(clickDirRef.current);
    atm.uniforms.uClickStrength.value = clickStrengthRef.current;

    // Scale — state × breath × gentle audio pump.
    const scale = s.scale * (1 + breathNorm * BREATH_AMP) * (1 + dBot * 0.06);
    mesh.scale.setScalar(scale);

    // Time + rotation — integrate by delta. Drag velocity layered on top.
    frac.uniforms.uTime.value += delta * s.speed;
    mesh.rotation.y += delta * s.rotation * ROTATION_SCALE + dragRef.current.velY * delta;
    mesh.rotation.x += delta * (s.rotation * 0.5) * ROTATION_SCALE + dragRef.current.velX * delta;

    // Float32 wrap.
    if (frac.uniforms.uTime.value > TIME_WRAP) frac.uniforms.uTime.value -= TIME_WRAP;
    if (mesh.rotation.y > ROT_WRAP)  mesh.rotation.y -= ROT_WRAP;
    if (mesh.rotation.y < -ROT_WRAP) mesh.rotation.y += ROT_WRAP;
    if (mesh.rotation.x > ROT_WRAP)  mesh.rotation.x -= ROT_WRAP;
    if (mesh.rotation.x < -ROT_WRAP) mesh.rotation.x += ROT_WRAP;

    // Drag momentum decay when the user isn't holding.
    const d = dragRef.current;
    if (!d.dragging) {
      d.velX *= DRAG_DAMP;
      d.velY *= DRAG_DAMP;
      if (Math.abs(d.velX) < 0.001) d.velX = 0;
      if (Math.abs(d.velY) < 0.001) d.velY = 0;
    }

    // Click-bloom decay — only when pointer is not held.
    if (!d.holding && clickStrengthRef.current > 0) {
      clickStrengthRef.current *= CLICK_DECAY;
      if (clickStrengthRef.current < 0.01) clickStrengthRef.current = 0;
    }

    // Zoom ease.
    camera.position.z = lerp(camera.position.z, zoomTargetRef.current, ZOOM_LERP);

    // Local camera position uniform — required for the raymarch ray origin.
    mesh.updateMatrixWorld();
    scratchCam.copy(camera.position);
    mesh.worldToLocal(scratchCam);
    frac.uniforms.uLocalCamPos.value.copy(scratchCam);
  });

  // Initial camera position (set once on mount). Zoom target drives ongoing.
  useEffect(() => {
    camera.position.set(0, 0, 13);
    zoomTargetRef.current = 13;
  }, [camera]);

  // Shared geometry — same sphere for orb + atmosphere, matches original.
  const geometry = useMemo(() => new THREE.SphereGeometry(2, 128, 128), []);
  useEffect(() => () => geometry.dispose(), [geometry]);

  return (
    <mesh ref={meshRef} geometry={geometry}>
      <fractalMaterial
        ref={fractalMatRef}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        attach="material"
      />
      <mesh
        ref={atmosphereRef}
        geometry={geometry}
        scale={[base.atmosphereScale, base.atmosphereScale, base.atmosphereScale]}
      >
        <atmosphereMaterial
          ref={atmosphereMatRef}
          transparent
          side={THREE.FrontSide}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          attach="material"
        />
      </mesh>
    </mesh>
  );
}

function attachStream(
  audio: { ctx: AudioContext | null },
  stream: MediaStream,
  _kind: 'bot' | 'user',
): AnalyserBundle | null {
  if (!audio.ctx) {
    try {
      const Ctx: typeof AudioContext =
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (window.AudioContext ?? (window as any).webkitAudioContext);
      audio.ctx = new Ctx();
    } catch {
      return null;
    }
  }
  const ctx = audio.ctx!;
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  analyser.smoothingTimeConstant = 0.55;
  source.connect(analyser);
  const buf = new Uint8Array(analyser.fftSize);
  return { analyser, source, buf };
}
