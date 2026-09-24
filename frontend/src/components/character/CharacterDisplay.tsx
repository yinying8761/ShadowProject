import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { characterAvatarUrl } from '../../utils/avatarUrl';

export function CharacterDisplay() {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const { t } = useTranslation();

  if (!activeCharacter) {
    return (
      <div className="flex-1 flex items-center justify-center text-white/70 text-sm drop-shadow-[0_1px_3px_rgba(0,0,0,0.6)]">
        <p>{t('Please select or create a character')}</p>
      </div>
    );
  }

  const portrait = characterAvatarUrl(activeCharacter.avatar_path);

  return (
    <div className="flex-1 relative overflow-hidden drag-region pointer-events-auto">
      {portrait ? (
        <img
          src={portrait}
          alt={activeCharacter.name}
          draggable={false}
          className="absolute inset-0 w-full h-full object-contain object-bottom portrait-fade animate-breathe select-none pointer-events-none"
        />
      ) : (
        <PlaceholderPortrait name={activeCharacter.name} gender={activeCharacter.gender} />
      )}
    </div>
  );
}

function PlaceholderPortrait({ name, gender }: { name: string; gender?: string }) {
  const initial = name[0] || '?';
  const gradient = gender === 'male'
    ? 'from-blue-500/15 via-indigo-500/10 to-transparent'
    : 'from-pink-400/15 via-purple-400/10 to-transparent';

  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <div className={`w-72 h-72 rounded-full bg-gradient-to-br ${gradient} flex items-center justify-center animate-breathe`}>
        <div className="w-40 h-40 rounded-full bg-white/5 border border-white/10 backdrop-blur-sm flex items-center justify-center">
          <span className="text-7xl font-light text-white/70">{initial}</span>
        </div>
      </div>
    </div>
  );
}
