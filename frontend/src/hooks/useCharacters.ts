import { useEffect, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import { api } from '../services/api';
import type { CharacterProfile } from '../types';

export function useCharacters() {
  const characters = useAppStore((s) => s.characters);
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const setCharacters = useAppStore((s) => s.setCharacters);
  const setActiveCharacter = useAppStore((s) => s.setActiveCharacter);

  const loadCharacters = useCallback(async () => {
    try {
      const chars = await api.fetchCharacters();
      setCharacters(chars);
      if (!activeCharacter && chars.length > 0) {
        setActiveCharacter(chars[0]);
      }
    } catch (e) {
      console.error('Failed to load characters:', e);
    }
  }, []);

  useEffect(() => {
    loadCharacters();
  }, [loadCharacters]);

  return {
    characters,
    activeCharacter,
    switchCharacter: setActiveCharacter,
    reloadCharacters: loadCharacters,
  };
}
