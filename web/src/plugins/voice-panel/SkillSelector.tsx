import { useEffect, useState } from 'react';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api, type Skill } from '@/lib/api';

export function SkillSelector() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [active, setActive] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await api.skills();
      setSkills(r.skills);
      setActive(r.active);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  useEffect(() => { load(); }, []);

  const onChange = async (slug: string) => {
    setActive(slug);
    try {
      const r = await api.setSkill(slug);
      if (r.error) setError(r.error);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  return (
    <div className="space-y-1.5">
      <Label htmlFor="skill" className="text-xs text-zinc-400">Skill</Label>
      <Select value={active || undefined} onValueChange={onChange}>
        <SelectTrigger id="skill" className="w-full">
          <SelectValue placeholder="—" />
        </SelectTrigger>
        <SelectContent>
          {skills.map((s) => (
            <SelectItem key={s.slug} value={s.slug}>{s.name ?? s.slug}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}
