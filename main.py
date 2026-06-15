# -*- coding: utf-8 -*-
"""desktop-pet 统一入口。

desktop-pet.exe                         启动桌宠
desktop-pet.exe hook <agent> <Event>    hook 模式（stdin 传事件 JSON）
desktop-pet.exe install [claude|all|codex]    默认 claude
desktop-pet.exe uninstall [claude|all|codex]  默认 claude
"""
import ctypes
import sys


def msgbox(text, title="desktop-pet"):
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


def main():
    args = sys.argv[1:]
    if not args:
        from desktop_pet import PetManager
        PetManager().run()
    elif args[0] == "hook":
        from desktop_hook import run_hook
        agent = args[1] if len(args) > 1 else "codex"
        event = args[2] if len(args) > 2 else "unknown"
        run_hook(agent, event)
    elif args[0] == "install":
        import installer
        report(installer.install(args[1] if len(args) > 1 else "claude"))
    elif args[0] == "uninstall":
        import installer
        report(installer.uninstall(args[1] if len(args) > 1 else "claude"))
    else:
        report(__doc__)


if __name__ == "__main__":
    main()
