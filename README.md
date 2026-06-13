# Claude Desktop Pet (Clawd) 🦀

![platform](https://img.shields.io/badge/platform-Windows-0078D6)
![python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![deps](https://img.shields.io/badge/dependencies-none-success)
![license](https://img.shields.io/badge/license-MIT-green)

一只住在桌面上的像素小螃蟹 Clawd（Claude Code 官方吉祥物形象），**沿屏幕四边绕圈
爬行**，通过 Claude Code hooks 实时显示 Claude 的工作状态。零第三方依赖（纯
`tkinter + ctypes`），打包为免 Python 环境的安装式 exe。

> Windows 专用。鼠标停在小克身上它会停下让你摸一摸，移开继续爬。

## 状态行为

| 状态 | 触发 | 表现 |
|------|------|------|
| thinking | 提交提示词 / 工具间隙 | 沿边散步 + `thinking...` |
| working | 工具执行中 | 沿边快走 + `⚙ 工具名` |
| asking | AskUserQuestion/ExitPlanMode、权限请求 | 朝屏内每秒起跳 + `⁉ 等你确认` |
| done | 回合结束 | 跳跃庆祝 + `done ✓` 6 秒 → 静止 |
| idle | 空闲 | 缓慢巡游绕屏，无气泡 |
| sleeping | 30 分钟无活动 | `zZz` |

## 绕屏爬行

空闲与工作时（idle 慢巡游 / thinking 散步 / working 快走）沿屏幕**四条边绕圈爬行**：
底边 → 走到拐角自动转向、贴着侧壁向上爬 → 顶边倒挂行走 → 另一侧壁向下 → 回到底边，
形成绕屏一周的闭环。asking/done/sleeping 等"等你确认/休息"状态静止贴在当前所处的边
上（可能停在侧壁或顶部）。

实现：螃蟹中心点沿"屏幕四边内缩矩形"轨道运动，按所处的边把像素精灵旋转
0/90/180/270° 使脚始终朝向墙壁，气泡与跳跃统一朝屏幕内侧弹出。

交互：**鼠标悬停暂停**（停在小克身上即停下，移开继续）；**双击回到 Claude 终端
窗口**（自动还原最小化并置前，多终端时指向最近活动的会话）；左键拖拽拎起（松手沿
重力落回底边）；右键菜单：原地待命 / **反向爬行** / 测试各状态 / 退出。

> 双击原理：hook 进程由 Claude Code 拉起，沿父进程链 AttachConsole 定位承载它的
> 终端窗口（兼容传统 conhost / Windows Terminal / VS Code），HWND 随状态一起写入
> status.json，桌宠双击时 SetForegroundWindow。

## 下载 / 快速开始

从 [Releases](../../releases) 下载 `claude-pet.zip`，解压到任意目录（整个文件夹即
程序，无需安装 Python），然后：

```
claude-pet.exe              # 启动桌宠
claude-pet.exe install      # 写入全局 hooks（exec 直启，免 shell）+ 开机自启
claude-pet.exe uninstall    # 清理 hooks 与自启（settings.json 自动备份 .bak）
claude-pet.exe hook <Event> # hook 模式，由 Claude Code 调用，无需手动执行
```

一般流程：解压 → 双击 `claude-pet.exe` 看到小克 → 跑一次 `claude-pet.exe install`
让它随 Claude Code 状态变化并开机自启。

`install` 注册的是 exe 的当前绝对路径——移动文件夹后重新跑一次 `install` 即可。
hooks 写在用户级 `~/.claude/settings.json`，因此**所有终端、所有项目**的
Claude Code 会话都驱动同一只桌宠（多会话并发时最后写入者生效）。

## 从源码运行

无需打包即可开发运行（Windows + Python 3.8+，无第三方依赖）：

```
py -3 clawd_pet.py          # 直接启动桌宠 GUI
py -3 main.py install       # 同 exe 的 install
```

## 架构

```
Claude Code ──hooks(exec)──▶ claude-pet.exe hook <Event> ──写──▶ ~/.claude/pet/status.json
                                                                      ▲
桌面上的 claude-pet.exe（tkinter GUI）──每 0.5s 轮询──────────────────┘
```

源码（开发模式可直接 `py -3 clawd_pet.py` 运行）：

- `main.py` — exe 统一入口（GUI / hook / install / uninstall 分发）
- `clawd_pet.py` — 桌宠 GUI：像素渲染、四边绕屏爬行/旋转贴壁/跳跃动画、气泡、状态机
- `pet_hook.py` — hook 事件 → 状态映射（含 Notification 分类、AskUserQuestion 特判）
- `installer.py` — settings.json 合并/清理 + 开机自启快捷方式

重新打包：`py -3 -m PyInstaller --noconfirm --onedir --windowed --name claude-pet main.py`
（onedir 而非 onefile：hook 每次事件都要启动 exe，onedir 免解压、启动快一个量级）

## 借鉴的开源项目

- [clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk) — Electron 桌宠 + hook 集成思路
- [claude-code-mascot-statusline](https://github.com/TeXmeijin/claude-code-mascot-statusline) — 状态行像素宠物的状态机设计
- [openpets](https://github.com/alvinunreal/openpets) — MCP/hook 状态桥接方案

## License

[MIT](LICENSE) © lingxiansen2

Clawd 形象版权归 Anthropic 所有；本项目为非官方的社区作品，与 Anthropic 无隶属关系。
