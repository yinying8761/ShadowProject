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
      const [chars, cfg] = await Promise.all([
        api.fetchCharacters(),
        api.fetchConfig(),
      ]);
      setCharacters(chars);
      const current = useAppStore.getState().activeCharacter;
      const stillExists = current && chars.some((c) => c.id === current.id);
      if (!stillExists && chars.length > 0) {
        // Restore last character from config, or pick first
        const lastId = cfg.lastCharacterId;
        const restore = (lastId && chars.find((c) => c.id === lastId)) || chars[0];
        setActiveCharacter(restore);
      } else if (!current && chars.length > 0) {
        const lastId = cfg.lastCharacterId;
        const restore = (lastId && chars.find((c) => c.id === lastId)) || chars[0];
        setActiveCharacter(restore);
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
