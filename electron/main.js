const { app, BrowserWindow, shell, Menu, Tray, dialog } = require('electron')

// macOS 26+ 베타 / 샌드박스 호환성 플래그 (app.whenReady 전에 설정해야 적용됨)
app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('disable-gpu-sandbox')
app.commandLine.appendSwitch('disable-features', 'VizDisplayCompositor')
app.commandLine.appendSwitch('js-flags', '--stack-size=65536')

const path = require('path')
const fs = require('fs')
const { spawn, spawnSync, execSync } = require('child_process')
const http = require('http')

const BACKEND_PORT = 8766
const IS_WIN = process.platform === 'win32'

// 개발 모드: electron/main.js 기준 상위 폴더 / 배포 모드: resources 폴더
const APP_DIR = app.isPackaged
  ? process.resourcesPath
  : path.join(__dirname, '..')

let mainWindow = null
let pythonProcess = null
let tray = null
let PYTHON_CMD = null

// ── Python 경로 동적 탐지 ─────────────────────────────
function findPython() {
  const candidates = IS_WIN
    ? ['python', 'py', 'python3']
    : ['python3', 'python']

  for (const cmd of candidates) {
    try {
      const result = spawnSync(cmd, ['--version'], { stdio: 'pipe', timeout: 5000 })
      if (result.status === 0) {
        const ver = (result.stdout || result.stderr || '').toString().trim()
        console.log(`[Setup] Python 발견: ${cmd} (${ver})`)
        return cmd
      }
    } catch (_) {}
  }
  return null
}

// ── venv 생성 + pip 의존성 설치 (첫 실행 시 1회만) ────────
// venv를 AppData에 만들어서 externally-managed-environment 문제 회피
function setupVenv(pythonCmd) {
  return new Promise((resolve) => {
    const userData = app.getPath('userData')
    const venvDir = path.join(userData, 'venv')
    const venvPython = IS_WIN
      ? path.join(venvDir, 'Scripts', 'python.exe')
      : path.join(venvDir, 'bin', 'python3')
    const venvPip = IS_WIN
      ? path.join(venvDir, 'Scripts', 'pip.exe')
      : path.join(venvDir, 'bin', 'pip3')

    // venv가 이미 있으면 그 Python 사용
    if (fs.existsSync(venvPython)) {
      console.log('[Setup] 기존 venv 사용:', venvPython)
      return resolve(venvPython)
    }

    const reqFile = path.join(APP_DIR, 'requirements.txt')

    // Step 1: venv 생성
    console.log('[Setup] venv 생성 중...')
    const createResult = spawnSync(pythonCmd, ['-m', 'venv', venvDir], {
      stdio: 'pipe', timeout: 60000
    })
    if (createResult.status !== 0) {
      console.error('[Setup] venv 생성 실패 — 시스템 Python으로 계속')
      return resolve(pythonCmd)
    }
    console.log('[Setup] venv 생성 완료')

    // Step 2: pip install
    if (!fs.existsSync(reqFile)) return resolve(venvPython)

    console.log('[Setup] pip install 시작...')
    const pip = spawn(venvPip, ['install', '-r', reqFile, '--quiet'], {
      stdio: 'pipe', cwd: APP_DIR
    })
    pip.stdout.on('data', d => console.log('[pip]', d.toString().trim()))
    pip.stderr.on('data', d => console.log('[pip]', d.toString().trim()))
    pip.on('exit', (code) => {
      if (code === 0) {
        console.log('[Setup] pip install 완료 → venv 사용')
      } else {
        console.error('[Setup] pip install 실패 (코드:', code, ')')
      }
      resolve(venvPython)
    })
    pip.on('error', (err) => {
      console.error('[Setup] pip 오류:', err)
      resolve(pythonCmd)  // fallback
    })
    setTimeout(() => resolve(venvPython), 180000)  // 3분 타임아웃
  })
}

// ── config.json 초기화 (AppData에 없으면 기본값 복사) ────
function initConfig() {
  const configDir = app.getPath('userData')
  if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true })

  const configDest = path.join(configDir, 'config.json')
  if (!fs.existsSync(configDest)) {
    const configSrc = path.join(APP_DIR, 'config.json')
    if (fs.existsSync(configSrc)) {
      fs.copyFileSync(configSrc, configDest)
      console.log('[Config] 기본 설정 복사 →', configDest)
    }
  }
}

// ── Python 서버 종료 ──────────────────────────────────
function killPythonServer() {
  if (!pythonProcess) return
  try {
    if (IS_WIN) {
      execSync(`taskkill /PID ${pythonProcess.pid} /T /F`, { stdio: 'ignore' })
    } else {
      // Mac/Linux: 프로세스 그룹 전체 종료
      try { process.kill(-pythonProcess.pid, 'SIGTERM') } catch (_) {
        pythonProcess.kill('SIGTERM')
      }
    }
  } catch (_) {
    try { pythonProcess.kill() } catch (_) {}
  }
  pythonProcess = null
}

// ── Python 서버 시작 ──────────────────────────────────
function startPythonServer() {
  const configDir = app.getPath('userData')
  console.log('[Electron] Python 서버 시작:', PYTHON_CMD)

  pythonProcess = spawn(
    PYTHON_CMD,
    ['keyword_filter.py'],
    {
      cwd: APP_DIR,
      env: { ...process.env, APP_CONFIG_DIR: configDir },
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: !IS_WIN,  // Mac/Linux에서 프로세스 그룹 kill 가능하게
    }
  )

  pythonProcess.stdout.on('data', d => console.log('[Python]', d.toString().trim()))
  pythonProcess.stderr.on('data', d => console.error('[Python ERR]', d.toString().trim()))
  pythonProcess.on('exit', code => console.log('[Python] 종료 코드:', code))
}

// ── 백엔드 준비 대기 ──────────────────────────────────
function waitForBackend(retries = 60) {
  return new Promise((resolve, reject) => {
    const check = (n) => {
      http.get(`http://127.0.0.1:${BACKEND_PORT}/`, () => {
        resolve()
      }).on('error', () => {
        if (n <= 0) return reject(new Error('Backend timeout'))
        setTimeout(() => check(n - 1), 500)
      })
    }
    check(retries)
  })
}

// ── 로딩 창 (pip install / 서버 시작 중 표시) ────────────
function createLoadingWindow() {
  const win = new BrowserWindow({
    width: 420,
    height: 260,
    frame: false,
    resizable: false,
    center: true,
    alwaysOnTop: true,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: false },
  })

  const loadingFile = path.join(APP_DIR, 'templates', 'loading.html')
  win.loadFile(loadingFile)

  return win
}

// ── 메인 창 생성 ──────────────────────────────────────
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: '키워드 필터 검색기',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainWindow.loadURL(`http://localhost:${BACKEND_PORT}`)

  // 외부 링크 → 기본 브라우저
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://localhost:${BACKEND_PORT}`)) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })

  // F12 DevTools 토글
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.key === 'F12' || (input.control && input.shift && input.key === 'I')) {
      mainWindow.webContents.isDevToolsOpened()
        ? mainWindow.webContents.closeDevTools()
        : mainWindow.webContents.openDevTools()
    }
  })

  // X 버튼 → 트레이로 최소화
  mainWindow.on('close', (e) => {
    if (!app.isQuiting) {
      e.preventDefault()
      mainWindow.hide()
    }
  })
  mainWindow.on('closed', () => { mainWindow = null })

  Menu.setApplicationMenu(null)
}

// ── 트레이 ────────────────────────────────────────────
function createTray() {
  const iconName = IS_WIN ? 'icon.ico' : 'icon.png'
  const iconPath = [
    path.join(APP_DIR, iconName),
    path.join(APP_DIR, 'icon.png'),
  ].find(p => { try { return fs.existsSync(p) } catch { return false } })

  if (!iconPath) return

  tray = new Tray(iconPath)
  tray.setToolTip('키워드 필터 검색기')
  tray.setContextMenu(Menu.buildFromTemplate([
    {
      label: '열기', click: () => {
        if (mainWindow) { mainWindow.show(); mainWindow.focus() }
        else createWindow()
      }
    },
    { type: 'separator' },
    {
      label: '종료', click: () => {
        app.isQuiting = true
        killPythonServer()
        app.quit()
      }
    },
  ]))
  tray.on('double-click', () => {
    if (mainWindow) { mainWindow.show(); mainWindow.focus() }
    else createWindow()
  })
}

// ── 앱 시작 ───────────────────────────────────────────
app.whenReady().then(async () => {
  // 1. Python 탐지
  PYTHON_CMD = findPython()
  if (!PYTHON_CMD) {
    dialog.showErrorBox(
      'Python을 찾을 수 없습니다',
      [
        'Python 3.x가 설치되어 있지 않습니다.',
        '',
        '설치 방법:',
        IS_WIN
          ? '  https://www.python.org/downloads/ 에서 설치\n  (설치 시 "Add Python to PATH" 체크 필수)'
          : '  터미널에서: brew install python3',
        '',
        '설치 후 앱을 다시 실행해 주세요.',
      ].join('\n')
    )
    app.quit()
    return
  }

  // 2. 로딩 창 표시
  const loadingWin = createLoadingWindow()

  // 3. config 초기화 (API 키 포함 기본값 복사)
  initConfig()

  // 4. venv 생성 + pip 설치 (첫 실행만) → 이후 실행에서는 venv Python 사용
  PYTHON_CMD = await setupVenv(PYTHON_CMD)

  // 5. Python 서버 시작
  startPythonServer()

  // 6. 백엔드 준비 대기
  try {
    await waitForBackend()
    console.log('[Electron] 백엔드 준비 완료')
  } catch (e) {
    console.error('[Electron] 백엔드 응답 없음 — 계속 진행')
  }

  // 7. 메인 창 먼저 열고 → 로딩 창 닫기
  // (순서 중요: loadingWin.destroy() 시 window-all-closed 방지)
  createWindow()
  loadingWin.destroy()

  try { createTray() } catch (_) {}
})

app.on('window-all-closed', () => {
  killPythonServer()
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})

app.on('before-quit', () => {
  app.isQuiting = true
  killPythonServer()
})
