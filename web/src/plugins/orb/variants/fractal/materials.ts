import * as THREE from 'three';
import { shaderMaterial } from '@react-three/drei';
import { extend } from '@react-three/fiber';
import fractalVert from './shaders/fractal.vert.glsl';
import fractalFrag from './shaders/fractal.frag.glsl';
import atmosphereVert from './shaders/atmosphere.vert.glsl';
import atmosphereFrag from './shaders/atmosphere.frag.glsl';

/**
 * Fractal core material. Ray-marched volumetric fractal inside a
 * sphere; uniforms drive density, iterations, color, click bloom, etc.
 */
export const FractalMaterial = shaderMaterial(
  {
    uTime: 0,
    uLocalCamPos: new THREE.Vector3(),
    uPrimaryColor: new THREE.Color('#0ea5e9'),
    uSecondaryColor: new THREE.Color('#f472b6'),
    uDensity: 2.4,
    uFractalIters: 4,
    uFractalScale: 0.85,
    uFractalDecay: -17.0,
    uInternalAnim: 0.42,
    uSmoothness: 0.032,
    uAsymmetry: 0.5,
    uAtmosphereGlow: 0.18,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  fractalVert,
  fractalFrag,
);

/**
 * Atmosphere shell material. Renders the halo ring around the fractal;
 * shared click bloom with the core material.
 */
export const AtmosphereMaterial = shaderMaterial(
  {
    uColor: new THREE.Color('#0ea5e9'),
    uColorSecondary: new THREE.Color('#f472b6'),
    uGlow: 0.18,
    uLevel: 1.0,
    uClickDir: new THREE.Vector3(0, 0, 1),
    uClickStrength: 0,
  },
  atmosphereVert,
  atmosphereFrag,
);

extend({ FractalMaterial, AtmosphereMaterial });

// JSX intrinsic type declarations. Lets us use <fractalMaterial ... /> in TSX.
declare module '@react-three/fiber' {
  // Augment r3f's ThreeElements so our custom classes appear as JSX tags.
  interface ThreeElements {
    fractalMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
    atmosphereMaterial: import('@react-three/fiber').ThreeElements['shaderMaterial'];
  }
}
