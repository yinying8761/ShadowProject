import { useEffect, useState } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { GroupInfo } from '../../types';

/**
 * 设置面板「群聊」：已建的群 + 创建群 + 编辑群（改名 / 增删成员）+ 删群。
 * 成员**点击顺序即发言顺序**（后端把数组下标存成 position），所以这里不做排序界面。
 * 点群名进入群聊：关设置面板 → 切完整模式（store.enterGroup）。
 * 删群走 `DELETE /api/groups/{id}`：**只删这个群的内容**（群 / 成员资格 / 群对话 /
 * 群消息），角色与它们的 1:1 会话不受影响 —— 所以这里要二次确认，别手滑。
 */
export function GroupSettings() {
  const characters = useAppStore((s) => s.characters);
  const enterGroup = useAppStore((s) => s.enterGroup);
  const activeGroup = useAppStore((s) => s.activeGroup);
  const leaveGroup = useAppStore((s) => s.leaveGroup);
  const { t } = useTranslation();

  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<GroupInfo | null>(null);
  const [name, setName] = useState('');
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [listError, setListError] = useState('');
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const reload = async () => {
    try {
      setGroups(await api.fetchGroups());
    } catch (e) {
      console.error('Failed to load groups:', e);
    }
  };

  useEffect(() => {
    reload().finally(() => setLoading(false));
  }, []);

  const openCreate = () => {
    setEditing(null);
    setCreating(true);
    setName('');
    setMemberIds([]);
    setError('');
  };

  const openEdit = (group: GroupInfo) => {
    setCreating(false);
    setEditing(group);
    setName(group.name);
    setMemberIds(group.members.map((m) => m.character_id));
    setError('');
  };

  const closeForm = () => {
    setCreating(false);
    setEditing(null);
    setError('');
  };

  const toggleMember = (characterId: string) =>
    setMemberIds((prev) =>
      prev.includes(characterId) ? prev.filter((id) => id !== characterId) : [...prev, characterId]
    );

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t('Group name cannot be empty'));
      return;
    }
    if (memberIds.length === 0) {
      setError(t('Pick at least one member'));
      return;
    }
    try {
      if (editing) {
        await api.updateGroup(editing.id, { name: trimmed, memberIds });
      } else {
        await api.createGroup(trimmed, memberIds);
      }
      closeForm();
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const memberNames = (group: GroupInfo) =>
    group.members
      .map((m) => m.character_name || characters.find((c) => c.id === m.character_id)?.name || '?')
      .join(', ');

  /** 删群：点一次进入"确认"，再点一次真删（与对话列表同一个二次确认手感）。 */
  const handleDelete = async (group: GroupInfo) => {
    if (confirmingDelete !== group.id) {
      setConfirmingDelete(group.id);
      setListError('');
      setTimeout(() => setConfirmingDelete((cur) => (cur === group.id ? null : cur)), 3000);
      return;
    }
    setConfirmingDelete(null);
    try {
      await api.deleteGroup(group.id);
      if (editing?.id === group.id) closeForm();
      // 删的正是当前所在的那个群 → 退出群聊（回单聊并恢复进群前的模式）
      if (activeGroup?.id === group.id) leaveGroup();
      await reload();
    } catch (e) {
      console.error('Failed to delete group:', e);
      setListError(t('Delete failed. Please check your network.'));
    }
  };

  if (loading) {
    return <p className="text-xs text-companion-text/50">{t('Loading…')}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-companion-text/80">{t('Groups')}</h3>
        <button
          onClick={openCreate}
          className="rounded-md border border-companion-accent/20 bg-companion-accent/8 px-2 py-1 text-[10px] text-companion-accent hover:bg-companion-accent/15 transition-colors"
        >
          + {t('Create Group')}
        </button>
      </div>

      {(creating || editing) && (
        <div className="flex flex-col gap-2 rounded-md border border-companion-border/30 p-3">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('Group name')}
            className="rounded border border-companion-border/40 bg-companion-bg/80 px-2 py-1 text-xs text-companion-text outline-none focus:border-companion-accent/60"
          />

          <div className="flex items-center justify-between">
            <span className="text-[10px] text-companion-text/50">{t('Members')}</span>
            <span className="text-[10px] text-companion-text/35">
              {t('Click order is the speaking order')}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {characters.map((c) => {
              const selected = memberIds.includes(c.id);
              return (
                <button
                  key={c.id}
                  onClick={() => toggleMember(c.id)}
                  className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                    selected
                      ? 'border-companion-accent/50 bg-companion-accent/15 text-companion-accent'
                      : 'border-white/10 text-companion-text/50 hover:text-companion-text/80'
                  }`}
                >
                  {selected ? `${memberIds.indexOf(c.id) + 1}. ` : ''}
                  {c.name}
                </button>
              );
            })}
            {characters.length === 0 && (
              <span className="text-[10px] text-companion-text/40">{t('No characters yet')}</span>
            )}
          </div>

          {error && <p className="text-[10px] text-red-400">{error}</p>}

          <div className="flex justify-end gap-2">
            <button
              onClick={closeForm}
              className="rounded-md px-2 py-1 text-[10px] text-companion-text/50 hover:text-companion-text/80 transition-colors"
            >
              {t('Cancel')}
            </button>
            <button
              onClick={submit}
              className="rounded-md bg-companion-accent px-2.5 py-1 text-[10px] text-white hover:bg-companion-accent-hover transition-colors"
            >
              {editing ? t('Save') : t('Create')}
            </button>
          </div>
        </div>
      )}

      <ul className="flex flex-col gap-1">
        {groups.map((group) => (
          <li
            key={group.id}
            className="group flex items-center gap-1 rounded-md border border-white/5 px-2 py-1.5 hover:bg-white/[0.04]"
          >
            <button onClick={() => enterGroup(group)} className="flex-1 min-w-0 text-left">
              <span className="block truncate text-xs text-companion-text/90">{group.name}</span>
              <span className="block truncate text-[10px] text-companion-text/45">
                {memberNames(group)} · {group.conversations.length}
              </span>
            </button>
            <button
              onClick={() => openEdit(group)}
              title={t('Edit')}
              className="px-1.5 py-1 text-[10px] text-white/30 opacity-0 transition-colors group-hover:opacity-100 hover:text-companion-accent/80"
            >
              ✎
            </button>
            <button
              onClick={() => handleDelete(group)}
              title={t('Delete group')}
              className={`px-1.5 py-1 text-[10px] transition-colors ${
                confirmingDelete === group.id
                  ? 'font-medium text-red-400'
                  : 'text-white/30 opacity-0 group-hover:opacity-100 hover:text-red-400/80'
              }`}
            >
              {confirmingDelete === group.id ? t('Confirm?') : '🗑'}
            </button>
          </li>
        ))}
        {groups.length === 0 && (
          <p className="text-[10px] text-companion-text/40">{t('No groups yet')}</p>
        )}
      </ul>
      {listError && <p className="text-[10px] text-red-400">{listError}</p>}
    </div>
  );
}
