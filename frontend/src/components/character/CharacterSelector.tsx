import { useCharacters } from '../../hooks/useCharacters';

export function CharacterSelector() {
  const { characters, activeCharacter, switchCharacter } = useCharacters();

  return (
    <div className="px-3 py-2 border-b border-companion-border/30">
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {characters.map((char) => (
          <button
            key={char.id}
            onClick={() => switchCharacter(char)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs whitespace-nowrap transition-all flex-shrink-0 ${
              activeCharacter?.id === char.id
                ? 'bg-companion-accent/20 text-companion-accent border border-companion-accent/40'
                : 'bg-companion-card/40 text-companion-text-muted border border-transparent hover:border-companion-border/50'
            }`}
          >
            <div className="w-4 h-4 rounded-full bg-companion-border/50 flex items-center justify-center text-[8px]">
              {char.name[0]}
            </div>
            {char.name}
          </button>
        ))}
      </div>
    </div>
  );
}
