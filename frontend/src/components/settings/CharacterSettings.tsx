import { useAppStore } from '../../stores/appStore';
import { useCharacters } from '../../hooks/useCharacters';
import { useTranslation } from '../../i18n/useTranslation';

export function CharacterSettings() {
  const openCharacterEditor = useAppStore((s) => s.openCharacterEditor);
  const { characters, activeCharacter, switchCharacter } = useCharacters();
  const { t } = useTranslation();

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-white/60">{t('Characters')}</label>
        <button
          onClick={() => openCharacterEditor(null)}
          className="text-xs text-companion-accent transition-colors hover:text-companion-accent-hover"
        >
          {t('+ New')}
        </button>
      </div>
      <div className="space-y-1.5">
        {characters.map((char) => (
          <div
            key={char.id}
            className={`flex items-center justify-between rounded-lg border px-3 py-2 transition-colors ${
              activeCharacter?.id === char.id
                ? 'border-companion-accent/40 bg-companion-accent/15'
                : 'border-white/10 bg-white/5 hover:bg-white/10'
            }`}
          >
            <button onClick={() => switchCharacter(char)} className="flex-1 text-left">
              <div className="text-sm text-white">{char.name}</div>
              <div className="mt-0.5 text-[10px] text-white/40">{char.archetype}</div>
            </button>
            <button
              onClick={() => openCharacterEditor(char)}
              className="rounded px-2 py-1 text-[10px] text-white/50 hover:text-white"
            >
              {t('Edit')}
            </button>
          </div>
        ))}
        {characters.length === 0 && (
          <div className="py-3 text-center text-xs text-white/40">{t('No characters yet')}</div>
        )}
      </div>
    </div>
  );
}
