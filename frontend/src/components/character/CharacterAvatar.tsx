import { useTranslation } from '../../i18n/useTranslation';

interface CharacterAvatarProps {
  name: string;
  avatarUrl?: string | null;
  size?: 'sm' | 'md' | 'lg';
}

const SIZE_MAP = { sm: 'w-8 h-8', md: 'w-12 h-12', lg: 'w-16 h-16' };
const FONT_MAP = { sm: 'text-sm', md: 'text-lg', lg: 'text-2xl' };

export function CharacterAvatar({ name, avatarUrl, size = 'md' }: CharacterAvatarProps) {
  const { t } = useTranslation();
  const initial = name[0]?.toUpperCase() || '?';

  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt={name}
        className={`${SIZE_MAP[size]} rounded-full object-cover border-2 border-companion-accent/30`}
      />
    );
  }

  return (
    <div
      className={`${SIZE_MAP[size]} rounded-full bg-companion-accent/15 border-2 border-companion-accent/30 flex items-center justify-center`}
      title={name || t('AI Companion')}
    >
      <span className={`${FONT_MAP[size]} text-companion-accent`}>{initial}</span>
    </div>
  );
}
