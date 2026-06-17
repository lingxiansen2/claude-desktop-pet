# -*- coding: utf-8 -*-
"""claude-pet 统一入口。

claude-pet.exe              启动桌宠
claude-pet.exe hook <Event> Claude Code hook 模式（stdin 传事件 JSON）
claude-pet.exe install      写入全局 hooks + 开机自启
claude-pet.exe uninstall    清理 hooks 与自启
"""
import ctypes
import sys


def msgbox(text, title="claude-pet"):
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)


def report(text):
    """命令行调用（stdout 被重定向）时打印；双击运行时弹窗。"""
    if sys.stdout is not None:
        try:
            print(text)
            return
        except OSError:
            pass
    msgbox(text)


def _crashlog(where):
    """把异常写到 ~/.claude/pet/crash.log，便于排查（绝不再抛出）。"""
    import os
    import time
    import traceback
    try:
        p = os.path.join(os.path.expanduser("~"), ".claude", "pet", "crash.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("\n--- %s @ %s ---\n%s\n" % (where, time.ctime(), traceback.format_exc()))
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if not args:
        try:
            from clawd_pet import PetManager
            PetManager().run()
        except Exception:
            _crashlog("gui")            # GUI 启动/运行异常 → 记日志，静默退出
    elif args[0] == "hook":
        # hook 由 Claude Code 拉起，无论如何不能因异常影响 Claude
        try:
            from pet_hook import run_hook
            run_hook(args[1] if len(args) > 1 else "unknown")
        except Exception:
            _crashlog("hook")
    elif args[0] == "install":
        try:
            import installer
            report(installer.install())
        except Exception:
            _crashlog("install")
            report("安装出错，详情见 ~/.claude/pet/crash.log")
    elif args[0] == "uninstall":
        try:
            import installer
            report(installer.uninstall())
        except Exception:
            _crashlog("uninstall")
            report("卸载出错，详情见 ~/.claude/pet/crash.log")
    else:
        report(__doc__)


if __name__ == "__main__":
    main()
