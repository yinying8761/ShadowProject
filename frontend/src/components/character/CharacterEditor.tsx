import { useState } from 'react';
import { useCharacters } from '../../hooks/useCharacters';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { CharacterProfile } from '../../types';

interface Props {
  character?: CharacterProfile;
  onClose: () => void;
}

export function CharacterEditor({ character, onClose }: Props) {
  const { reloadCharacters } = useCharacters();
  const { t } = useTranslation();
  const [form, setForm] = useState({
    name: character?.name || '',
    gender: character?.gender || '',
    personality: character?.personality || '',
    role: character?.role || 'companion',
    archetype: character?.archetype || 'friend',
    voice_style: character?.voice_style || '',
    system_prompt_template: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async () => {
    if (!form.name.trim()) return;
    setSaving(true);
    setError('');
    try {
      if (character) {
        await api.updateCharacter(character.id, form);
      } else {
        await api.createCharacter(form);
      }
      await reloadCharacters();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Failed to save'));
    }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (!character) return;
    if (!confirm(t('Delete "{name}"? This cannot be undone.', { name: character.name }))) return;
    try {
      await api.deleteCharacter(character.id);
      await reloadCharacters();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Failed to delete'));
    }
  };

  const fieldClass = "w-full bg-companion-bg border border-companion-border rounded-lg px-3 py-2 text-sm text-companion-text outline-none focus:border-companion-accent/50 transition-colors";
  const labelClass = "text-xs text-companion-text-muted mb-1 block";

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-companion-sidebar border border-companion-border rounded-2xl w-[420px] max-h-[80vh] overflow-y-auto p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-companion-text">
          {character ? t('Edit Character') : t('Create Character')}
        </h2>

        <div>
          <label className={labelClass}>{t('Name *')}</label>
          <input className={fieldClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelClass}>{t('Gender')}</label>
            <select className={fieldClass} value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
              <option value="">{t('Not set')}</option>
              <option value="female">{t('Female')}</option>
              <option value="male">{t('Male')}</option>
            </select>
          </div>
          <div>
            <label className={labelClass}>{t('Role')}</label>
            <input className={fieldClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} />
          </div>
        </div>

        <div>
          <label className={labelClass}>{t('Archetype')}</label>
          <input className={fieldClass} value={form.archetype} onChange={(e) => setForm({ ...form, archetype: e.target.value })} />
        </div>

        <div>
          <label className={labelClass}>{t('Voice Style')}</label>
          <input className={fieldClass} value={form.voice_style} onChange={(e) => setForm({ ...form, voice_style: e.target.value })} placeholder={t('e.g. warm and friendly')} />
        </div>

        <div>
          <label className={labelClass}>{t('Personality')}</label>
          <textarea
            className={fieldClass}
            rows={3}
            value={form.personality}
            onChange={(e) => setForm({ ...form, personality: e.target.value })}
            placeholder={t("Describe the character's personality in detail...")}
          />
        </div>

        {error && <p className="text-red-400 text-xs">{error}</p>}

        <div className="flex justify-between pt-2">
          <div>
            {character && (
              <button onClick={handleDelete} className="text-red-400 hover:text-red-300 text-xs transition-colors">
                {t('Delete')}
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-2 text-xs text-companion-text-muted hover:text-companion-text transition-colors">
              {t('Cancel')}
            </button>
            <button
              onClick={handleSubmit}
              disabled={saving || !form.name.trim()}
              className="bg-companion-accent hover:bg-companion-accent-hover disabled:opacity-40 text-white px-4 py-2 rounded-lg text-xs transition-colors"
            >
              {saving ? t('Saving...') : t('Save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
