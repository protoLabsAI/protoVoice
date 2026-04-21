import { Badge } from '@/components/ui/badge';
import { useVoiceStateSelector } from '@/voice/hooks';
import { usePipecatClientTransportState } from '@pipecat-ai/client-react';
import type { VoiceState } from '@/voice/state';

const STATE_STYLE: Record<VoiceState, { label: string; className: string }> = {
  idle:      { label: 'idle',      className: 'bg-zinc-800 text-zinc-300 border-zinc-700' },
  listening: { label: 'listening', className: 'bg-emerald-950 text-emerald-200 border-emerald-800' },
  thinking:  { label: 'thinking',  className: 'bg-amber-950 text-amber-200 border-amber-800' },
  speaking:  { label: 'speaking',  className: 'bg-sky-950 text-sky-200 border-sky-800' },
};

export function StatusChip() {
  const state = useVoiceStateSelector((s) => s.state);
  const transport = usePipecatClientTransportState();
  const connected = transport === 'ready' || transport === 'connected';

  const shown = connected ? STATE_STYLE[state] : { label: transport, className: 'bg-zinc-900 text-zinc-400 border-zinc-800' };

  return (
    <div className="pointer-events-none fixed top-4 right-16 z-10">
      <Badge
        variant="outline"
        className={`font-mono text-[11px] tracking-wide uppercase transition-colors ${shown.className}`}
      >
        {shown.label}
      </Badge>
    </div>
  );
}
