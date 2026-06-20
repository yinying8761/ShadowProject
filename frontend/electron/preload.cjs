const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),
  setAlwaysOnTop: (flag) => ipcRenderer.invoke('set-always-on-top', flag),
  minimize: () => ipcRenderer.invoke('window-minimize'),
  minimizeWindow: () => ipcRenderer.invoke('window-minimize'),
  windowMinimize: () => ipcRenderer.invoke('window-minimize'),
  hide: () => ipcRenderer.invoke('window-hide'),
  hideWindow: () => ipcRenderer.invoke('window-hide'),
  windowHide: () => ipcRenderer.invoke('window-hide'),
  close: () => ipcRenderer.invoke('window-close'),
  closeWindow: () => ipcRenderer.invoke('window-close'),
  windowClose: () => ipcRenderer.invoke('window-close'),
  setFloatingEnabled: (flag) => ipcRenderer.invoke('set-floating-enabled', flag),
  setFloatingAvatar: (info) => ipcRenderer.invoke('set-floating-avatar', info),
  refreshFloatingAvatar: () => ipcRenderer.invoke('refresh-floating-avatar'),
  notifyProactiveReply: (payload) => ipcRenderer.invoke('notify-proactive-reply', payload),
  setContentProtection: (enable) => ipcRenderer.invoke('set-content-protection', enable),
});
