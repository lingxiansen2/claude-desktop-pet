# -*- coding: utf-8 -*-
"""Clawd 桌面宠物：每个 Claude Code 会话一只螃蟹，沿屏幕四边绕圈爬行。

状态来源：Claude Code hooks 按会话写入 ~/.claude/pet/sessions/<session_id>.json
（见 pet_hook.py）。PetManager 每 0.5s 轮询该目录：
- 出现新会话文件 → 生成一只螃蟹（独立 Toplevel 窗口）
- 会话结束（文件被删）/ 终端窗口关闭 / 文件长期不更新 → 移除对应螃蟹
- 没有任何会话时 → 保留一只待机螃蟹，让桌面上始终有小克陪着

每只螃蟹各自爬行、各自气泡、各自双击跳回它所属的终端。
零第三方依赖，仅用 tkinter + ctypes（Windows）。

运动设计：
- idle 缓慢巡游 / thinking 散步 / working 快走，沿屏幕四边绕圈爬行（底→侧壁向上→
  顶边倒挂→另一侧壁向下→回到底，闭环）；asking/done/sleeping 静止贴在当前位置
- 贴壁渲染：螃蟹中心沿四边矩形轨道运动，窗口跟随但始终完整留在工作区内（分层透明
  窗口移出屏幕外会整体不渲染→消失），贴边时窗口钳住、改在画布内部偏移绘制；按所处
  的边把像素精灵旋转 0/90/180/270°，让脚始终朝向墙壁，气泡朝屏幕内侧弹出
- 性能：平移时画面内容不变，用画面签名跳过无意义重绘，避免每帧重建像素块
- asking 约每秒朝屏幕内侧起跳提醒；done 起跳庆祝一次 + 气泡 6 秒，然后静止待命
- 左键拖拽可拎起，松手沿重力落回底边
"""
import ctypes
import ctypes.wintypes
import json
import os
import random
import sys
import time
import tkinter as tk

SCALE = 5                       # 每个像素格的边长
TRANSPARENT = "#ff00ff"         # 窗口透明色
ORANGE = "#D97757"              # Claude 品牌橙
EYE = "#5C2E1A"
CANVAS_W, CANVAS_H = 260, 200   # 画布；螃蟹居中，四周留出气泡/跳跃空间
TICK_MS = 30                    # 动画帧间隔（~33fps）
POLL_TICKS = 16                 # 每 16 帧（约 0.5s）扫一次会话目录
SLEEP_AFTER = 300               # 会话文件超过 5 分钟未更新(无对话) → 睡觉
DEAD_AFTER = 6 * 3600           # 会话文件超过 6 小时未更新 → 视为已死，移除螃蟹
DONE_SHOW = 6                   # done 气泡展示秒数，之后静止待命
HOP_V0 = 4.5                    # 起跳初速度（px/帧）
HOP_G = 0.45                    # 跳跃重力（px/帧²）
FALL_G = 0.9                    # 拖拽释放下落重力（窗口坐标）

PET_DIR = os.path.join(os.path.expanduser("~"), ".claude", "pet")
SESSIONS_DIR = os.path.join(PET_DIR, "sessions")

# 桌宠形象："cat"=月薪喵(沿屏幕底部漫步，移动时跳散味舞) / "crab"=像素螃蟹(沿屏边爬)
PET_KIND = os.environ.get("CLAWD_PET_KIND", "cat")

# 月薪喵参数
CAT_CW, CAT_CH = 280, 280               # 窗口画布(够大以容纳贴壁旋转后的精灵+气泡)
DANCE_FRAME_TICKS = 2                   # 每 2 帧(~16fps)切一张抖动帧


def asset_dir():
    """资源目录：打包(frozen)时在 sys._MEIPASS/assets，源码运行时在脚本同级 assets。"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# 像素画：X=身体 e=眼睛 .=透明（参照 Claude Code 里的 Clawd 形象，脚朝下）
BODY = [
    "..XX....XX..",
    "..XX....XX..",
    ".XXXXXXXXXX.",
    ".XXXXXXXXXX.",
    ".XXeXXXXeXX.",
    ".XXXXXXXXXX.",
    ".XXXXXXXXXX.",
]
LEGS_A = ".XX.XX.XX.XX"
LEGS_B = "XX.XX.XX.XX."

BUBBLE_STYLE = {
    "thinking": ("#3b3b4f", "#f5e9d8"),
    "working":  ("#2e4a3f", "#d8f5e3"),
    "asking":   ("#8a3b2e", "#ffe9d8"),
    "done":     ("#2e6b3f", "#e3ffd8"),
    "sleeping": ("#3b3b4f", "#cfcfe8"),
    "hello":    ("#3b3b4f", "#f5e9d8"),
}

# ---- 四边绕行参数（屏幕坐标，顺时针 bottom→left→top→right）----
# 旋转圈数 k（顺时针 90°/次）：让脚朝向所在墙壁
ROT_K = {"bottom": 0, "left": 1, "top": 2, "right": 3}
# 屏幕内侧法向（画布像素方向），用于气泡弹出与跳跃偏移
NORMAL = {"bottom": (0, -1), "top": (0, 1), "left": (1, 0), "right": (-1, 0)}
# 顺时针前进时，该边自由坐标的变化符号
CW_SIGN = {"bottom": -1, "left": -1, "top": 1, "right": 1}
NEXT_CW = {"bottom": "left", "left": "top", "top": "right", "right": "bottom"}
NEXT_CCW = {v: k for k, v in NEXT_CW.items()}


def _rot(grid, k):
    """把像素网格（list[str]）顺时针旋转 k*90°。"""
    for _ in range(k % 4):
        grid = ["".join(col) for col in zip(*grid[::-1])]
    return grid


def get_work_area():
    """主显示器工作区（不含任务栏）。"""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def _is_window(hwnd):
    return bool(hwnd) and bool(ctypes.windll.user32.IsWindow(hwnd))


class Crab:
    """一只螃蟹 = 一个会话。拥有独立的 Toplevel 窗口、状态机与动画。"""

    def __init__(self, mgr, session_id):
        self.mgr = mgr
        self.session_id = session_id          # None = 待机螃蟹（无会话绑定）
        self.status_file = (
            os.path.join(SESSIONS_DIR, session_id + ".json")
            if session_id is not None else None)

        # 共享几何（同一块屏幕，由 manager 算好）
        self.wa_l, self.wa_t, self.wa_r, self.wa_b = mgr.wa_l, mgr.wa_t, mgr.wa_r, mgr.wa_b
        self.x_lo, self.x_hi = mgr.x_lo, mgr.x_hi
        self.y_lo, self.y_hi = mgr.y_lo, mgr.y_hi
        self.cx_left, self.cx_right = mgr.cx_left, mgr.cx_right
        self.cy_top, self.cy_bottom = mgr.cy_top, mgr.cy_bottom

        self.win = tk.Toplevel(mgr.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.win, width=CANVAS_W, height=CANVAS_H,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()

        # 行为状态
        self.state = "hello"
        self.detail = ""
        self.project = ""
        self.state_ts = time.time()
        self.status_mtime = 0.0
        self.edge = "bottom"                # 当前所在的边
        self.cw = random.choice([True, False])  # 绕行方向（顺/逆时针）
        self.cx = random.randint(self.x_lo, self.x_hi)  # 螃蟹中心（屏幕坐标）
        self.cy = self.cy_bottom
        self.pause_until = time.time() + 2
        self.tick_n = random.randint(0, 64)   # 错开各螃蟹的动画相位
        self.walking = False               # 本帧是否在走（控制摆腿动画）
        self.hop_dy = 0.0                  # 跳跃高度（沿内侧法向，>=0）
        self.hop_v = 0.0                   # 跳跃速度（向内为正）
        self.fall_v = 0.0                  # 拖拽释放后的下落速度
        self.dragging = False
        self.falling = False
        self.paused = False
        self.hover = False                 # 鼠标悬停在小克身上时暂停爬行
        self.frozen_state = None           # 右键菜单的测试状态覆盖
        self.win_x = self.win_y = 0        # 当前窗口左上角（始终留在屏幕内）
        self._last_sig = None              # 上一帧画面签名，用于跳过无变化重绘

        self.claude_hwnd = 0               # 本会话的 Claude 终端窗口句柄
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", self._focus_claude)
        self.canvas.bind("<Button-3>", self._menu)
        # 透明窗口只有不透明像素(小克本体)才收到鼠标事件，故 Enter/Leave 即
        # "鼠标停在小克身上/离开"：悬停时停下让你摸一摸，移开继续爬
        self.canvas.bind("<Enter>", self._hover_on)
        self.canvas.bind("<Leave>", self._hover_off)

        self._place()

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ---------- 状态读取 ----------
    def poll(self):
        if self.frozen_state is not None:
            self._set_state(self.frozen_state, "(测试)")
            return
        if self.status_file is None:           # 待机螃蟹：无会话文件，停在 idle
            if self.state == "hello" and time.time() - self.state_ts > 3:
                self._set_state("idle", "")
            return
        try:
            mtime = os.path.getmtime(self.status_file)
            if mtime == self.status_mtime and self.state != "hello":
                if time.time() - mtime > SLEEP_AFTER:
                    self._set_state("sleeping", "")
                elif self.state == "done" and time.time() - self.state_ts > DONE_SHOW:
                    self._set_state("idle", "")
                return
            self.status_mtime = mtime
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("hwnd"):
                self.claude_hwnd = data["hwnd"]
            self.project = data.get("project", "") or ""
            self._set_state(data.get("state", "idle"), data.get("detail", ""))
        except (OSError, ValueError):
            if self.state == "hello" and time.time() - self.state_ts > 3:
                self._set_state("idle", "")

    def _set_state(self, state, detail):
        if state == self.state and detail == self.detail:
            return
        if self.state == "hello" and time.time() - self.state_ts < 3:
            return
        self.state, self.detail = state, detail
        self.state_ts = time.time()
        if state in ("done", "asking"):
            self.hop_v = HOP_V0            # 起跳庆祝/提醒（朝屏幕内侧）

    # ---------- 交互 ----------
    def _drag_start(self, e):
        self.dragging = True
        self.falling = False
        self.edge = "bottom"               # 拎在手里时恢复正立
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.win.winfo_pointerx() - self._dx
        y = self.win.winfo_pointery() - self._dy
        self.cx = x + CANVAS_W // 2
        self.cy = y + CANVAS_H // 2
        self.win.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self.dragging = False
        self.falling = True                # 松手沿重力落回底边
        self.fall_v = 0.0

    def _focus_claude(self, _e=None):
        """双击：把本会话的 Claude 终端窗口还原并置前。"""
        hwnd = self.claude_hwnd
        u32 = ctypes.windll.user32
        if not (hwnd and u32.IsWindow(hwnd)):
            return
        buf = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, buf, 64)
        if buf.value == "PseudoConsoleWindow":   # ConPTY 伪窗口，不可聚焦
            return
        self._focus_try(hwnd, 1)

    def _focus_try(self, hwnd, attempt):
        """跨进程 ShowWindow/SetForegroundWindow 是异步的，
        需延时复查、失败重试（最后一次用最小化-还原兜底）。"""
        u32 = ctypes.windll.user32
        if not u32.IsWindow(hwnd):
            return
        if u32.GetForegroundWindow() == hwnd:
            return
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)            # SW_RESTORE
        elif attempt >= 3:
            u32.ShowWindow(hwnd, 6)            # SW_MINIMIZE
            u32.ShowWindow(hwnd, 9)            # SW_RESTORE
        u32.keybd_event(0x12, 0, 0, 0)
        u32.keybd_event(0x12, 0, 2, 0)         # KEYEVENTF_KEYUP
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        if attempt < 3:
            self.win.after(180, lambda: self._focus_try(hwnd, attempt + 1))

    def _menu(self, e):
        m = tk.Menu(self.win, tearoff=0)
        m.add_command(label="恢复爬行" if self.paused else "原地待命",
                      command=self._toggle_pause)
        m.add_command(label="反向爬行", command=self._flip_dir)
        test = tk.Menu(m, tearoff=0)
        for s in ("thinking", "working", "asking", "done", "sleeping"):
            test.add_command(label=s, command=lambda s=s: self._freeze(s))
        test.add_command(label="取消测试", command=lambda: self._freeze(None))
        m.add_cascade(label="测试状态", menu=test)
        m.add_separator()
        m.add_command(label="全部退出", command=self.mgr.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _hover_on(self, _e=None):
        self.hover = True

    def _hover_off(self, _e=None):
        self.hover = False

    def _toggle_pause(self):
        self.paused = not self.paused

    def _flip_dir(self):
        self.cw = not self.cw

    def _freeze(self, s):
        self.frozen_state = s
        if s is None:
            self.status_mtime = 0.0   # 强制重读文件

    # ---------- 每帧推进（由 manager 调用）----------
    def tick(self):
        self.tick_n += 1
        self.walking = False
        if not self.dragging:
            if self.falling:
                self._fall()
            else:
                self._move()
            self._hop()
            # asking 状态：约每秒朝屏幕内侧起跳一次提醒
            if (self.state == "asking" and self.hop_dy == 0 and self.hop_v == 0
                    and self.tick_n % 33 == 0):
                self.hop_v = HOP_V0
        self._draw()

    def _speed(self):
        """空闲也缓慢绕屏巡游，工作时更快；asking/done/sleeping/hello 静止待命。
        暂停或鼠标悬停在小克身上时停下。"""
        if self.paused or self.hover:
            return 0
        return {"idle": 3, "thinking": 5, "working": 8}.get(self.state, 0)

    def _move(self):
        speed = self._speed()
        if speed == 0:
            return
        now = time.time()
        if now < self.pause_until:
            return
        if random.random() < 0.004:            # 偶尔中途歇脚
            self.pause_until = now + random.uniform(0.8, 3.0)
            return
        self.walking = True
        self._advance(speed)
        self._place()

    def _advance(self, speed):
        """沿当前边推进；越过拐角则转向下一条边。"""
        e = self.edge
        horiz = e in ("bottom", "top")
        sign = CW_SIGN[e] * (1 if self.cw else -1)
        if horiz:
            self.cx += sign * speed
            lo, hi, val = self.x_lo, self.x_hi, self.cx
        else:
            self.cy += sign * speed
            lo, hi, val = self.y_lo, self.y_hi, self.cy
        if (sign > 0 and val >= hi) or (sign < 0 and val <= lo):
            corner = hi if sign > 0 else lo
            if horiz:
                self.cx = corner
            else:
                self.cy = corner
            self.edge = NEXT_CW[e] if self.cw else NEXT_CCW[e]
            self.pause_until = time.time() + random.uniform(0.15, 0.5)

    def _place(self):
        """按当前边把固定坐标钉到墙上、自由坐标限幅，并落实窗口位置。"""
        e = self.edge
        if e in ("bottom", "top"):
            self.cy = self.cy_bottom if e == "bottom" else self.cy_top
            self.cx = min(max(self.cx, self.x_lo), self.x_hi)
        else:
            self.cx = self.cx_left if e == "left" else self.cx_right
            self.cy = min(max(self.cy, self.y_lo), self.y_hi)
        self._apply()

    def _apply(self):
        """窗口始终完整留在工作区内（分层透明窗口移出屏幕外会整体不渲染）；
        贴边时窗口被钳住不动，靠 _draw 在画布内部偏移来贴墙，故不会"消失"。"""
        x = min(max(int(self.cx - CANVAS_W // 2), self.wa_l), self.wa_r - CANVAS_W)
        y = min(max(int(self.cy - CANVAS_H // 2), self.wa_t), self.wa_b - CANVAS_H)
        self.win_x, self.win_y = x, y
        self.win.geometry(f"+{x}+{y}")

    def _fall(self):
        """拖拽释放后沿重力落回底边（脚朝下）。"""
        self.fall_v += FALL_G
        self.cy = min(self.cy + self.fall_v, self.cy_bottom)
        self.cx = min(max(self.cx, self.x_lo), self.x_hi)
        if self.cy >= self.cy_bottom:
            self.cy = self.cy_bottom
            self.fall_v = 0.0
            self.falling = False
            self.edge = "bottom"
        self._apply()

    def _hop(self):
        """沿内侧法向的跳跃动画（不改变贴壁位置，流畅）。"""
        if self.hop_v or self.hop_dy:
            self.hop_dy += self.hop_v
            self.hop_v -= HOP_G
            if self.hop_dy <= 0:
                self.hop_dy = 0.0
                self.hop_v = 0.0

    # ---------- 绘制 ----------
    def _sprite_rows(self):
        legs = LEGS_A if (not self.walking or (self.tick_n // 5) % 2 == 0) else LEGS_B
        rows = list(BODY) + [legs]
        blink = self.state == "sleeping" or (self.tick_n % 130) in (0, 1, 2, 3)
        if blink:
            rows = [r.replace("e", "X") for r in rows]
        return rows

    def _draw(self):
        # 螃蟹在画布内的中心 = 屏幕中心 - 窗口左上角（贴边时窗口被钳住，
        # 这个值随之偏移，让螃蟹紧贴墙壁而不出画布）
        scx = int(self.cx - self.win_x)
        scy = int(self.cy - self.win_y)
        legs_key = (self.tick_n // 5) % 2 if self.walking else -1
        blink = self.state == "sleeping" or (self.tick_n % 130) in (0, 1, 2, 3)
        text = self._bubble_text()
        sig = (self.edge, legs_key, blink, int(self.hop_dy), scx, scy, text, self.state)
        if sig == self._last_sig:        # 画面无变化 → 跳过重绘（仅窗口在平移）
            return
        self._last_sig = sig

        c = self.canvas
        c.delete("all")
        rows = _rot(self._sprite_rows(), ROT_K[self.edge])
        sw, sh = len(rows[0]) * SCALE, len(rows) * SCALE
        nx, ny = NORMAL[self.edge]
        ox = scx - sw // 2 + int(nx * self.hop_dy)
        oy = scy - sh // 2 + int(ny * self.hop_dy)
        for ry, row in enumerate(rows):
            for rx, ch in enumerate(row):
                if ch == ".":
                    continue
                x0 = ox + rx * SCALE
                y0 = oy + ry * SCALE
                c.create_rectangle(x0, y0, x0 + SCALE, y0 + SCALE,
                                   fill=EYE if ch == "e" else ORANGE, width=0)
        self._draw_bubble(c, (ox, oy, ox + sw, oy + sh))

    def _bubble_text(self):
        dots = "." * (1 + (self.tick_n // 14) % 3)
        # 多会话时带上项目名，让你一眼看出是哪个项目在喊你
        tag = f"[{self.project[:12]}] " if self.project else ""
        if self.state == "thinking":
            return tag + "thinking" + dots
        if self.state == "working":
            return f"{tag}⚙ {self.detail or 'working'}"
        if self.state == "asking":
            msg = (self.detail or "")[:16]
            return f"{tag}⁉ 等你确认{': ' + msg if msg else ''}"
        if self.state == "done":
            return tag + "done ✓"
        if self.state == "sleeping":
            return "zZz" + dots
        if self.state == "hello":
            return "Hi~ 我是 Clawd"
        return None   # idle 无气泡

    def _draw_bubble(self, c, sb):
        """气泡朝屏幕内侧（当前边的内法向）弹出，避免被屏幕边缘裁切。"""
        text = self._bubble_text()
        if not text:
            return
        bg, fg = BUBBLE_STYLE.get(self.state, ("#3b3b4f", "#f5e9d8"))
        t = c.create_text(0, 0, text=text, font=("Segoe UI", 9, "bold"),
                          fill=fg, anchor="nw")
        x0, y0, x1, y1 = c.bbox(t)
        pad = 6
        bw, bh = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
        ox, oy, ex, ey = sb
        scx, scy = (ox + ex) // 2, (oy + ey) // 2
        gap = 8
        if self.edge == "bottom":
            bx, by = scx - bw // 2, oy - bh - gap
        elif self.edge == "top":
            bx, by = scx - bw // 2, ey + gap
        elif self.edge == "left":
            bx, by = ex + gap, scy - bh // 2
        else:  # right
            bx, by = ox - bw - gap, scy - bh // 2
        bx = min(max(bx, 2), CANVAS_W - bw - 2)
        by = min(max(by, 2), CANVAS_H - bh - 2)
        c.create_rectangle(bx, by, bx + bw, by + bh, fill=bg, outline=bg)
        c.coords(t, bx + pad, by + pad)
        c.tag_raise(t)


class CatPet:
    """月薪喵：像螃蟹一样沿屏幕四边绕圈爬行（底/侧壁/顶，按边旋转贴壁），移动时循环
    播放散味舞抖动帧。由会话状态驱动；默认持续巡逻（始终走→始终跳），睡眠/暂停/被摸时停。"""

    _imgs = None        # {edge: {"base": PhotoImage, "dance": [PhotoImage,...]}}
    _sleep = None       # {"base": PhotoImage, "frames": [PhotoImage,...]}(整场景睡姿,正立)
    SW = SH = 0

    def __init__(self, mgr, session_id):
        self.mgr = mgr
        self.session_id = session_id
        self.sid4 = (session_id or "")[:4]           # 短会话ID(区分同项目的多会话)
        self.status_file = (
            os.path.join(SESSIONS_DIR, session_id + ".json")
            if session_id is not None else None)
        CatPet._load_images()
        sw, sh = CatPet.SW, CatPet.SH
        self.wa_l, self.wa_t, self.wa_r, self.wa_b = mgr.wa_l, mgr.wa_t, mgr.wa_r, mgr.wa_b
        # 轨道几何(同螃蟹公式)：水平精灵 sw×sh，竖直(旋转后) sh×sw
        w_h, h_h = sw, sh
        w_v, h_v = sh, sw
        self.x_lo = self.wa_l + w_h // 2
        self.x_hi = self.wa_r - w_h // 2
        self.y_lo = self.wa_t + h_v // 2
        self.y_hi = self.wa_b - h_v // 2
        self.cx_left = self.wa_l + w_v // 2
        self.cx_right = self.wa_r - w_v // 2
        self.cy_top = self.wa_t + h_h // 2
        self.cy_bottom = self.wa_b - h_h // 2

        self.win = tk.Toplevel(mgr.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.win, width=CAT_CW, height=CAT_CH,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()

        self.state = "hello"
        self.detail = ""
        self.project = ""
        self.state_ts = time.time()
        self.status_mtime = 0.0
        self.edge = "bottom"                         # 当前所在的边
        self.cw = random.choice([True, False])       # 绕行方向(顺/逆时针)
        self.cx = random.randint(self.x_lo, self.x_hi)
        self.cy = self.cy_bottom
        self.pause_until = time.time() + 2
        self.tick_n = random.randint(0, 64)
        self.frame_i = 0
        self.sleep_i = 0
        self.walking = False
        self.hop_dy = 0.0
        self.hop_v = 0.0
        self.fall_v = 0.0
        self.dragging = False
        self.falling = False
        self.paused = False
        self.hover = False
        self.frozen_state = None
        self.win_x = self.win_y = 0
        self.claude_hwnd = 0
        self._last_sig = None

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", self._focus_claude)
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Enter>", lambda e: setattr(self, "hover", True))
        self.canvas.bind("<Leave>", lambda e: setattr(self, "hover", False))
        self._place()

    @classmethod
    def _load_images(cls):
        if cls._imgs is not None:
            return
        d = os.path.join(asset_dir(), "yuexinmao", "dance")
        meta = {}
        try:
            with open(os.path.join(d, "meta.txt")) as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if v.isdigit():
                        meta[k] = int(v)
        except OSError:
            pass
        imgs = {}
        for edge in ("bottom", "left", "top", "right"):
            ed = os.path.join(d, edge)
            names = sorted(n for n in os.listdir(ed)
                           if n.startswith("cat_") and n.endswith(".png"))
            imgs[edge] = {
                "base": tk.PhotoImage(file=os.path.join(ed, "base.png")),
                "dance": [tk.PhotoImage(file=os.path.join(ed, n)) for n in names],
            }
        cls._imgs = imgs
        cls.SW = meta.get("SW", 168)
        cls.SH = meta.get("SH", 150)
        # 睡觉精灵(整场景:猫+椅子+桌子+笔记本，正立一套)
        sd = os.path.join(asset_dir(), "yuexinmao", "sleep")
        try:
            sn = sorted(n for n in os.listdir(sd)
                        if n.startswith("cat_") and n.endswith(".png"))
            cls._sleep = {
                "base": tk.PhotoImage(file=os.path.join(sd, "base.png")),
                "frames": [tk.PhotoImage(file=os.path.join(sd, n)) for n in sn],
            } if sn else None
        except OSError:
            cls._sleep = None

    def destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    # ---------- 状态读取（与 Crab 一致）----------
    def poll(self):
        if self.frozen_state is not None:
            self._set_state(self.frozen_state, "(测试)")
            return
        if self.status_file is None:
            if self.state == "hello" and time.time() - self.state_ts > 3:
                self._set_state("idle", "")
            return
        try:
            mtime = os.path.getmtime(self.status_file)
            if mtime == self.status_mtime and self.state != "hello":
                if time.time() - mtime > SLEEP_AFTER:
                    self._set_state("sleeping", "")
                elif self.state == "done" and time.time() - self.state_ts > DONE_SHOW:
                    self._set_state("idle", "")
                return
            self.status_mtime = mtime
            with open(self.status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("hwnd"):
                self.claude_hwnd = data["hwnd"]
            self.project = data.get("project", "") or ""
            self._set_state(data.get("state", "idle"), data.get("detail", ""))
        except (OSError, ValueError):
            if self.state == "hello" and time.time() - self.state_ts > 3:
                self._set_state("idle", "")

    def _set_state(self, state, detail):
        if state == self.state and detail == self.detail:
            return
        if self.state == "hello" and time.time() - self.state_ts < 3:
            return
        self.state, self.detail = state, detail
        self.state_ts = time.time()
        if state in ("done", "asking"):
            self.hop_v = HOP_V0            # 朝屏幕内侧蹦一下(庆祝/提醒)

    # ---------- 交互 ----------
    def _drag_start(self, e):
        self.dragging = True
        self.falling = False
        self.edge = "bottom"               # 拎在手里时恢复正立
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.win.winfo_pointerx() - self._dx
        y = self.win.winfo_pointery() - self._dy
        self.cx = x + CAT_CW // 2
        self.cy = y + CAT_CH // 2
        self.win.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self.dragging = False
        self.falling = True                # 松手沿重力落回底边
        self.fall_v = 0.0

    def _focus_claude(self, _e=None):
        hwnd = self.claude_hwnd
        u32 = ctypes.windll.user32
        if not (hwnd and u32.IsWindow(hwnd)):
            return
        buf = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(hwnd, buf, 64)
        if buf.value == "PseudoConsoleWindow":
            return
        self._focus_try(hwnd, 1)

    def _focus_try(self, hwnd, attempt):
        u32 = ctypes.windll.user32
        if not u32.IsWindow(hwnd):
            return
        if u32.GetForegroundWindow() == hwnd:
            return
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)
        elif attempt >= 3:
            u32.ShowWindow(hwnd, 6)
            u32.ShowWindow(hwnd, 9)
        u32.keybd_event(0x12, 0, 0, 0)
        u32.keybd_event(0x12, 0, 2, 0)
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        if attempt < 3:
            self.win.after(180, lambda: self._focus_try(hwnd, attempt + 1))

    def _menu(self, e):
        m = tk.Menu(self.win, tearoff=0)
        m.add_command(label="恢复漫步" if self.paused else "原地待命",
                      command=lambda: setattr(self, "paused", not self.paused))
        m.add_command(label="反向绕圈", command=lambda: setattr(self, "cw", not self.cw))
        test = tk.Menu(m, tearoff=0)
        for s in ("thinking", "working", "asking", "done", "sleeping"):
            test.add_command(label=s, command=lambda s=s: self._freeze(s))
        test.add_command(label="取消测试", command=lambda: self._freeze(None))
        m.add_cascade(label="测试状态", menu=test)
        m.add_separator()
        m.add_command(label="全部退出", command=self.mgr.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _freeze(self, s):
        self.frozen_state = s
        if s is None:
            self.status_mtime = 0.0

    # ---------- 每帧推进 ----------
    def _speed(self):
        # 默认就沿底栏来回巡逻(始终走→始终跳散味舞)；睡眠/暂停/被摸时才停
        if self.paused or self.hover or self.state == "sleeping":
            return 0
        return {"thinking": 5, "working": 8}.get(self.state, 4)

    def tick(self):
        self.tick_n += 1
        self.walking = False
        if not self.dragging:
            if self.falling:
                self._fall()
            else:
                self._move()
            self._hop()
            if (self.state == "asking" and self.hop_dy == 0 and self.hop_v == 0
                    and self.tick_n % 33 == 0):
                self.hop_v = HOP_V0
        if self.walking and self.tick_n % DANCE_FRAME_TICKS == 0:
            n = len(CatPet._imgs[self.edge]["dance"]) or 1
            self.frame_i = (self.frame_i + 1) % n      # 移动 → 跳散味舞
        if self.state == "sleeping" and CatPet._sleep and self.tick_n % 5 == 0:
            n = len(CatPet._sleep["frames"]) or 1
            self.sleep_i = (self.sleep_i + 1) % n      # 睡觉 → 缓慢呼吸
        self._draw()

    def _move(self):
        speed = self._speed()
        if speed == 0:
            return
        now = time.time()
        if now < self.pause_until:
            return
        if random.random() < 0.004:            # 偶尔中途歇脚
            self.pause_until = now + random.uniform(0.8, 3.0)
            return
        self.walking = True
        self._advance(speed)
        self._place()

    def _advance(self, speed):
        """沿当前边推进；越过拐角则转向下一条边。"""
        e = self.edge
        horiz = e in ("bottom", "top")
        sign = CW_SIGN[e] * (1 if self.cw else -1)
        if horiz:
            self.cx += sign * speed
            lo, hi, val = self.x_lo, self.x_hi, self.cx
        else:
            self.cy += sign * speed
            lo, hi, val = self.y_lo, self.y_hi, self.cy
        if (sign > 0 and val >= hi) or (sign < 0 and val <= lo):
            corner = hi if sign > 0 else lo
            if horiz:
                self.cx = corner
            else:
                self.cy = corner
            self.edge = NEXT_CW[e] if self.cw else NEXT_CCW[e]
            self.pause_until = time.time() + random.uniform(0.15, 0.5)

    def _place(self):
        e = self.edge
        if e in ("bottom", "top"):
            self.cy = self.cy_bottom if e == "bottom" else self.cy_top
            self.cx = min(max(self.cx, self.x_lo), self.x_hi)
        else:
            self.cx = self.cx_left if e == "left" else self.cx_right
            self.cy = min(max(self.cy, self.y_lo), self.y_hi)
        self._apply()

    def _apply(self):
        x = min(max(int(self.cx - CAT_CW // 2), self.wa_l), self.wa_r - CAT_CW)
        y = min(max(int(self.cy - CAT_CH // 2), self.wa_t), self.wa_b - CAT_CH)
        self.win_x, self.win_y = x, y
        self.win.geometry(f"+{x}+{y}")

    def _fall(self):
        self.fall_v += FALL_G
        self.cy = min(self.cy + self.fall_v, self.cy_bottom)
        self.cx = min(max(self.cx, self.x_lo), self.x_hi)
        if self.cy >= self.cy_bottom:
            self.cy = self.cy_bottom
            self.fall_v = 0.0
            self.falling = False
            self.edge = "bottom"
        self._apply()

    def _hop(self):
        if self.hop_v or self.hop_dy:
            self.hop_dy += self.hop_v
            self.hop_v -= HOP_G
            if self.hop_dy <= 0:
                self.hop_dy = 0.0
                self.hop_v = 0.0

    # ---------- 绘制 ----------
    def _draw(self):
        scx = int(self.cx - self.win_x)
        scy = int(self.cy - self.win_y)
        text = self._bubble_text()
        sleeping = self.state == "sleeping" and CatPet._sleep
        sig = (self.edge, self.frame_i if self.walking else -1,
               self.sleep_i if sleeping else -1,
               int(self.hop_dy), scx, scy, text, self.state)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        c = self.canvas
        c.delete("all")
        if sleeping:
            sf = CatPet._sleep["frames"]
            img = sf[self.sleep_i % len(sf)] if sf else CatPet._sleep["base"]
            dx, dy = scx, scy                          # 整场景睡姿，正立居中
        else:
            eset = CatPet._imgs[self.edge]
            img = (eset["dance"][self.frame_i % len(eset["dance"])]
                   if self.walking and eset["dance"] else eset["base"])
            nx, ny = NORMAL[self.edge]
            dx = scx + int(nx * self.hop_dy)
            dy = scy + int(ny * self.hop_dy)
        c.create_image(dx, dy, image=img)
        iw, ih = img.width(), img.height()
        self._draw_bubble(c, text, (dx - iw // 2, dy - ih // 2,
                                    dx + iw // 2, dy + ih // 2))

    def _bubble_text(self):
        """常显气泡：[项目 #会话ID] + 当前状态，标明是哪个 Claude 对话。"""
        dots = "." * (1 + (self.tick_n // 14) % 3)
        proj = self.project[:12]
        ident = proj + (" #" + self.sid4 if self.sid4 else "")
        tag = f"[{ident}] " if ident else ""
        s, d = self.state, self.detail
        if s == "thinking":
            body = "thinking" + dots
        elif s == "working":
            body = f"⚙ {d or 'working'}"
        elif s == "asking":
            msg = (d or "")[:16]
            body = "⁉ 等你确认" + (": " + msg if msg else "")
        elif s == "done":
            body = "done ✓"
        elif s == "sleeping":
            body = "zZz" + dots
        elif s == "hello":
            body = "Hi~ 月薪喵"
        else:                                  # idle
            body = "待命中"
        return tag + body if tag else ("月薪喵 " + body if s == "idle" else body)

    def _draw_bubble(self, c, text, sb):
        """气泡朝屏幕内侧(当前边的内法向)弹出，避免被屏幕边缘裁切。"""
        if not text:
            return
        bg, fg = BUBBLE_STYLE.get(self.state, ("#3b3b4f", "#f5e9d8"))
        t = c.create_text(0, 0, text=text, font=("Segoe UI", 9, "bold"),
                          fill=fg, anchor="nw")
        # 超长自动截断(末尾加…)：保留前面的对话标识，按实测像素宽收缩
        max_w = CAT_CW - 16
        if c.bbox(t)[2] - c.bbox(t)[0] > max_w:
            s = text
            while len(s) > 3:
                s = s[:-1]
                c.itemconfig(t, text=s + "…")
                bb = c.bbox(t)
                if bb[2] - bb[0] <= max_w:
                    break
        x0, y0, x1, y1 = c.bbox(t)
        pad = 6
        bw, bh = (x1 - x0) + pad * 2, (y1 - y0) + pad * 2
        ox, oy, ex, ey = sb
        mcx, mcy = (ox + ex) // 2, (oy + ey) // 2
        gap = 8
        if self.edge == "bottom":
            bx, by = mcx - bw // 2, oy - bh - gap
        elif self.edge == "top":
            bx, by = mcx - bw // 2, ey + gap
        elif self.edge == "left":
            bx, by = ex + gap, mcy - bh // 2
        else:  # right
            bx, by = ox - bw - gap, mcy - bh // 2
        bx = min(max(bx, 2), CAT_CW - bw - 2)
        by = min(max(by, 2), CAT_CH - bh - 2)
        c.create_rectangle(bx, by, bx + bw, by + bh, fill=bg, outline=bg)
        c.coords(t, bx + pad, by + pad)
        c.tag_raise(t)


class PetManager:
    """统管所有桌宠：轮询会话目录，按会话生死增删，驱动统一动画循环。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()               # 隐藏根窗口，只显示各螃蟹的 Toplevel

        self.wa_l, self.wa_t, self.wa_r, self.wa_b = get_work_area()
        # 精灵像素尺寸：水平(底/顶) 12x8，竖直(侧壁) 8x12
        rows_n, cols_n = len(BODY) + 1, len(BODY[0])
        w_h, h_h = cols_n * SCALE, rows_n * SCALE       # 水平朝向 宽,高
        w_v, h_v = rows_n * SCALE, cols_n * SCALE       # 竖直朝向 宽,高
        # 螃蟹中心点可走的矩形轨道（贴壁：脚正好压在工作区边界上）
        self.x_lo = self.wa_l + w_h // 2
        self.x_hi = self.wa_r - w_h // 2
        self.y_lo = self.wa_t + h_v // 2
        self.y_hi = self.wa_b - h_v // 2
        self.cx_left = self.wa_l + w_v // 2
        self.cx_right = self.wa_r - w_v // 2
        self.cy_top = self.wa_t + h_h // 2
        self.cy_bottom = self.wa_b - h_h // 2

        self.crabs = {}                    # session_id(None=待机) -> 桌宠实例
        self._pet_class = CatPet if PET_KIND == "cat" else Crab
        self.frame = 0

        # 前台锁定超时设为 0：允许本进程把别的窗口拉到前台
        ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 3)

        os.makedirs(SESSIONS_DIR, exist_ok=True)
        self._sync_sessions()              # 启动即生成螃蟹（无会话则一只待机）
        self.root.after(TICK_MS, self._tick)

    def quit(self):
        self.root.destroy()

    def _present_sessions(self):
        """扫描会话目录，返回 {session_id: path}；顺手清掉过期死文件。"""
        now = time.time()
        present = {}
        try:
            names = os.listdir(SESSIONS_DIR)
        except OSError:
            return present
        for name in names:
            if not name.endswith(".json") or name.endswith(".tmp"):
                continue
            sid = name[:-5]
            path = os.path.join(SESSIONS_DIR, name)
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            if now - mt > DEAD_AFTER:       # 长期不更新 → 视为已死
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            present[sid] = path
        return present

    def _sync_sessions(self):
        present = self._present_sessions()

        # 移除已结束（文件没了）或终端已关闭的会话螃蟹
        for sid in list(self.crabs):
            if sid is None:
                continue
            crab = self.crabs[sid]
            gone = sid not in present
            closed = crab.claude_hwnd and not _is_window(crab.claude_hwnd)
            if gone or closed:
                if closed and sid in present:
                    try:
                        os.remove(present.pop(sid))
                    except OSError:
                        present.pop(sid, None)
                self.crabs.pop(sid).destroy()

        if present:
            # 有真实会话 → 撤掉待机螃蟹，为每个会话补齐螃蟹
            if None in self.crabs:
                self.crabs.pop(None).destroy()
            for sid in present:
                if sid not in self.crabs:
                    self.crabs[sid] = self._pet_class(self, sid)
        elif not self.crabs:
            # 没有任何会话 → 留一只待机桌宠陪着
            self.crabs[None] = self._pet_class(self, None)

        for crab in self.crabs.values():
            crab.poll()

    def _tick(self):
        self.frame += 1
        if self.frame % POLL_TICKS == 0:
            self._sync_sessions()
        for crab in list(self.crabs.values()):
            crab.tick()
        self.root.after(TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    PetManager().run()
