# -*- coding: utf-8 -*-
"""desktop-pet 安装/卸载：合并 Claude Code 与 Codex hooks。"""
import json
import os
import subprocess
import sys

CLAUDE_SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
CODEX_HOOKS = os.path.join(os.path.expanduser("~"), ".codex", "hooks.json")
STARTUP_LNK = os.path.join(
    os.path.expanduser("~"),
    "AppData", "Roaming", "Microsoft", "Windows",
    "Start Menu", "Programs", "Startup", "desktop-pet.lnk")

CLAUDE_HOOK_EVENTS = [
    ("UserPromptSubmit", False),
    ("PreToolUse", True),
    ("PostToolUse", True),
    ("Notification", True),
    ("PermissionRequest", True),
    ("Stop", False),
    ("SessionEnd", False),
]
CODEX_HOOK_EVENTS = [
    ("SessionStart", "startup|resume|clear|compact"),
    ("UserPromptSubmit", None),
    ("PreToolUse", "*"),
    ("PermissionRequest", "*"),
    ("PostToolUse", "*"),
    ("PreCompact", "manual|auto"),
    ("PostCompact", "manual|auto"),
    ("SubagentStart", "*"),
    ("SubagentStop", "*"),
    ("Stop", None),
]
OWN_MARKS = (
    "desktop_hook.py",
    "desktop-pet.exe",
    "desktop-pet",
    "claude-pet.exe",
    "pet_hook.py",
    "codex_hook.py",
)


def app_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")


def hook_command(agent, event):
    target = app_path()
    if getattr(sys, "frozen", False):
        return f'"{target}" hook {agent} {event}'
    return f'py -3 "{target}" hook {agent} {event}'


def _load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(path, cfg):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as src, \
                open(path + ".bak", "w", encoding="utf-8") as dst:
            dst.write(src.read())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _handler_command(handler):
    return " ".join(str(handler.get(k, "")) for k in
                    ("command", "commandWindows", "command_windows"))


def _is_own(entry):
    for h in entry.get("hooks", []):
        cmd = _handler_command(h)
        if any(mark in cmd for mark in OWN_MARKS):
            return True
    return False


def _strip_own(cfg):
    hooks = cfg.get("hooks", {})
    for ev in list(hooks.keys()):
        hooks[ev] = [e for e in hooks[ev] if not _is_own(e)]
        if not hooks[ev]:
            del hooks[ev]


def _install_claude():
    cfg = _load_json(CLAUDE_SETTINGS)
    _strip_own(cfg)
    hooks = cfg.setdefault("hooks", {})
    for ev, has_matcher in CLAUDE_HOOK_EVENTS:
        entry = {
            "hooks": [{
                "type": "command",
                "command": hook_command("claude", ev),
                "timeout": 10,
            }]
        }
        if has_matcher:
            entry["matcher"] = "*"
        hooks.setdefault(ev, []).append(entry)
    _save_json(CLAUDE_SETTINGS, cfg)
    return CLAUDE_SETTINGS


def _install_codex():
    cfg = _load_json(CODEX_HOOKS)
    _strip_own(cfg)
    hooks = cfg.setdefault("hooks", {})
    for ev, matcher in CODEX_HOOK_EVENTS:
        command = hook_command("codex", ev)
        entry = {
            "hooks": [{
                "type": "command",
                "command": command,
                "commandWindows": command,
                "timeout": 10,
                "statusMessage": "Updating desktop pet",
            }]
        }
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(ev, []).append(entry)
    _save_json(CODEX_HOOKS, cfg)
    return CODEX_HOOKS


def _create_startup(make_startup):
    if not (make_startup and getattr(sys, "frozen", False)):
        return "源码模式跳过"
    target = app_path()
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
          "$s.TargetPath = '{exe}'; $s.WorkingDirectory = '{wd}'; $s.Save()"
          ).format(lnk=STARTUP_LNK, exe=target, wd=os.path.dirname(target))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True)
    return "已创建"


def _normalize_target(target):
    target = (target or "all").lower()
    if target not in ("all", "claude", "codex"):
        raise ValueError("target must be one of: all, claude, codex")
    return target


def install(target="all", make_startup=True):
    target = _normalize_target(target)
    paths = []
    if target in ("all", "claude"):
        paths.append(_install_claude())
    if target in ("all", "codex"):
        paths.append(_install_codex())
    startup = _create_startup(make_startup)
    return ("已安装 desktop-pet hooks：{}\n开机自启：{}\n"
            "新开的 Claude Code / Codex 会话即可驱动同一只桌宠。"
            ).format(", ".join(paths), startup)


def _uninstall_path(path):
    cfg = _load_json(path)
    _strip_own(cfg)
    _save_json(path, cfg)


def uninstall(target="all"):
    target = _normalize_target(target)
    paths = []
    if target in ("all", "claude"):
        _uninstall_path(CLAUDE_SETTINGS)
        paths.append(CLAUDE_SETTINGS)
    if target in ("all", "codex"):
        _uninstall_path(CODEX_HOOKS)
        paths.append(CODEX_HOOKS)
    if os.path.exists(STARTUP_LNK):
        os.remove(STARTUP_LNK)
    return "已卸载 desktop-pet hooks 与开机自启：{}（原配置已备份为 .bak）。".format(
        ", ".join(paths))
