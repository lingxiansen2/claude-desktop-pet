# -*- coding: utf-8 -*-
"""claude-pet 安装/卸载：把 hook 配置合并进 ~/.claude/settings.json。

安装时使用 exec 形式（command + args 直接拉起 exe，不经 shell），
并清理本项目旧版的 `py -3 pet_hook.py` 条目；其他 hooks 原样保留。
"""
import json
import os
import subprocess
import sys

SETTINGS = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
STARTUP_LNK = os.path.join(
    os.path.expanduser("~"),
    "AppData", "Roaming", "Microsoft", "Windows",
    "Start Menu", "Programs", "Startup", "claude-pet.lnk")

# (事件, 是否带 matcher)
HOOK_EVENTS = [
    ("UserPromptSubmit", False),
    ("PreToolUse", True),
    ("PostToolUse", True),
    ("Notification", True),
    ("PermissionRequest", True),
    ("Stop", False),
    ("SessionEnd", False),
]
# 识别本项目 hook 条目的特征（旧 py 版 + exe 版）
OWN_MARKS = ("pet_hook.py", "claude-pet.exe", "claude-pet-hook.exe")


def exe_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)


def _load_settings():
    if os.path.exists(SETTINGS):
        with open(SETTINGS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_settings(cfg):
    if os.path.exists(SETTINGS):
        with open(SETTINGS, "r", encoding="utf-8") as src, \
                open(SETTINGS + ".bak", "w", encoding="utf-8") as dst:
            dst.write(src.read())
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS)


def _is_own(entry):
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if any(m in cmd for m in OWN_MARKS):
            return True
    return False


def _strip_own(cfg):
    hooks = cfg.get("hooks", {})
    for ev in list(hooks.keys()):
        hooks[ev] = [e for e in hooks[ev] if not _is_own(e)]
        if not hooks[ev]:
            del hooks[ev]


def install(make_startup=True):
    exe = exe_path()
    cfg = _load_settings()
    _strip_own(cfg)
    hooks = cfg.setdefault("hooks", {})
    for ev, has_matcher in HOOK_EVENTS:
        entry = {
            "hooks": [{
                "type": "command",
                "command": exe,
                "args": ["hook", ev],
                "async": True,
                "timeout": 10,
            }]
        }
        if has_matcher:
            entry["matcher"] = "*"
        hooks.setdefault(ev, []).append(entry)
    _save_settings(cfg)

    if make_startup and getattr(sys, "frozen", False):
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
              "$s.TargetPath = '{exe}'; $s.WorkingDirectory = '{wd}'; $s.Save()"
              ).format(lnk=STARTUP_LNK, exe=exe, wd=os.path.dirname(exe))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True)

    return ("已安装：hooks 写入 {}\n开机自启：{}\n"
            "新开的 Claude Code 会话即可驱动桌宠。"
            ).format(SETTINGS, "已创建" if make_startup else "跳过")


def uninstall():
    cfg = _load_settings()
    _strip_own(cfg)
    _save_settings(cfg)
    if os.path.exists(STARTUP_LNK):
        os.remove(STARTUP_LNK)
    return "已卸载：hooks 条目与开机自启已移除（settings.json 已备份为 .bak）。"
