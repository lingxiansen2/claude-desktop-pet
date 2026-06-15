# Desktop Pet

![platform](https://img.shields.io/badge/platform-Windows-0078D6)
![python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![deps](https://img.shields.io/badge/dependencies-none-success)
![license](https://img.shields.io/badge/license-MIT-green)

一只住在桌面上的像素桌宠，沿屏幕四边绕圈爬行，通过 Claude Code 和 Codex hooks
实时显示 agent 工作状态。当前内置 Miku Upgraded 像素形象，包含站立、行走、侧边、
顶边方向帧。

> Windows 专用。鼠标停在桌宠身上会暂停，移开继续爬。

## 状态行为

| 状态 | 触发 | 表现 |
|------|------|------|
| thinking | 提交提示词 / 工具间隙 | 沿边散步 + `thinking...` |
| working | 工具执行中 / 子代理运行中 | 沿边快走 + `⚙ 工具名` |
| asking | 权限请求 / 等待用户输入 | 朝屏内每秒起跳 + `⁉ 等你确认` |
| done | 回合结束 | 跳跃庆祝 + `done ✓` 6 秒 → 静止 |
| idle | 新会话启动 / 空闲 | 缓慢巡游绕屏，无气泡 |
| sleeping | 30 分钟无活动 | `zZz` |

## 交互

- 鼠标悬停暂停，离开继续爬行
- 双击回到最近活动的 Claude Code / Codex 终端或窗口
- 左键拖拽拎起，松手沿重力落回底边
- 右键菜单：原地待命 / 反向爬行 / 测试各状态 / 退出

## 快速开始

源码运行（Windows + Python 3.8+，无第三方依赖）：

```powershell
py -3 desktop_pet.py            # 直接启动桌宠 GUI
py -3 main.py install           # 同时安装 Claude Code + Codex hooks
py -3 main.py install claude    # 只安装 Claude Code hooks
py -3 main.py install codex     # 只安装 Codex hooks
py -3 main.py uninstall         # 清理本项目 hooks
```

安装后，Claude Code 和 Codex hooks 都写入同一个状态文件：

```text
%USERPROFILE%\.desktop-pet\status.json
```

首次安装或修改 Codex hooks 后，Codex 可能要求在 `/hooks` 中信任该 hook。

## 打包

```powershell
py -3 -m PyInstaller --noconfirm --onedir --windowed --name desktop-pet --add-data "assets;assets" main.py
```

打包后：

```powershell
desktop-pet.exe                         # 启动桌宠
desktop-pet.exe install                 # 安装 Claude Code + Codex hooks
desktop-pet.exe install claude          # 只安装 Claude Code hooks
desktop-pet.exe install codex           # 只安装 Codex hooks
desktop-pet.exe uninstall               # 清理 hooks 与自启
desktop-pet.exe hook <agent> <Event>    # hook 模式，由 agent 调用
```

## 架构

```text
Claude Code hooks ─┐
                   ├──▶ desktop-pet hook <agent> <Event> ──写──▶ ~/.desktop-pet/status.json
Codex hooks ───────┘                                               ▲
                                                                    │
桌面上的 desktop-pet（tkinter GUI）──每 0.5s 轮询───────────────────┘
```

源码：

- `main.py` — 统一入口（GUI / hook / install / uninstall）
- `desktop_pet.py` — 桌宠 GUI：贴图帧动画、四边绕屏爬行、方向旋转、气泡、状态机
- `desktop_hook.py` — Claude Code / Codex hook 事件到状态文件的桥接
- `installer.py` — 合并/清理 Claude Code `settings.json` 与 Codex `hooks.json`
- `assets/miku-upgraded/` — Miku Upgraded 桌宠贴图资产

## License

[MIT](LICENSE) © lingxiansen2

内置 Miku Upgraded 资产来自本地 `awesome-codex-pet` 下载资源的增强版本，按源资源
CC BY-NC 4.0 / fan-art 限制用于个人非商业场景。本项目为非官方社区作品，与 Anthropic、
OpenAI 或相关角色权利方无隶属关系。
