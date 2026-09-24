/**
 * 角色头像在后端静态目录里的完整 URL（桌面端后端固定监听 8722）。
 * 只有一处拼这个地址，改端口不会漏掉某个组件。
 */
export function characterAvatarUrl(avatarPath?: string | null): string | null {
  return avatarPath ? `http://localhost:8722/data/${avatarPath}` : null;
}
