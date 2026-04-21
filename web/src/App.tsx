import { useRef } from 'react';
import { TransportStateEnum } from '@pipecat-ai/client-js';
import type { PipecatClient } from '@pipecat-ai/client-js';
import {
  PipecatClientProvider,
  PipecatClientAudio,
  usePipecatClient,
  usePipecatClientTransportState,
} from '@pipecat-ai/client-react';
import { Button } from '@/components/ui/button';
import { Drawer } from '@/components/Drawer';
import { buildClient } from './voice/client';
import { VoiceStateBridge } from './voice/VoiceStateBridge';
import { Slot } from './plugins/PluginHost';
// Side-effect imports — each plugin registers at module load.
import './plugins/orb';
import './plugins/status-chip';
import './plugins/status-pill';
import './plugins/orb-settings';
import './plugins/voice-panel';

function ConnectButton() {
  const client = usePipecatClient();
  const transport = usePipecatClientTransportState();
  const connecting = transport === TransportStateEnum.CONNECTING || transport === TransportStateEnum.AUTHENTICATING;
  const connected = transport === TransportStateEnum.READY || transport === TransportStateEnum.CONNECTED;

  const toggle = async () => {
    if (!client) return;
    try {
      if (connected) await client.disconnect();
      else await client.connect();
    } catch (err) {
      console.error('[voice] connect/disconnect error:', err);
    }
  };

  return (
    <div className="fixed top-4 left-4 z-10">
      <Button onClick={toggle} disabled={!client || connecting} variant="outline" size="sm">
        {connected ? 'Disconnect' : connecting ? 'Connecting…' : 'Connect'}
      </Button>
    </div>
  );
}

function App() {
  const clientRef = useRef<PipecatClient | null>(null);
  if (!clientRef.current) clientRef.current = buildClient();
  return (
    <PipecatClientProvider client={clientRef.current}>
      <VoiceStateBridge />
      <div className="fixed inset-0 overflow-hidden bg-[#0a0a0a]">
        <Slot name="stage" />
        <Slot name="overlay-top" />
        <Slot name="overlay-bottom" />
        <ConnectButton />
        <Drawer />
      </div>
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

export default App;
