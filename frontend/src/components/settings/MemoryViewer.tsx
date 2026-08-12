import { useEffect, useState, useRef } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { MemoryEntry } from '../../types';

const TYPE_LABELS: Record<string, string> = {
  user_fact: 'User Fact',
  user_preference: 'User Preference',
  important_event: 'Important Event',
};

function groupByType(memories: MemoryEntry[]): Map<string, MemoryEntry[]> {
  const groups = new Map<string, MemoryEntry[]>();
  for (const m of memories) {
    const list = groups.get(m.memory_type) || [];
    list.push(m);
    groups.set(m.memory_type, list);
  }
  return groups;
}

function downloadFile(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function MemoryViewer() {
  const showMemoryViewer = useChatStore((s) => s.showMemoryViewer);
  const setShowMemoryViewer = useChatStore((s) => s.setShowMemoryViewer);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { t } = useTranslation();
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (showMemoryViewer) {
      setLoading(true);
      api
        .fetchMemories(activeCharacter?.id)
        .then((data) => setMemories(data.memories))
        .catch(() => setMemories([]))
        .finally(() => setLoading(false));
    }
  }, [showMemoryViewer, activeCharacter?.id]);

  if (!showMemoryViewer) return null;

  const grouped = groupByType(memories);

  const handleExportJSON = () => {
    const data = memories.map((m) => ({
      id: m.id,
      content: m.content,
      type: m.memory_type,
      importance: m.importance,
      access_count: m.access_count,
      created_at: m.created_at,
      last_accessed_at: m.last_accessed_at,
    }));
    downloadFile(
      JSON.stringify(data, null, 2),
      `memories-${new Date().toISOString().slice(0, 10)}.json`,
      'application/json',
    );
  };

  const handleExportTXT = () => {
    const lines: string[] = [];
    for (const [type, items] of grouped) {
      lines.push(`=== ${t(TYPE_LABELS[type] || type)} ===`);
      for (const m of items) {
        lines.push(`[${m.importance}/10] ${m.content}`);
      }
      lines.push('');
    }
    downloadFile(
      lines.join('\n'),
      `memories-${new Date().toISOString().slice(0, 10)}.txt`,
      'text/plain;charset=utf-8',
    );
  };

  return (
    <div
      className="no-drag fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={() => setShowMemoryViewer(false)}
    >
      <div
        className="max-h-[85vh] w-[520px] flex flex-col overflow-hidden rounded-2xl border border-white/10 bg-companion-overlay-strong shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={t('Memories')}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
          <h2 className="text-base font-semibold text-white">
            {t('Memories')}{' '}
            <span className="text-xs text-white/40">({memories.length})</span>
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={handleExportJSON}
              className="rounded-md border border-white/15 bg-white/5 px-2.5 py-1 text-[11px] text-white/60 transition-colors hover:text-white/90"
            >
              {t('Export JSON')}
            </button>
            <button
              onClick={handleExportTXT}
              className="rounded-md border border-white/15 bg-white/5 px-2.5 py-1 text-[11px] text-white/60 transition-colors hover:text-white/90"
            >
              {t('Export TXT')}
            </button>
            <button
              onClick={() => setShowMemoryViewer(false)}
              className="ml-1 text-white/50 transition-colors hover:text-white"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="py-8 text-center text-sm text-white/40">
              {t('Checking')}
            </div>
          )}

          {!loading && memories.length === 0 && (
            <div className="py-10 text-center text-sm text-white/40">
              {t('No memories yet')}
            </div>
          )}

          {!loading &&
            [...grouped].map(([type, items]) => (
              <div key={type} className="mb-5">
                <div className="mb-2 flex items-center gap-2">
                  <span className="rounded bg-companion-accent/20 px-2 py-0.5 text-[11px] text-companion-accent">
                    {t(TYPE_LABELS[type] || type)}
                  </span>
                  <span className="text-[10px] text-white/30">{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((m) => (
                    <div
                      key={m.id}
                      className="rounded-lg border border-white/10 bg-white/5 px-3 py-2.5"
                    >
                      <p className="text-sm leading-relaxed text-white/85">{m.content}</p>
                      <div className="mt-1.5 flex items-center gap-3 text-[10px] text-white/35">
                        <span title={t('Importance')}>
                          {'★'.repeat(Math.min(m.importance, 10))}
                          {'☆'.repeat(Math.max(0, 5 - Math.min(m.importance, 10)))}
                        </span>
                        <span>
                          {t('Access Count')}: {m.access_count}
                        </span>
                        {m.created_at && (
                          <span>
                            {t('Created')}: {new Date(m.created_at).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
