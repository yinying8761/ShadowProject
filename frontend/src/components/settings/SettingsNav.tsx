import { useTranslation } from '../../i18n/useTranslation';

type NavKey = 'general' | 'character' | 'profile' | 'model' | 'proactive' | 'voice' | 'memory';

const NAV_ITEMS: { key: NavKey; label: string }[] = [
  { key: 'general',   label: 'General' },
  { key: 'character', label: 'Characters' },
  { key: 'profile',   label: 'Your Profile' },
  { key: 'model',     label: 'Model' },
  { key: 'proactive', label: 'Proactive Chat' },
  { key: 'voice',     label: 'Voice (TTS)' },
  { key: 'memory',    label: 'Memories' },
];

interface SettingsNavProps {
  active: NavKey;
  onChange: (key: NavKey) => void;
}

export function SettingsNav({ active, onChange }: SettingsNavProps) {
  const { t } = useTranslation();

  return (
    <nav className="w-[130px] flex-shrink-0 flex flex-col gap-0.5 py-1 border-r border-white/[0.06]">
      {NAV_ITEMS.map((item) => (
        <button
          key={item.key}
          onClick={() => onChange(item.key)}
          className={`px-3 py-2 text-[11px] transition-colors text-left rounded-r-sm ${
            active === item.key
              ? 'bg-companion-accent/10 text-companion-accent border-r-[3px] border-companion-accent font-medium'
              : 'text-companion-text/50 hover:text-companion-text/80 hover:bg-white/[0.04]'
          }`}
        >
          {t(item.label)}
        </button>
      ))}
    </nav>
  );
}
