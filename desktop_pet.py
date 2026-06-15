# -*- coding: utf-8 -*-
"""Desktop Pet：沿屏幕四边绕圈爬行，实时显示 Claude Code / Codex 状态。

状态来源：hooks 写入 ~/.desktop-pet/status.json（见 desktop_hook.py），
本程序每 0.5s 轮询该文件并切换形象/气泡。
零第三方依赖，仅用 tkinter + ctypes（Windows）。

运动设计：
- idle 缓慢巡游 / thinking 散步 / working 快走，沿屏幕四边绕圈爬行（底→侧壁向上→
  顶边倒挂→另一侧壁向下→回到底，闭环）；asking/done/sleeping 静止贴在当前位置
- 贴壁渲染：角色中心沿四边矩形轨道运动，窗口跟随但始终完整留在工作区内（分层透明
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

TRANSPARENT = "#ff00ff"         # 窗口透明色
CANVAS_W, CANVAS_H = 300, 260   # 画布；角色居中，四周留出气泡/跳跃空间
TICK_MS = 30                    # 动画帧间隔（~33fps）
POLL_TICKS = 16                 # 每 16 帧（约 0.5s）读一次状态文件
SLEEP_AFTER = 1800              # 状态文件超过 30 分钟未更新 → 睡觉
DONE_SHOW = 6                   # done 气泡展示秒数，之后静止待命
HOP_V0 = 4.5                    # 起跳初速度（px/帧）
HOP_G = 0.45                    # 跳跃重力（px/帧²）
FALL_G = 0.9                    # 拖拽释放下落重力（窗口坐标）

STATUS_FILE = os.path.join(os.path.expanduser("~"), ".desktop-pet", "status.json")
RESOURCE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(RESOURCE_ROOT, "assets", "miku-upgraded")
IDLE_ROW = os.path.join(ASSET_DIR, "idle-row.png")
IDLE_ROW_LEFT = os.path.join(ASSET_DIR, "idle-row-left.png")
IDLE_ROW_TOP = os.path.join(ASSET_DIR, "idle-row-top.png")
IDLE_ROW_RIGHT = os.path.join(ASSET_DIR, "idle-row-right.png")
WALK_BOTTOM_RIGHT = os.path.join(ASSET_DIR, "walk-bottom-right.png")
WALK_BOTTOM_LEFT = os.path.join(ASSET_DIR, "walk-bottom-left.png")
WALK_TOP_RIGHT = os.path.join(ASSET_DIR, "walk-top-right.png")
WALK_TOP_LEFT = os.path.join(ASSET_DIR, "walk-top-left.png")
WALK_LEFT_UP = os.path.join(ASSET_DIR, "walk-left-up.png")
WALK_LEFT_DOWN = os.path.join(ASSET_DIR, "walk-left-down.png")
WALK_RIGHT_UP = os.path.join(ASSET_DIR, "walk-right-up.png")
WALK_RIGHT_DOWN = os.path.join(ASSET_DIR, "walk-right-down.png")
FRAME_W, FRAME_H = 192, 208
IDLE_FRAME_COUNT = 6            # idle-row 后两格为空帧，播放它们会短暂消失
WALK_FRAME_COUNT = 8
SPRITE_SUBSAMPLE = 2            # 192x208 -> 96x104，桌面上更精致也不挡视线
IDLE_ROWS = {
    "bottom": (IDLE_ROW, FRAME_W, FRAME_H),
    "left": (IDLE_ROW_LEFT, FRAME_H, FRAME_W),
    "top": (IDLE_ROW_TOP, FRAME_W, FRAME_H),
    "right": (IDLE_ROW_RIGHT, FRAME_H, FRAME_W),
}
WALK_ROWS = {
    ("bottom", "right"): (WALK_BOTTOM_RIGHT, FRAME_W, FRAME_H),
    ("bottom", "left"): (WALK_BOTTOM_LEFT, FRAME_W, FRAME_H),
    ("top", "right"): (WALK_TOP_RIGHT, FRAME_W, FRAME_H),
    ("top", "left"): (WALK_TOP_LEFT, FRAME_W, FRAME_H),
    ("left", "up"): (WALK_LEFT_UP, FRAME_H, FRAME_W),
    ("left", "down"): (WALK_LEFT_DOWN, FRAME_H, FRAME_W),
    ("right", "up"): (WALK_RIGHT_UP, FRAME_H, FRAME_W),
    ("right", "down"): (WALK_RIGHT_DOWN, FRAME_H, FRAME_W),
}

BUBBLE_STYLE = {
    "thinking": ("#3b3b4f", "#f5e9d8"),
    "working":  ("#2e4a3f", "#d8f5e3"),
    "asking":   ("#8a3b2e", "#ffe9d8"),
    "done":     ("#2e6b3f", "#e3ffd8"),
    "sleeping": ("#3b3b4f", "#cfcfe8"),
    "hello":    ("#3b3b4f", "#f5e9d8"),
}

# ---- 四边绕行参数（屏幕坐标，顺时针 bottom→left→top→right）----
# 屏幕内侧法向（画布像素方向），用于气泡弹出与跳跃偏移
NORMAL = {"bottom": (0, -1), "top": (0, 1), "left": (1, 0), "right": (-1, 0)}
# 顺时针前进时，该边自由坐标的变化符号
CW_SIGN = {"bottom": -1, "left": -1, "top": 1, "right": 1}
NEXT_CW = {"bottom": "left", "left": "top", "top": "right", "right": "bottom"}
NEXT_CCW = {v: k for k, v in NEXT_CW.items()}


def get_work_area():
    """主显示器工作区（不含任务栏）。"""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


class DesktopPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.root, width=CANVAS_W, height=CANVAS_H,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.idle_frames_by_edge, self.walk_frames_by_motion = self._load_frames()
        self.sprite_size = {
            edge: (frames[0].width(), frames[0].height())
            for edge, frames in self.idle_frames_by_edge.items()
        }

        self.wa_l, self.wa_t, self.wa_r, self.wa_b = get_work_area()

        # 按边缘方向使用旋转后的真实贴图尺寸，让脚始终朝向边框。
        bottom_w, bottom_h = self.sprite_size["bottom"]
        left_w, left_h = self.sprite_size["left"]
        top_w, top_h = self.sprite_size["top"]
        right_w, right_h = self.sprite_size["right"]
        self.x_lo = self.wa_l + max(bottom_w, top_w) // 2
        self.x_hi = self.wa_r - max(bottom_w, top_w) // 2
        self.y_lo = self.wa_t + max(left_h, right_h) // 2
        self.y_hi = self.wa_b - max(left_h, right_h) // 2
        self.cx_left = self.wa_l + left_w // 2
        self.cx_right = self.wa_r - right_w // 2
        self.cy_top = self.wa_t + top_h // 2
        self.cy_bottom = self.wa_b - bottom_h // 2

        # 行为状态
        self.state = "hello"
        self.detail = ""
        self.state_ts = time.time()
        self.status_mtime = 0.0
        self.edge = "bottom"                # 当前所在的边
        self.cw = random.choice([True, False])  # 绕行方向（顺/逆时针）
        self.cx = random.randint(self.x_lo, self.x_hi)  # 角色中心（屏幕坐标）
        self.cy = self.cy_bottom
        self.pause_until = time.time() + 2
        self.tick_n = 0
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

        self.agent_hwnd = 0                # 最近活动的 agent 终端/窗口句柄
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", self._focus_agent)
        self.canvas.bind("<Button-3>", self._menu)
        # 透明窗口只有不透明像素(角色本体)才收到鼠标事件，故 Enter/Leave 即
        # "鼠标停在小克身上/离开"：悬停时停下让你摸一摸，移开继续爬
        self.canvas.bind("<Enter>", self._hover_on)
        self.canvas.bind("<Leave>", self._hover_off)

        # 前台锁定超时设为 0：允许本进程把别的窗口拉到前台
        ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 3)

        self._place()
        self.root.after(TICK_MS, self._tick)

    def _load_frames(self):
        """加载 Miku Upgraded 四方向 spritesheet，裁成 Tk 原生 PhotoImage 帧。"""
        idle_frames = {
            edge: self._load_frame_row(path, frame_w, frame_h, IDLE_FRAME_COUNT)
            for edge, (path, frame_w, frame_h) in IDLE_ROWS.items()
        }
        walk_frames = {}
        for key, (path, frame_w, frame_h) in WALK_ROWS.items():
            walk_frames[key] = self._load_frame_row(path, frame_w, frame_h,
                                                    WALK_FRAME_COUNT)
        return idle_frames, walk_frames

    def _load_frame_row(self, path, frame_w, frame_h, max_count):
        sheet = tk.PhotoImage(file=path)
        count = max(1, min(max_count, sheet.width() // frame_w))
        frames = []
        for i in range(count):
            frame = tk.PhotoImage(width=frame_w, height=frame_h)
            frame.tk.call(frame, "copy", sheet, "-from",
                          i * frame_w, 0, (i + 1) * frame_w, frame_h,
                          "-to", 0, 0)
            if SPRITE_SUBSAMPLE > 1:
                frame = frame.subsample(SPRITE_SUBSAMPLE, SPRITE_SUBSAMPLE)
            frames.append(frame)
        return frames

    def _frame_index(self, frames):
        if self.walking:
            pace = 3 if self.state == "working" else 4
            return (self.tick_n // pace) % len(frames)
        if self.state == "sleeping":
            return 2 if len(frames) > 2 else 0
        if self.state == "asking":
            base = 4 if len(frames) > 4 else 0
            return base + ((self.tick_n // 18) % min(2, len(frames) - base))
        if self.state == "done":
            return 5 if len(frames) > 5 else len(frames) - 1
        if self.state in ("idle", "thinking", "working"):
            pace = 5 if self.state == "working" else 8
            return (self.tick_n // pace) % len(frames)
        return 0

    def _motion_direction(self):
        sign = CW_SIGN[self.edge] * (1 if self.cw else -1)
        if self.edge in ("bottom", "top"):
            return "right" if sign > 0 else "left"
        return "down" if sign > 0 else "up"

    def _current_frames(self):
        if self.walking:
            key = (self.edge, self._motion_direction())
            return self.walk_frames_by_motion.get(key,
                                                  self.idle_frames_by_edge[self.edge])
        return self.idle_frames_by_edge[self.edge]

    # ---------- 状态读取 ----------
    def _poll_status(self):
        if self.frozen_state is not None:
            self._set_state(self.frozen_state, "(测试)")
            return
        try:
            mtime = os.path.getmtime(STATUS_FILE)
            if mtime == self.status_mtime and self.state != "hello":
                if time.time() - mtime > SLEEP_AFTER:
                    self._set_state("sleeping", "")
                elif self.state == "done" and time.time() - self.state_ts > DONE_SHOW:
                    self._set_state("idle", "")
                return
            self.status_mtime = mtime
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("hwnd"):
                self.agent_hwnd = data["hwnd"]
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
        x = self.root.winfo_pointerx() - self._dx
        y = self.root.winfo_pointery() - self._dy
        self.cx = x + CANVAS_W // 2
        self.cy = y + CANVAS_H // 2
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self.dragging = False
        self.falling = True                # 松手沿重力落回底边
        self.fall_v = 0.0

    def _focus_agent(self, _e=None):
        """双击：把最近活动的 agent 终端/窗口还原并置前。"""
        hwnd = self.agent_hwnd
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
            self.root.after(180, lambda: self._focus_try(hwnd, attempt + 1))

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="恢复爬行" if self.paused else "原地待命",
                      command=self._toggle_pause)
        m.add_command(label="反向爬行", command=self._flip_dir)
        test = tk.Menu(m, tearoff=0)
        for s in ("thinking", "working", "asking", "done", "sleeping"):
            test.add_command(label=s, command=lambda s=s: self._freeze(s))
        test.add_command(label="取消测试", command=lambda: self._freeze(None))
        m.add_cascade(label="测试状态", menu=test)
        m.add_separator()
        m.add_command(label="退出", command=self.root.destroy)
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

    # ---------- 主循环 ----------
    def _tick(self):
        self.tick_n += 1
        if self.tick_n % POLL_TICKS == 0:
            self._poll_status()
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
        self.root.after(TICK_MS, self._tick)

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
        self.root.geometry(f"+{x}+{y}")

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
    def _draw(self):
        # 角色在画布内的中心 = 屏幕中心 - 窗口左上角（贴边时窗口被钳住，
        # 这个值随之偏移，让角色紧贴墙壁而不出画布）
        scx = int(self.cx - self.win_x)
        scy = int(self.cy - self.win_y)
        frames = self._current_frames()
        frame_idx = self._frame_index(frames)
        text = self._bubble_text()
        sig = (self.edge, frame_idx, int(self.hop_dy), scx, scy, text, self.state)
        if sig == self._last_sig:        # 画面无变化 → 跳过重绘（仅窗口在平移）
            return
        self._last_sig = sig

        c = self.canvas
        c.delete("all")
        frame = frames[frame_idx]
        sw, sh = frame.width(), frame.height()
        nx, ny = NORMAL[self.edge]
        ox = scx - sw // 2 + int(nx * self.hop_dy)
        oy = scy - sh // 2 + int(ny * self.hop_dy)
        c.create_image(ox, oy, image=frame, anchor="nw")
        self._draw_bubble(c, (ox, oy, ox + sw, oy + sh))

    def _bubble_text(self):
        dots = "." * (1 + (self.tick_n // 14) % 3)
        if self.state == "thinking":
            return "thinking" + dots
        if self.state == "working":
            return f"⚙ {self.detail or 'working'}"
        if self.state == "asking":
            msg = (self.detail or "")[:20]
            return f"⁉ 等你确认{': ' + msg if msg else ''}"
        if self.state == "done":
            return "done ✓"
        if self.state == "sleeping":
            return "zZz" + dots
        if self.state == "hello":
            return "Hi~ 我是 Desktop Pet"
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

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    DesktopPet().run()
