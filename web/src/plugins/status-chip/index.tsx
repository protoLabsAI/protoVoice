import { registerPlugin } from '../PluginHost';
import { StatusChip } from './StatusChip';

registerPlugin({
  id: 'status-chip',
  slots: { 'overlay-top': StatusChip },
});
