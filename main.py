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


def main():
    args = sys.argv[1:]
    if not args:
        from clawd_pet import ClawdPet
        ClawdPet().run()
    elif args[0] == "hook":
        from pet_hook import run_hook
        run_hook(args[1] if len(args) > 1 else "unknown")
    elif args[0] == "install":
        import installer
        report(installer.install())
    elif args[0] == "uninstall":
        import installer
        report(installer.uninstall())
    else:
        report(__doc__)


if __name__ == "__main__":
    main()
