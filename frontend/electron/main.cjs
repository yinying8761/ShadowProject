const { app, BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain, Notification } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow = null;
let floatingWindow = null;
let tray = null;
let pythonProcess = null;
let floatingEnabled = true;
let hasUnreadProactive = false;
let floatingAvatarInfo = { initial: 'AI' };
const isDev = process.env.NODE_ENV !== 'production';
const APP_ID = 'com.ai-companion.desktop';

const WIN_WIDTH = 480;
const WIN_HEIGHT = 760;
const FLOAT_SIZE = 64;

function startPythonBackend() {
  const backendDir = path.join(__dirname, '..', '..', 'backend');
  pythonProcess = spawn('python', ['main.py'], {
    cwd: backendDir,
    env: { ...process.env },
    stdio: 'pipe',
  });

  pythonProcess.stdout.on('data', (data) => {
    console.log(`[Python] ${data}`);
  });

  pythonProcess.stderr.on('data', (data) => {
    console.error(`[Python stderr] ${data}`);
  });

  pythonProcess.on('close', (code) => {
    console.log(`[Python] exited with code ${code}`);
  });
}

function defaultFloatingPosition() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  return {
    x: width - FLOAT_SIZE - 24,
    y: height - FLOAT_SIZE - 110,
  };
}

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  mainWindow = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    minWidth: 360,
    minHeight: 560,
    x: width - WIN_WIDTH - 20,
    y: height - WIN_HEIGHT - 20,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.setAlwaysOnTop(true, 'floating');

  // Exclude this window from screen capture (Windows WDA_EXCLUDEFROMCAPTURE).
  // Uses Electron's native wrapper around SetWindowDisplayAffinity.
  // This prevents the AI from seeing its own UI in screen captures.
  mainWindow.once('ready-to-show', () => {
    try {
      mainWindow.setContentProtection(true);
      console.log('[MainWindow] content protection enabled');
    } catch (e) {
      console.warn('[MainWindow] content protection failed:', e);
    }
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  // When the main window becomes visible for any reason, dismiss the floating icon.
  const dismissFloating = () => {
    hasUnreadProactive = false;
    hideFloatingWindow();
    pushFloatingUnread();
    updateTrayMenu();
  };

  mainWindow.on('show', () => {
    console.log('[MainWindow] show event');
    dismissFloating();
    // Notify renderer of visibility change for daily greeting
    mainWindow.webContents.send('window:visibility-changed', { visible: true });
  });

  mainWindow.on('restore', () => {
    console.log('[MainWindow] restore event');
    dismissFloating();
  });

  mainWindow.on('focus', () => {
    // Safety net: if main window gets focus, ensure floating is hidden.
    if (floatingWindow && !floatingWindow.isDestroyed() && floatingWindow.isVisible()) {
      console.log('[MainWindow] focus — hiding floating');
      dismissFloating();
    }
  });

  mainWindow.on('close', (e) => {
    if (tray) {
      e.preventDefault();
      mainWindow.hide();
      showFloatingWindow();
    }
  });

  mainWindow.on('minimize', () => {
    console.log('[MainWindow] minimize event');
    showFloatingWindow();
  });
}

function createFloatingWindow() {
  const pos = defaultFloatingPosition();
  floatingWindow = new BrowserWindow({
    width: FLOAT_SIZE,
    height: FLOAT_SIZE,
    x: pos.x,
    y: pos.y,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload-floating.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  floatingWindow.loadFile(path.join(__dirname, 'floating.html'));

  floatingWindow.once('ready-to-show', () => {
    try {
      floatingWindow.setContentProtection(true);
      console.log('[Floating] content protection enabled');
    } catch (e) {
      console.warn('[Floating] content protection failed:', e);
    }
  });

  floatingWindow.webContents.on('did-finish-load', () => {
    pushFloatingAvatar();
    pushFloatingUnread();
  });

  floatingWindow.on('close', (e) => {
    e.preventDefault();
    floatingWindow.hide();
  });
}

function pushFloatingAvatar() {
  if (!floatingWindow || floatingWindow.isDestroyed()) return;
  floatingWindow.webContents.send('floating:avatar', floatingAvatarInfo);
}

function pushFloatingUnread() {
  if (!floatingWindow || floatingWindow.isDestroyed()) return;
  floatingWindow.webContents.send('floating:unread', { unread: hasUnreadProactive });
}

function showFloatingWindow() {
  if (!floatingEnabled || !floatingWindow || floatingWindow.isDestroyed()) return;
  if (mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible() && !mainWindow.isMinimized()) return;
  pushFloatingAvatar();
  pushFloatingUnread();
  floatingWindow.setAlwaysOnTop(true, 'floating');
  floatingWindow.show();
  console.log('[Floating] shown, hasUnreadProactive=', hasUnreadProactive);
}

function hideFloatingWindow() {
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    floatingWindow.hide();
    console.log('[Floating] hidden');
  }
}

function createTray() {
  const trayIcon = nativeImage.createEmpty();
  tray = new Tray(trayIcon);
  tray.setToolTip('AI Companion');
  updateTrayMenu();

  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

function showTrayBalloon(title, body) {
  if (!tray || typeof tray.displayBalloon !== 'function') return;
  try {
    tray.displayBalloon({
      title: title || 'AI Companion',
      content: body || 'You have a new proactive reply.',
      noSound: false,
    });
  } catch (e) {
    console.warn('[TrayBalloon] failed:', e);
  }
}

function updateTrayMenu() {
  if (!tray) return;

  const proactiveLabel = hasUnreadProactive ? 'Unread proactive reply' : 'No unread proactive reply';
  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Show',
      click: () => {
        mainWindow?.show();
        mainWindow?.focus();
      },
    },
    {
      label: 'Hide',
      click: () => {
        mainWindow?.hide();
        showFloatingWindow();
      },
    },
    { type: 'separator' },
    {
      label: 'Toggle Always On Top',
      click: () => {
        if (mainWindow) {
          const next = !mainWindow.isAlwaysOnTop();
          mainWindow.setAlwaysOnTop(next, 'floating');
        }
      },
    },
    {
      label: proactiveLabel,
      enabled: false,
    },
    { type: 'separator' },
    {
      label: 'Quit',
      click: () => {
        tray = null;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.setToolTip(hasUnreadProactive ? 'AI Companion - new proactive reply' : 'AI Companion');
}

function shouldNotify() {
  if (!mainWindow || mainWindow.isDestroyed()) return false;
  const hidden = mainWindow.isMinimized() || !mainWindow.isVisible();
  console.log('[Notify] shouldNotify? minimized=', mainWindow.isMinimized(), 'visible=', mainWindow.isVisible(), '=>', hidden);
  return hidden;
}

function handleProactiveNotification(payload = {}) {
  const { title, body, enabled } = payload;
  console.log('[Notify] handleProactiveNotification title=', title, 'enabled=', enabled);
  hasUnreadProactive = true;
  updateTrayMenu();
  pushFloatingUnread();

  // Always attempt to show floating window when a proactive reply arrives.
  // showFloatingWindow() internally checks if main window is visible & not minimized.
  showFloatingWindow();

  // System notification
  const wantNotify = enabled !== false && shouldNotify();
  console.log('[Notify] system notification wanted=', wantNotify);
  if (wantNotify) {
    let shown = false;

    if (Notification.isSupported()) {
      try {
        const notification = new Notification({
          title: title || 'AI Companion',
          body: body || 'You have a new proactive reply.',
          silent: false,
        });

        notification.on('click', () => {
          console.log('[Notify] notification clicked');
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
            mainWindow.focus();
          }
          hasUnreadProactive = false;
          pushFloatingUnread();
          updateTrayMenu();
          hideFloatingWindow();
        });

        notification.show();
        shown = true;
        console.log('[Notify] Electron Notification shown');
      } catch (e) {
        console.warn('[Notify] Notification failed:', e);
      }
    }

    if (!shown) {
      console.log('[Notify] falling back to tray balloon');
      showTrayBalloon(title, body);
    }
  }
}

ipcMain.handle('get-app-version', () => app.getVersion());
ipcMain.handle('set-always-on-top', (_, flag) => {
  if (mainWindow) mainWindow.setAlwaysOnTop(flag, 'floating');
});
ipcMain.handle('window-minimize', () => mainWindow?.minimize());
ipcMain.handle('window-hide', () => {
  if (mainWindow) {
    mainWindow.hide();
    showFloatingWindow();
  }
});
ipcMain.handle('window-close', () => {
  if (mainWindow) {
    mainWindow.hide();
    showFloatingWindow();
  }
});
// Temporary content protection for screen capture — makes the window
// invisible to screen-capture APIs (mss, OBS, etc.) while still visible to the user.
// Uses SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) on Windows.
ipcMain.handle('set-content-protection', (_, enable) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.setContentProtection(Boolean(enable));
      console.log('[MainWindow] setContentProtection:', enable);
      return true;
    } catch (e) {
      console.warn('[MainWindow] setContentProtection failed:', e);
      return false;
    }
  }
  return false;
});
ipcMain.handle('set-floating-enabled', (_, flag) => {
  floatingEnabled = Boolean(flag);
  if (!floatingEnabled) {
    hideFloatingWindow();
  } else if (mainWindow && !mainWindow.isVisible()) {
    showFloatingWindow();
  }
});
ipcMain.handle('set-floating-avatar', (_, info) => {
  if (info && typeof info === 'object') {
    floatingAvatarInfo = info;
    pushFloatingAvatar();
  }
  return true;
});
ipcMain.handle('refresh-floating-avatar', () => {
  pushFloatingAvatar();
  return true;
});
ipcMain.handle('notify-proactive-reply', (_, payload) => {
  console.log('[IPC] notify-proactive-reply received, payload=', JSON.stringify(payload));
  handleProactiveNotification(payload);
  return true;
});
ipcMain.on('floating:restore', () => {
  if (mainWindow) {
    mainWindow.show();
    mainWindow.focus();
  }
  hasUnreadProactive = false;
  pushFloatingUnread();
  hideFloatingWindow();
  updateTrayMenu();
});

let floatDragOrigin = null;

ipcMain.on('floating:drag-move', (_evt, totalDx, totalDy) => {
  if (!floatingWindow || floatingWindow.isDestroyed()) return;
  if (!floatDragOrigin) {
    floatDragOrigin = floatingWindow.getPosition();
  }
  floatingWindow.setPosition(
    floatDragOrigin[0] + totalDx,
    floatDragOrigin[1] + totalDy,
  );
});

ipcMain.on('floating:drag-end', () => {
  floatDragOrigin = null;
});

app.whenReady().then(() => {
  app.setAppUserModelId(APP_ID);
  if (!isDev) {
    startPythonBackend();
  }
  createWindow();
  createFloatingWindow();
  createTray();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // Keep in tray
  }
});

app.on('before-quit', () => {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
});
