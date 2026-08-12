import { useEffect, useState } from 'react';
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

/** Chinese relative date: 今天 / 昨天 / N天前 / N周前 / N个月前 / 年月 */
function relativeDate(iso: string): string {
  const dt = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - dt.getTime();
  const days = Math.floor(diffMs / 86400000);
  if (days < 0) return new Date(iso).toLocaleDateString();
  if (days === 0) return '今天';
  if (days === 1) return '昨天';
  if (days <= 7) return `${days}天前`;
  if (days <= 30) return `${Math.floor(days / 7)}周前`;
  if (days < 180) return `${Math.floor(days / 30)}个月前`;
  return `${dt.getFullYear()}年${dt.getMonth() + 1}月`;
}

export function MemoryPanel() {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { t } = useTranslation();
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.fetchMemories(activeCharacter?.id)
      .then((data) => setMemories(data.memories || []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [activeCharacter]);

  const groups = groupByType(memories);

  const handleExportJSON = () => {
    downloadFile(
      JSON.stringify(memories, null, 2),
      `memories-${activeCharacter?.name || 'character'}.json`,
      'application/json'
    );
  };

  const handleExportTXT = () => {
    const lines = memories.map((m) => {
      const date = m.created_at ? new Date(m.created_at).toLocaleDateString() : '?';
      return `[${date}] [${m.memory_type}] ${m.content}`;
    });
    downloadFile(
      lines.join('\n'),
      `memories-${activeCharacter?.name || 'character'}.txt`,
      'text/plain'
    );
  };

  if (loading) {
    return <p className="text-xs text-companion-text/40 py-4">{t('Checking')}</p>;
  }

  return (
    <div className="space-y-3">
      {/* Header with export */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-companion-text/60">
          {memories.length} {t('Memories')}
        </span>
        {memories.length > 0 && (
          <div className="flex gap-1">
            <button onClick={handleExportJSON} className="text-[10px] text-companion-accent hover:text-companion-accent-hover transition-colors">
              {t('Export JSON')}
            </button>
            <span className="text-white/20">·</span>
            <button onClick={handleExportTXT} className="text-[10px] text-companion-accent hover:text-companion-accent-hover transition-colors">
              {t('Export TXT')}
            </button>
          </div>
        )}
      </div>

      {memories.length === 0 ? (
        <div className="py-6 text-center text-xs text-companion-text/40">
          {t('No memories yet')}
        </div>
      ) : (
        <div className="space-y-3">
          {Array.from(groups.entries()).map(([type, items]) => (
            <div key={type} className="rounded-lg border border-white/10 bg-black/30 p-3">
              <div className="mb-2 text-[10px] font-medium text-companion-accent/70">
                {t(TYPE_LABELS[type] || type)}
                <span className="ml-1 text-companion-text/40">({items.length})</span>
              </div>
              <div className="space-y-2">
                {items.map((m) => (
                  <div key={m.id} className="text-xs text-companion-text/80 leading-relaxed pl-3 border-l-2 border-white/10">
                    <p>{m.content}</p>
                    <div className="mt-1 flex gap-3 text-[10px] text-companion-text/40">
                      {m.created_at && (
                        <span>{relativeDate(m.created_at)}</span>
                      )}
                      <span>{t('Importance')}: {m.importance}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
