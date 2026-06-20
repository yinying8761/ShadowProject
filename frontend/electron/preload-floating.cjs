const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('floatingAPI', {
  restore: () => ipcRenderer.send('floating:restore'),
  dragMove: (dx, dy) => ipcRenderer.send('floating:drag-move', dx, dy),
  dragEnd: () => ipcRenderer.send('floating:drag-end'),
  onAvatar: (cb) => {
    ipcRenderer.on('floating:avatar', (_evt, info) => cb(info));
  },
  onUnread: (cb) => {
    ipcRenderer.on('floating:unread', (_evt, info) => cb(info));
  },
});
