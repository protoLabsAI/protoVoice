// Voice-reactive fractal orb visualizer.
//
// Shader + base fractal logic by sabosugi (https://codepen.io/sabosugi/pen/EagJwmv).
// Kept verbatim; we wrap it in a class and drive the uniforms from two AnalyserNodes
// (local mic + remote bot audio) so the orb pumps with the voice session.

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

// --- Presets (unchanged from the pen) ---------------------------------------
export const PRESETS = {
  Default: {
    primaryEnergy: '#00b3ff', secondaryEnergy: '#2e9aff', speed: 0.5, density: 3.0, dpr: 0.7,
    atmosphereGlow: 0.15, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.89,
    internalAnim: 0.43, fractalIters: 4, fractalScale: 0.97, fractalDecay: -16.7,
    smoothness: 0.031, asymmetry: 0.55, chromaticAberration: 0.025,
  },
  Cyan: {
    primaryEnergy: '#00ffee', secondaryEnergy: '#9900ff', speed: 0.5, density: 1.1, dpr: 0.7,
    atmosphereGlow: 0.15, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.89,
    internalAnim: 0.43, fractalIters: 3, fractalScale: 0.75, fractalDecay: -16.7,
    smoothness: 0.05, asymmetry: 0.45, chromaticAberration: 0.026,
  },
  Gray: {
    primaryEnergy: '#ffffff', secondaryEnergy: '#000000', speed: 0.3, density: 0.9, dpr: 0.7,
    atmosphereGlow: 0.15, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.46,
    internalAnim: 0.17, fractalIters: 4, fractalScale: 0.74, fractalDecay: -21.6,
    smoothness: 0.036, asymmetry: 0.0, chromaticAberration: 0.017,
  },
  Yellow: {
    primaryEnergy: '#ffbb00', secondaryEnergy: '#2eff9d', speed: 0.5, density: 2.1, dpr: 0.7,
    atmosphereGlow: 0.15, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.53,
    internalAnim: 0.43, fractalIters: 3, fractalScale: 0.69, fractalDecay: -14.5,
    smoothness: 0.008, asymmetry: 0.35, chromaticAberration: 0.024,
  },
  Green: {
    primaryEnergy: '#44ff00', secondaryEnergy: '#0062ff', speed: 1.1, density: 1.3, dpr: 0.7,
    atmosphereGlow: 0.15, atmosphereLevel: 1.0, atmosphereScale: 1.03, orbRotation: 0.56,
    internalAnim: 0.4, fractalIters: 4, fractalScale: 0.89, fractalDecay: -24.3,
    smoothness: 0.081, asymmetry: 0.26, chromaticAberration: 0.0,
  },
};

// --- Shaders (verbatim from the pen) ----------------------------------------
const vertexShader = /* glsl */ `
  varying vec3 vLocalPosition;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vLocalPosition = position;
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const fragmentShader = /* glsl */ `
  uniform float uTime;
  uniform vec3 uLocalCamPos;
  uniform vec3 uPrimaryColor;
  uniform vec3 uSecondaryColor;
  uniform float uDensity;
  uniform float uFractalIters;
  uniform float uFractalScale;
  uniform float uFractalDecay;
  uniform float uInternalAnim;
  uniform float uSmoothness;
  uniform float uAsymmetry;
  uniform float uAtmosphereGlow;
  varying vec3 vLocalPosition;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  float evaluateStructure(vec3 pos) {
    float densityAcc = 0.0;
    vec3 anchor = pos;
    float animTime = uTime * uInternalAnim;
    float s = sin(animTime);
    float c = cos(animTime);
    mat2 rotAnim = mat2(c, s, -s, c);
    float a = 0.5 * uAsymmetry;
    mat2 rotAsym1 = mat2(cos(a), sin(a), -sin(a), cos(a));
    float b = 0.3 * uAsymmetry;
    mat2 rotAsym2 = mat2(cos(b), sin(b), -sin(b), cos(b));
    for (int step = 0; step < 12; ++step) {
      if (float(step) >= uFractalIters) break;
      pos.xy *= rotAnim;
      pos.yz *= rotAnim;
      pos.xz *= rotAsym1;
      pos.yz *= rotAsym2;
      pos += vec3(0.05, -0.02, 0.03) * uAsymmetry;
      vec3 foldedPos = sqrt(pos * pos + uSmoothness);
      float magnitudeSq = dot(foldedPos, foldedPos);
      magnitudeSq = max(magnitudeSq, 0.00001);
      pos = (uFractalScale * foldedPos / magnitudeSq) - uFractalScale;
      float ySq = pos.y * pos.y;
      float zSq = pos.z * pos.z;
      float yz2 = 2.0 * pos.y * pos.z;
      pos.yz = vec2(ySq - zSq, yz2);
      pos = vec3(pos.z, pos.x, pos.y);
      densityAcc += exp(uFractalDecay * abs(dot(pos, anchor)));
    }
    return densityAcc * 0.5;
  }

  vec2 getVolumeBounds(vec3 origin, vec3 dir, float radius) {
    float b = dot(origin, dir);
    float c = dot(origin, origin) - radius * radius;
    float discriminant = b * b - c;
    if (discriminant < 0.0) return vec2(-1.0);
    float root = sqrt(discriminant);
    return vec2(-b - root, -b + root);
  }

  vec3 traceEnergy(vec3 origin, vec3 dir, vec2 limits) {
    float currentDepth = limits.x;
    float marchStep = 0.02;
    vec3 finalEnergy = vec3(0.0);
    float fieldVal = 0.0;
    for (int i = 0; i < 64; i++) {
      currentDepth += marchStep * exp(-2.0 * fieldVal);
      if (currentDepth > limits.y) break;
      vec3 samplePoint = origin + currentDepth * dir;
      fieldVal = evaluateStructure(samplePoint);
      float vSq = fieldVal * fieldVal;
      float gradientBlend = smoothstep(0.0, 0.4, fieldVal);
      vec3 currentGradient = mix(uSecondaryColor, uPrimaryColor, gradientBlend);
      vec3 emission = currentGradient * (fieldVal * 1.8 + vSq * 1.0);
      finalEnergy = 0.99 * finalEnergy + (0.08 * uDensity) * emission;
    }
    return finalEnergy;
  }

  void main() {
    vec3 rayOrig = uLocalCamPos;
    vec3 rayDir = normalize(vLocalPosition - uLocalCamPos);
    float t = uTime * 0.1;
    float s = sin(t);
    float c = cos(t);
    mat2 rotXZ = mat2(c, s, -s, c);
    rayOrig.xz *= rotXZ;
    rayDir.xz *= rotXZ;
    vec2 limits = getVolumeBounds(rayOrig, rayDir, 2.0);
    if (limits.x < 0.0) discard;
    vec3 volumeColor = traceEnergy(rayOrig, rayDir, limits);
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float facingRatio = max(dot(normal, viewDir), 0.0);
    float edgeAA = smoothstep(0.0, 0.05, facingRatio);
    vec3 finalColor = 0.5 * log(1.0 + volumeColor);
    finalColor = clamp(finalColor, 0.0, 1.0);
    finalColor *= edgeAA;
    float maxLuma = max(finalColor.r, max(finalColor.g, finalColor.b));
    float alpha = clamp(maxLuma * 1.5, 0.0, 1.0) * edgeAA;
    gl_FragColor = vec4(finalColor, alpha);
  }
`;

const atmosphereVertexShader = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const atmosphereFragmentShader = /* glsl */ `
  uniform vec3 uColor;
  uniform float uGlow;
  uniform float uLevel;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float vdn = max(dot(normal, viewDir), 0.0);
    float edgeFade = smoothstep(0.0, 0.15, vdn);
    float innerFadePoint = clamp(1.0 - uLevel, 0.0, 0.99);
    float centerFade = smoothstep(1.0, innerFadePoint, vdn);
    float alpha = edgeFade * centerFade * uGlow;
    gl_FragColor = vec4(uColor, alpha);
  }
`;

const ChromaticAberrationShader = {
  uniforms: { tDiffuse: { value: null }, uAmount: { value: 0.025 } },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform float uAmount;
    varying vec2 vUv;
    void main() {
      vec4 baseColor = texture2D(tDiffuse, vUv);
      float luma = max(baseColor.r, max(baseColor.g, baseColor.b));
      float mask = smoothstep(0.01, 0.1, luma);
      vec2 offset = (vUv - 0.5) * uAmount;
      float r = texture2D(tDiffuse, vUv + offset).r;
      float g = texture2D(tDiffuse, vUv).g;
      float b = texture2D(tDiffuse, vUv - offset).b;
      vec3 aberratedColor = vec3(r, g, b);
      gl_FragColor = vec4(mix(baseColor.rgb, aberratedColor, mask), 1.0);
    }
  `,
};

// --- Helpers ----------------------------------------------------------------
const lerp = (a, b, t) => a + (b - a) * t;

function rmsFromAnalyser(analyser, buf) {
  analyser.getByteTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = (buf[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / buf.length);
}

// --- Main class -------------------------------------------------------------
export class VoiceOrb {
  constructor(container, { preset = 'Default' } = {}) {
    this.container = container;
    this.basePreset = { ...PRESETS[preset] };
    this.params = { ...this.basePreset };

    // Scene
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x000000);
    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 100);
    this.camera.position.set(0, 0, 6);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.setPixelRatio(this.params.dpr);
    container.appendChild(this.renderer.domElement);

    // Uniforms
    this.uniforms = {
      uTime: { value: 0 },
      uLocalCamPos: { value: new THREE.Vector3() },
      uPrimaryColor: { value: new THREE.Color(this.params.primaryEnergy) },
      uSecondaryColor: { value: new THREE.Color(this.params.secondaryEnergy) },
      uDensity: { value: this.params.density },
      uFractalIters: { value: this.params.fractalIters },
      uFractalScale: { value: this.params.fractalScale },
      uFractalDecay: { value: this.params.fractalDecay },
      uInternalAnim: { value: this.params.internalAnim },
      uSmoothness: { value: this.params.smoothness },
      uAsymmetry: { value: this.params.asymmetry },
    };

    const material = new THREE.ShaderMaterial({
      vertexShader, fragmentShader, uniforms: this.uniforms,
      transparent: true, side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.atmosphereUniforms = {
      uColor: { value: new THREE.Color(this.params.primaryEnergy) },
      uGlow: { value: this.params.atmosphereGlow },
      uLevel: { value: this.params.atmosphereLevel },
    };
    const atmosphereMaterial = new THREE.ShaderMaterial({
      vertexShader: atmosphereVertexShader,
      fragmentShader: atmosphereFragmentShader,
      uniforms: this.atmosphereUniforms,
      transparent: true, side: THREE.FrontSide, depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const geometry = new THREE.SphereGeometry(2.0, 128, 128);
    this.orb = new THREE.Mesh(geometry, material);
    this.scene.add(this.orb);
    this.atmosphereMesh = new THREE.Mesh(geometry, atmosphereMaterial);
    this.atmosphereMesh.scale.setScalar(this.params.atmosphereScale);
    this.orb.add(this.atmosphereMesh);

    // Post
    this.composer = new EffectComposer(this.renderer);
    this.composer.setPixelRatio(this.params.dpr);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.caPass = new ShaderPass(ChromaticAberrationShader);
    this.caPass.uniforms.uAmount.value = this.params.chromaticAberration;
    this.composer.addPass(this.caPass);

    // Audio analysers — created lazily when a stream is attached.
    this._audioCtx = null;
    this._analysers = { local: null, remote: null };
    this._bufs = { local: null, remote: null };
    this._rms = { local: 0, remote: 0 };

    // Drive target — what we animate the uniforms toward each frame.
    this._target = { ...this.basePreset };

    this.clock = new THREE.Clock();
    this._onResize = this._onResize.bind(this);
    window.addEventListener('resize', this._onResize);

    this._running = true;
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
  }

  _onResize() {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
  }

  setPreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    this.basePreset = { ...p };
    Object.assign(this._target, p);
    // Swap immediately for values that don't need smoothing.
    this.uniforms.uFractalIters.value = p.fractalIters;
    this.renderer.setPixelRatio(p.dpr);
    this.composer.setPixelRatio(p.dpr);
  }

  /**
   * Hook an audio stream for level analysis.
   * @param {MediaStream} stream
   * @param {'local'|'remote'} kind — 'local' = user mic, 'remote' = bot audio
   */
  attachStream(stream, kind) {
    if (!this._audioCtx) this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // Safari/Chrome sometimes suspend the context until user gesture.
    if (this._audioCtx.state === 'suspended') this._audioCtx.resume().catch(() => {});
    this.detachStream(kind);
    const source = this._audioCtx.createMediaStreamSource(stream);
    const analyser = this._audioCtx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.7;
    source.connect(analyser);
    // Intentionally NOT connected to destination — playback happens elsewhere.
    this._analysers[kind] = { source, analyser };
    this._bufs[kind] = new Uint8Array(analyser.fftSize);
  }

  detachStream(kind) {
    const a = this._analysers[kind];
    if (!a) return;
    try { a.source.disconnect(); } catch (_) {}
    this._analysers[kind] = null;
    this._bufs[kind] = null;
    this._rms[kind] = 0;
  }

  destroy() {
    this._running = false;
    window.removeEventListener('resize', this._onResize);
    this.detachStream('local');
    this.detachStream('remote');
    if (this._audioCtx) { try { this._audioCtx.close(); } catch (_) {} this._audioCtx = null; }
    this.renderer.dispose();
    if (this.renderer.domElement.parentNode) {
      this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
    }
  }

  _tick() {
    if (!this._running) return;
    requestAnimationFrame(this._tick);

    const delta = this.clock.getDelta();

    // Read audio levels.
    for (const kind of ['local', 'remote']) {
      const a = this._analysers[kind];
      if (a) this._rms[kind] = rmsFromAnalyser(a.analyser, this._bufs[kind]);
    }
    const bot = this._rms.remote;
    const user = this._rms.local;

    // Map levels onto target uniforms, modulating on top of the base preset.
    const base = this.basePreset;
    this._target.density           = base.density           + bot * 4.0 + user * 1.0;
    this._target.atmosphereGlow    = base.atmosphereGlow    + bot * 2.5;
    this._target.speed             = base.speed             * (1.0 + bot * 1.5);
    this._target.chromaticAberration = Math.min(0.05, base.chromaticAberration + bot * 0.03);
    this._target.asymmetry         = Math.min(1.0, base.asymmetry + user * 0.4);
    this._target.internalAnim      = base.internalAnim * (1.0 + bot * 1.0 + user * 0.3);
    this._target.orbRotation       = base.orbRotation * (1.0 + bot * 0.5);

    // Lerp the params toward the targets — fast attack, slower release.
    const attack = 0.35;
    const release = 0.08;
    const approach = (key) => {
      const t = this._target[key];
      const v = this.params[key];
      const k = t > v ? attack : release;
      this.params[key] = lerp(v, t, k);
    };
    for (const key of ['density', 'atmosphereGlow', 'speed', 'chromaticAberration',
                       'asymmetry', 'internalAnim', 'orbRotation']) {
      approach(key);
    }

    // Push params into uniforms.
    this.uniforms.uDensity.value           = this.params.density;
    this.uniforms.uAsymmetry.value         = this.params.asymmetry;
    this.uniforms.uInternalAnim.value      = this.params.internalAnim;
    this.atmosphereUniforms.uGlow.value    = this.params.atmosphereGlow;
    this.caPass.uniforms.uAmount.value     = this.params.chromaticAberration;

    // Animate.
    this.uniforms.uTime.value += delta * this.params.speed;
    this.orb.rotation.y += delta * this.params.orbRotation;
    this.orb.rotation.x += delta * (this.params.orbRotation * 0.5);

    this.orb.updateMatrixWorld();
    const localCam = new THREE.Vector3().copy(this.camera.position);
    this.orb.worldToLocal(localCam);
    this.uniforms.uLocalCamPos.value.copy(localCam);

    this.composer.render();
  }
}
