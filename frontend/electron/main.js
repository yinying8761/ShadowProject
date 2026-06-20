const { app, BrowserWindow, Tray, Menu, nativeImage, screen, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow = null;
let tray = null;
let pythonProcess = null;
const isDev = process.env.NODE_ENV !== 'production';

const WIN_WIDTH = 480;
const WIN_HEIGHT = 760;

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
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.setAlwaysOnTop(true, 'floating');

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('close', (e) => {
    if (tray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });
}

function createTray() {
  const trayIcon = nativeImage.createEmpty();
  tray = new Tray(trayIcon);
  tray.setToolTip('AI Companion');

  const contextMenu = Menu.buildFromTemplate([
    { label: 'Show', click: () => mainWindow.show() },
    { label: 'Hide', click: () => mainWindow.hide() },
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
  tray.on('double-click', () => mainWindow.show());
}

ipcMain.handle('get-app-version', () => app.getVersion());
ipcMain.handle('set-always-on-top', (_, flag) => {
  if (mainWindow) mainWindow.setAlwaysOnTop(flag, 'floating');
});
ipcMain.handle('window-minimize', () => mainWindow?.minimize());
ipcMain.handle('window-hide', () => mainWindow?.hide());
ipcMain.handle('window-close', () => mainWindow?.close());

app.whenReady().then(() => {
  // In dev mode, Python is launched separately by scripts/dev.bat
  // to avoid double-launching and to keep logs visible in its own console.
  if (!isDev) {
    startPythonBackend();
  }
  createWindow();
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
