import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { api } from '@/lib/api';

type Status = 'idle' | 'uploading' | 'done' | 'error';

export function VoiceCloneForm() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [slug, setSlug] = useState('');
  const [name, setName] = useState('');
  const [transcript, setTranscript] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setStatus('error');
      setMessage('pick an audio file first');
      return;
    }
    if (!slug.trim()) {
      setStatus('error');
      setMessage('slug is required');
      return;
    }
    setStatus('uploading');
    setMessage('uploading — auto-transcribe may take a few seconds…');
    try {
      const r = await api.cloneVoice({
        slug: slug.trim().toLowerCase(),
        audio: file,
        name: name.trim() || undefined,
        transcript: transcript.trim() || undefined,
      });
      if (r.error) {
        setStatus('error');
        setMessage(r.error);
        return;
      }
      setStatus('done');
      setMessage(
        r.auto_transcribed
          ? `saved. auto-transcript: "${r.transcript}"`
          : 'saved.',
      );
      setSlug('');
      setName('');
      setTranscript('');
      if (fileRef.current) fileRef.current.value = '';
    } catch (err) {
      setStatus('error');
      setMessage(String((err as Error).message ?? err));
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">Voice clone</div>

      <div className="space-y-1.5">
        <Label htmlFor="clone-slug" className="text-xs text-zinc-400">Slug <span className="text-zinc-600">(lowercase, a-z0-9-_)</span></Label>
        <Input id="clone-slug" value={slug} onChange={(e) => setSlug(e.target.value)} required />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="clone-name" className="text-xs text-zinc-400">Display name <span className="text-zinc-600">(optional)</span></Label>
        <Input id="clone-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="clone-file" className="text-xs text-zinc-400">Audio sample</Label>
        <input
          id="clone-file"
          ref={fileRef}
          type="file"
          accept="audio/*"
          className="w-full text-xs text-zinc-300 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border file:border-border file:bg-secondary file:text-secondary-foreground file:cursor-pointer"
          required
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="clone-transcript" className="text-xs text-zinc-400">Transcript <span className="text-zinc-600">(optional — Whisper will auto-transcribe if blank)</span></Label>
        <Input id="clone-transcript" value={transcript} onChange={(e) => setTranscript(e.target.value)} />
      </div>

      <Button type="submit" size="sm" disabled={status === 'uploading'}>
        {status === 'uploading' ? 'Uploading…' : 'Clone voice'}
      </Button>

      {message && (
        <div className={`text-xs ${status === 'error' ? 'text-red-400' : 'text-zinc-400'}`}>
          {message}
        </div>
      )}
    </form>
  );
}
