import { useEffect, useState } from 'react';
import { Menu } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Slot } from '@/plugins/PluginHost';

const STORAGE_TAB = 'protoVoice.tab';
type TabName = 'voice' | 'orb';

export function Drawer() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabName>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_TAB);
      if (saved === 'voice' || saved === 'orb') return saved;
    } catch {}
    return 'voice';
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_TAB, tab); } catch {}
  }, [tab]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          className="fixed top-4 right-4 z-20 h-9 w-9"
          aria-label="Open settings drawer"
        >
          <Menu className="h-4 w-4" />
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-[380px] max-w-[90vw] flex flex-col gap-4">
        <SheetHeader className="pb-0">
          <SheetTitle className="font-mono text-sm tracking-wider uppercase text-zinc-400">
            protoVoice
          </SheetTitle>
          <SheetDescription className="sr-only">Voice agent settings and orb visualizer controls.</SheetDescription>
        </SheetHeader>
        <Tabs value={tab} onValueChange={(v) => setTab(v as TabName)} className="flex-1 min-h-0 flex flex-col px-4">
          <TabsList className="grid grid-cols-2 w-full">
            <TabsTrigger value="voice">Voice</TabsTrigger>
            <TabsTrigger value="orb">Orb</TabsTrigger>
          </TabsList>
          <TabsContent value="voice" className="flex-1 min-h-0 overflow-y-auto pt-4 space-y-4">
            <Slot name="drawer-voice" />
          </TabsContent>
          <TabsContent value="orb" className="flex-1 min-h-0 overflow-y-auto pt-4 space-y-4">
            <Slot name="drawer-orb" />
          </TabsContent>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
