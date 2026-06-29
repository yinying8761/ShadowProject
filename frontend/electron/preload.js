const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getVersion: () => ipcRenderer.invoke('get-app-version'),
  setAlwaysOnTop: (flag) => ipcRenderer.invoke('set-always-on-top', flag),
  minimize: () => ipcRenderer.invoke('window-minimize'),
  hide: () => ipcRenderer.invoke('window-hide'),
  close: () => ipcRenderer.invoke('window-close'),
  onWindowVisibilityChanged: (cb) => {
    ipcRenderer.on('window:visibility-changed', (_event, value) => cb(value));
  },
});

contextBridge.exposeInMainWorld('isElectron', true);
