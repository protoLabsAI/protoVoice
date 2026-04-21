import { SkillSelector } from './SkillSelector';
import { VerbositySelector } from './VerbositySelector';
import { VoiceCloneForm } from './VoiceCloneForm';

export function VoicePanel() {
  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">Agent</div>
        <SkillSelector />
        <VerbositySelector />
      </section>
      <VoiceCloneForm />
    </div>
  );
}
