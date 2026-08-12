import { useEffect, useState, useRef } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { UserProfile } from '../../types';

export function UserProfileSettings() {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const showSettings = useAppStore((s) => s.showSettings);
  const { t } = useTranslation();

  const [profile, setProfile] = useState<Partial<UserProfile>>({
    user_name: '',
    user_gender: null,
    user_occupation: '',
    user_relationship: 'friend',
    user_bio: '',
  });
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [profileSaving, setProfileSaving] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (showSettings && activeCharacter) {
      api.fetchUserProfile(activeCharacter.id)
        .then((p) => {
          setProfile({
            user_name: p.user_name,
            user_gender: p.user_gender,
            user_occupation: p.user_occupation,
            user_relationship: p.user_relationship,
            user_bio: p.user_bio,
          });
          setProfileLoaded(true);
        })
        .catch(() => setProfileLoaded(true));
    }
  }, [showSettings, activeCharacter]);

  useEffect(() => {
    if (!profileLoaded || !activeCharacter) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      setProfileSaving(true);
      api.updateUserProfile(activeCharacter.id, profile)
        .catch(console.error)
        .finally(() => setProfileSaving(false));
    }, 600);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [profile, profileLoaded, activeCharacter]);

  const updateProfile = (patch: Partial<UserProfile>) => {
    setProfile((prev) => ({ ...prev, ...patch }));
  };

  const inputClass = 'w-full rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-companion-accent/50 focus:outline-none';

  return (
    <div className="space-y-3 rounded-lg border border-white/10 bg-black/30 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-white/60">{t('Your Profile')}</span>
        {profileSaving && <span className="text-[10px] text-companion-accent">{t('Saving...')}</span>}
      </div>

      <div>
        <label className="mb-1 block text-[10px] text-white/50">{t('Name')}</label>
        <input type="text" value={profile.user_name || ''} onChange={(e) => updateProfile({ user_name: e.target.value })} placeholder={t('Your name')} className={inputClass} />
      </div>

      <div>
        <label className="mb-1 block text-[10px] text-white/50">{t('Gender')}</label>
        <select
          value={profile.user_gender || ''}
          onChange={(e) => updateProfile({ user_gender: e.target.value || null })}
          className="w-full rounded-md border border-white/15 bg-companion-bg px-3 py-1.5 text-xs text-companion-text focus:border-companion-accent/50 focus:outline-none appearance-none cursor-pointer"
          style={{ colorScheme: 'dark' }}
        >
          <option value="" className="text-companion-text bg-companion-bg">{t('Not set')}</option>
          <option value="男" className="text-companion-text bg-companion-bg">{t('Male')}</option>
          <option value="女" className="text-companion-text bg-companion-bg">{t('Female')}</option>
          <option value="其他" className="text-companion-text bg-companion-bg">{t('Other')}</option>
        </select>
      </div>

      <div>
        <label className="mb-1 block text-[10px] text-white/50">{t('Identity')}</label>
        <input type="text" value={profile.user_occupation || ''} onChange={(e) => updateProfile({ user_occupation: e.target.value })} placeholder={t('e.g. 大学生, 打工人, 自由职业...')} className={inputClass} />
      </div>

      <div>
        <label className="mb-1 block text-[10px] text-white/50">{t('Relationship')}</label>
        <input type="text" value={profile.user_relationship || ''} onChange={(e) => updateProfile({ user_relationship: e.target.value })} placeholder={t('e.g. 朋友, 助手和用户, 伙伴...')} className={inputClass} />
      </div>

      <div>
        <label className="mb-1 block text-[10px] text-white/50">{t('Self-introduction')}</label>
        <textarea rows={4} value={profile.user_bio || ''} onChange={(e) => updateProfile({ user_bio: e.target.value })} placeholder={t('Tell the AI about yourself — interests, hobbies, what you do...')} className={`${inputClass} resize-none`} />
      </div>

      <p className="text-[10px] text-white/40">
        {t('Each character can have a different profile. Switch characters to edit theirs.')}
      </p>
    </div>
  );
}
