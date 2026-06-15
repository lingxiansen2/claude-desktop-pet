# -*- coding: utf-8 -*-
"""Claude Code / Codex hook → 桌宠状态文件。

用法:
  py -3 main.py hook claude <EventName>
  py -3 main.py hook codex <EventName>
或打包后:
  desktop-pet.exe hook claude <EventName>
  desktop-pet.exe hook codex <EventName>
事件 JSON 从 stdin 传入，提取关键信息后原子写入
~/.desktop-pet/status.json，供桌宠轮询。
"""
import ctypes
import ctypes.wintypes
import json
import os
import sys
import time

CODEX_EVENT_STATE = {
    "SessionStart": "idle",
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PermissionRequest": "asking",
    "PostToolUse": "thinking",
    "PreCompact": "working",
    "PostCompact": "thinking",
    "SubagentStart": "working",
    "SubagentStop": "thinking",
    "Stop": "done",
}

CLAUDE_EVENT_STATE = {
    "UserPromptSubmit": "thinking",
    "PreToolUse": "working",
    "PostToolUse": "thinking",
    "Notification": "asking",
    "PermissionRequest": "asking",
    "Stop": "done",
    "SubagentStop": "thinking",
    "SessionStart": "idle",
    "SessionEnd": "idle",
}

# 这些工具本质是在等用户做选择，应显示为 asking 而不是 working
CODEX_ASKING_TOOLS = {"request_user_input", "functions.request_user_input"}
CLAUDE_ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}

STATUS_PATH = os.path.join(os.path.expanduser("~"), ".desktop-pet", "status.json")


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _snapshot():
    """进程快照：返回 (pid→ppid, pid→exe名小写)。"""
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(2, 0)   # TH32CS_SNAPPROCESS
    ppid, names = {}, {}
    if snap == -1:
        return ppid, names
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    if k32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            ppid[entry.th32ProcessID] = entry.th32ParentProcessID
            names[entry.th32ProcessID] = entry.szExeFile.lower()
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
    k32.CloseHandle(snap)
    return ppid, names


def _visible_windows():
    """枚举所有可见顶层主窗口，返回 [(hwnd, owner_pid)]。"""
    u32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def enum_cb(hwnd, _):
        if u32.IsWindowVisible(hwnd) and not u32.GetWindow(hwnd, 4):
            wpid = ctypes.wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            out.append((hwnd, wpid.value))
        return True

    u32.EnumWindows(enum_cb, 0)
    return out


def _visible_toplevel_of(pid):
    """返回属于 pid 的第一个可见顶层主窗口 HWND，找不到返回 0。"""
    for hwnd, wpid in _visible_windows():
        if wpid == pid:
            return hwnd
    return 0


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def _window_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


# 允许作为 agent 终端窗口的宿主进程（小写）；其余一律视为非交互会话，
# 避免后台 bot/桥接进程把企业微信等无关窗口写进状态文件
TERMINAL_HOSTS = {
    "windowsterminal.exe", "code.exe", "cursor.exe", "wezterm-gui.exe",
    "alacritty.exe", "conemu64.exe", "conemu.exe", "tabby.exe",
    "hyper.exe", "warp.exe",
}
GUI_HOSTS = {"codex.exe", "codex app.exe", "codex-desktop.exe"}


def find_agent_hwnd():
    """定位承载 agent 的终端/桌面窗口 HWND；非交互会话返回 0。

    控制台程序（codex/claude/cmd/pwsh）自身没有窗口，可见窗口属于宿主：
    - 传统 conhost：AttachConsole 后 GetConsoleWindow 即为可见窗口
    - Windows Terminal / VS Code：GetConsoleWindow 是隐形伪窗口，
      其属主 conhost/openconsole 的父进程才是真正的终端主进程
    """
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    ppid, names = _snapshot()

    pid = os.getpid()
    chain = []
    for _ in range(12):
        pid = ppid.get(pid, 0)
        if not pid:
            break
        chain.append(pid)

    for pid in chain:
        if names.get(pid) in GUI_HOSTS:
            hwnd = _visible_toplevel_of(pid)
            if hwnd:
                return hwnd

    k32.FreeConsole()   # 自身可能带控制台（开发模式），先脱离
    console_title = ""
    got_console = False
    for pid in chain:
        if names.get(pid) == "explorer.exe":
            break
        if not k32.AttachConsole(pid):
            continue
        got_console = True
        tbuf = ctypes.create_unicode_buffer(512)
        k32.GetConsoleTitleW(tbuf, 512)
        console_title = tbuf.value
        chwnd = k32.GetConsoleWindow()
        host_pid = ctypes.wintypes.DWORD()
        if chwnd:
            u32.GetWindowThreadProcessId(chwnd, ctypes.byref(host_pid))
        k32.FreeConsole()
        if (chwnd and u32.IsWindowVisible(chwnd)
                and window_class(chwnd) != "PseudoConsoleWindow"):
            return chwnd                        # 传统 conhost，真控制台窗口
        # ConPTY：伪窗口属主的父进程若是已知终端，则取其主窗口
        host = ppid.get(host_pid.value, 0)
        if host and names.get(host) in TERMINAL_HOSTS:
            hwnd = _visible_toplevel_of(host)
            if hwnd:
                return hwnd
        break   # 同一条链共享一个控制台，无需再试更高层祖先

    if not got_console:
        return 0
    # ConPTY 且宿主不在父链上（Win11 默认终端接管）：
    # 在所有可见窗口里找终端宿主进程的窗口，优先标题匹配
    cands = [(h, p) for h, p in _visible_windows()
             if names.get(p) in TERMINAL_HOSTS]
    if console_title:
        for h, _p in cands:
            t = _window_title(h)
            if t and (console_title in t or t in console_title):
                return h
    if len(cands) == 1:
        return cands[0][0]
    for h, _p in _visible_windows():
        if "codex" in _window_title(h).lower():
            return h
    return 0


def _read_stdin(stdin):
    try:
        stream = stdin if stdin is not None else getattr(sys.stdin, "buffer", None)
        if stream is None:
            return {}
        raw = stream.read().decode("utf-8-sig", errors="replace").strip()
        return json.loads(raw) if raw else {}
    except (ValueError, OSError):
        return {}


def _tool_detail(data):
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if isinstance(tool_input, dict):
        desc = tool_input.get("description") or tool_input.get("command")
        if desc:
            return tool, str(desc)[:48]
    return tool, tool


def _is_codex_asking_tool(tool_name):
    low = (tool_name or "").lower()
    return any(mark.lower() in low for mark in CODEX_ASKING_TOOLS)


def _handle_codex(event, data):
    event = data.get("hook_event_name") or event
    state = CODEX_EVENT_STATE.get(event, "idle")
    detail = ""
    if event == "PreToolUse":
        tool, detail = _tool_detail(data)
        if _is_codex_asking_tool(tool):
            state, detail = "asking", "等你输入"
    elif event == "PermissionRequest":
        _tool, detail = _tool_detail(data)
    elif event == "PostToolUse":
        detail = data.get("tool_name", "")
    elif event in ("PreCompact", "PostCompact"):
        detail = data.get("trigger", "")
    elif event in ("SubagentStart", "SubagentStop"):
        detail = data.get("agent_type", "")
    elif event == "SessionStart":
        detail = data.get("source", "")
    return event, state, detail


def _handle_claude(event, data):
    state = CLAUDE_EVENT_STATE.get(event, "idle")
    detail = ""
    if event == "PreToolUse":
        detail = data.get("tool_name", "")
        if detail in CLAUDE_ASKING_TOOLS:
            state, detail = "asking", "选择选项"
    elif event == "PermissionRequest":
        detail = data.get("tool_name", "")
    elif event == "Notification":
        msg = data.get("message", "")
        low = msg.lower()
        if "permission" in low or "approval" in low or "confirm" in low:
            detail = msg.rsplit(" use ", 1)[-1] if " use " in msg else msg
        elif "waiting for" in low or "idle" in low:
            state = "idle"
        else:
            detail = msg
    return event, state, detail


def run_hook(agent, event, stdin=None):
    """读取事件 JSON 并写状态文件。stdin 为 None 时用 sys.stdin。"""
    data = _read_stdin(stdin)
    agent = (agent or "codex").lower()
    if agent == "claude":
        event, state, detail = _handle_claude(event, data)
    else:
        event, state, detail = _handle_codex(event, data)

    try:
        hwnd = find_agent_hwnd()
    except OSError:
        hwnd = 0
    out = {"state": state, "detail": detail, "event": event, "agent": agent,
           "hwnd": hwnd, "ts": time.time()}
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, STATUS_PATH)


if __name__ == "__main__":
    run_hook(sys.argv[1] if len(sys.argv) > 1 else "codex",
             sys.argv[2] if len(sys.argv) > 2 else "unknown")
