import { registerPlugin } from '../PluginHost';
import { OrbCanvas } from './OrbCanvas';

/**
 * Orb plugin registration — runs at module import. Importing this file
 * from App.tsx is what wires the orb into the 'stage' slot.
 */
registerPlugin({
  id: 'orb',
  slots: {
    stage: OrbCanvas,
  },
});
