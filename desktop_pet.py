# -*- coding: utf-8 -*-
"""Desktop Pet：每个 agent 对话一只独立桌宠，沿屏幕四边绕圈爬行并显示状态。

架构：
- PetManager  单进程，持有唯一隐藏的 tk.Tk()，每 0.5s 扫描
  ~/.desktop-pet/sessions/*.json（每个 agent 会话一个状态文件，见 desktop_hook.py），
  按需创建/销毁 PetWindow，并把状态分发给对应窗口。
- PetWindow   一个 Toplevel + canvas，封装爬行/动画/气泡/拖拽/双击聚焦；它的
  状态与终端句柄来自“自己会话”的文件，故双击只聚焦自己的对话窗口（不再串台）。

生命周期：首次收到该会话事件即出现；SessionEnd 删除文件→销毁窗口；文件长时间
未更新（异常退出兜底）自动清理。零第三方依赖，仅用 tkinter + ctypes（Windows）。

运动设计：
- idle 缓慢巡游 / thinking 散步 / working 快走，沿屏幕四边绕圈爬行（底→侧壁向上→
  顶边倒挂→另一侧壁向下→回到底，闭环）；asking/done/sleeping 静止贴在当前位置
- 贴壁渲染：角色中心沿四边矩形轨道运动，窗口跟随但始终完整留在工作区内（分层透明
  窗口移出屏幕外会整体不渲染→消失），贴边时窗口钳住、改在画布内部偏移绘制；按所处
  的边把像素精灵旋转 0/90/180/270°，让脚始终朝向墙壁，气泡朝屏幕内侧弹出
- working/thinking 多行气泡显示“工作到哪里了”（当前/最近动作 + TODO 进度），顶部
  浅色标签 agent·目录名 区分多只；asking 约每秒起跳提醒；done 起跳庆祝
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
CANVAS_W, CANVAS_H = 300, 280   # 画布；角色居中，四周留出气泡/跳跃空间
TICK_MS = 30                    # 动画帧间隔（~33fps）
DIR_POLL_MS = 500              # 扫描会话目录间隔（0.5s）
SLEEP_AFTER = 1800             # 会话文件超过 30 分钟未更新 → 睡觉
REMOVE_AFTER = 5400           # 超过 90 分钟未更新 → 移除该桌宠（异常退出兜底）
DONE_SHOW = 6                   # done 气泡展示秒数，之后静止待命
HOP_V0 = 4.5                    # 起跳初速度（px/帧）
HOP_G = 0.45                    # 跳跃重力（px/帧²）
FALL_G = 0.9                    # 拖拽释放下落重力（窗口坐标）

SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".desktop-pet", "sessions")
LEGACY_STATUS = os.path.join(os.path.expanduser("~"), ".desktop-pet", "status.json")
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
    "idle":     ("#2f2f3a", "#b8b8c8"),
}
LABEL_FG = "#9aa0b5"            # 身份标签的浅色

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


def _short(s, n):
    s = str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


class PetManager:
    """单进程管理所有桌宠：扫描会话目录，按需增删 PetWindow。"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()                # 根窗口隐藏，只用它的事件循环
        self.idle_frames_by_edge, self.walk_frames_by_motion = self._load_frames()
        self.sprite_size = {
            edge: (frames[0].width(), frames[0].height())
            for edge, frames in self.idle_frames_by_edge.items()
        }
        self.wa_l, self.wa_t, self.wa_r, self.wa_b = get_work_area()
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

        # 前台锁定超时设为 0：允许本进程把别的窗口拉到前台
        ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(0), 3)

        os.makedirs(SESSIONS_DIR, exist_ok=True)
        try:
            os.remove(LEGACY_STATUS)        # 清理旧的单文件模式遗留
        except OSError:
            pass

        self.pets = {}                      # key -> PetWindow
        self.mtimes = {}                    # key -> 上次读到的文件 mtime
        self.spawn_count = 0
        self.root.after(DIR_POLL_MS, self._scan)

    def _load_frames(self):
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

    # ---------- 目录扫描 ----------
    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _scan(self):
        try:
            files = {fn[:-5]: os.path.join(SESSIONS_DIR, fn)
                     for fn in os.listdir(SESSIONS_DIR) if fn.endswith(".json")}
        except OSError:
            files = {}

        for key in list(self.pets):         # 文件消失 → 销毁对应窗口
            if key not in files:
                self._remove(key)

        now = time.time()
        for key, path in files.items():
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            pet = self.pets.get(key)
            if pet is None:
                pet = self._spawn(key, self._read(path))
                self.mtimes[key] = mtime
            elif mtime != self.mtimes.get(key):
                self.mtimes[key] = mtime
                pet.update_status(self._read(path))
            if now - mtime > REMOVE_AFTER:
                self._remove(key)
            else:
                pet.note_age(now - mtime)

        self.root.after(DIR_POLL_MS, self._scan)

    def _spawn(self, key, data):
        self.spawn_count += 1
        pet = PetWindow(self, key, self.spawn_count, data)
        self.pets[key] = pet
        return pet

    def _remove(self, key):
        self.mtimes.pop(key, None)
        pet = self.pets.pop(key, None)
        if pet:
            pet.destroy()

    def delete_session(self, key):
        """某只桌宠被手动关闭：删其会话文件并销毁窗口。"""
        try:
            os.remove(os.path.join(SESSIONS_DIR, key + ".json"))
        except OSError:
            pass
        self._remove(key)

    def run(self):
        self.root.mainloop()


class PetWindow:
    """单个会话对应的桌宠窗口（状态由 PetManager 注入，自己不读文件）。"""

    def __init__(self, mgr, key, index, data):
        self.mgr = mgr
        self.key = key
        self.root = tk.Toplevel(mgr.root)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = tk.Canvas(self.root, width=CANVAS_W, height=CANVAS_H,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()

        # 共享管理器的贴图与屏幕几何
        self.idle_frames_by_edge = mgr.idle_frames_by_edge
        self.walk_frames_by_motion = mgr.walk_frames_by_motion
        self.sprite_size = mgr.sprite_size
        for attr in ("wa_l", "wa_t", "wa_r", "wa_b", "x_lo", "x_hi", "y_lo",
                     "y_hi", "cx_left", "cx_right", "cy_top", "cy_bottom"):
            setattr(self, attr, getattr(mgr, attr))

        # 会话状态（来自文件）
        self.state = "idle"
        self.detail = ""
        self.todo = None
        self.history = []
        self.agent = ""
        self.cwd = ""
        self.agent_hwnd = 0
        self.state_ts = time.time()

        # 行为状态
        self.edge = "bottom"
        self.cw = random.choice([True, False])
        span = max(1, self.x_hi - self.x_lo)
        self.cx = self.x_lo + (index * 220) % span     # 多只错开起点
        self.cy = self.cy_bottom
        self.pause_until = time.time() + 1
        self.tick_n = 0
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
        self._last_sig = None

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Double-Button-1>", self._focus_agent)
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Enter>", self._hover_on)
        self.canvas.bind("<Leave>", self._hover_off)

        self.update_status(data)
        self._place()
        self._alive = True
        self.root.after(TICK_MS, self._tick)

    # ---------- 状态注入 ----------
    def update_status(self, data):
        if not data:
            return
        if data.get("hwnd"):
            self.agent_hwnd = data["hwnd"]
        self.agent = data.get("agent", self.agent) or self.agent
        self.cwd = data.get("cwd", self.cwd) or self.cwd
        self.todo = data.get("todo")
        self.history = data.get("history") or []
        self._set_state(data.get("state", "idle"), data.get("detail", ""))

    def _set_state(self, state, detail):
        if self.frozen_state is not None:
            return
        if state == self.state and detail == self.detail:
            return
        self.state, self.detail = state, detail
        self.state_ts = time.time()
        if state in ("done", "asking"):
            self.hop_v = HOP_V0            # 起跳庆祝/提醒（朝屏幕内侧）

    def note_age(self, age):
        """管理器每次扫描调用：处理睡觉与 done→idle 的超时。"""
        if self.frozen_state is not None:
            return
        if age > SLEEP_AFTER and self.state != "sleeping":
            self._set_state("sleeping", "")
        elif self.state == "done" and time.time() - self.state_ts > DONE_SHOW:
            self._set_state("idle", "")

    def destroy(self):
        self._alive = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ---------- 帧选择 ----------
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
            return self.walk_frames_by_motion.get(
                key, self.idle_frames_by_edge[self.edge])
        return self.idle_frames_by_edge[self.edge]

    # ---------- 交互 ----------
    def _drag_start(self, e):
        self.dragging = True
        self.falling = False
        self.edge = "bottom"
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_pointerx() - self._dx
        y = self.root.winfo_pointery() - self._dy
        self.cx = x + CANVAS_W // 2
        self.cy = y + CANVAS_H // 2
        self.root.geometry(f"+{x}+{y}")

    def _drag_end(self, _e):
        self.dragging = False
        self.falling = True
        self.fall_v = 0.0

    def _focus_agent(self, _e=None):
        """双击：把本会话的 agent 终端/窗口还原并置前。"""
        hwnd = self.agent_hwnd
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
        m.add_command(label="关闭此宠", command=lambda: self.mgr.delete_session(self.key))
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
        if s is not None:
            self.state, self.detail = s, "(测试)"
            self.state_ts = time.time()

    # ---------- 主循环 ----------
    def _tick(self):
        if not self._alive:
            return
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
        self._draw()
        self.root.after(TICK_MS, self._tick)

    def _speed(self):
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
        if random.random() < 0.004:
            self.pause_until = now + random.uniform(0.8, 3.0)
            return
        self.walking = True
        self._advance(speed)
        self._place()

    def _advance(self, speed):
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
        x = min(max(int(self.cx - CANVAS_W // 2), self.wa_l), self.wa_r - CANVAS_W)
        y = min(max(int(self.cy - CANVAS_H // 2), self.wa_t), self.wa_b - CANVAS_H)
        self.win_x, self.win_y = x, y
        self.root.geometry(f"+{x}+{y}")

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
    def _label(self):
        name = self.agent or "agent"
        proj = os.path.basename(self.cwd.rstrip("/\\")) if self.cwd else ""
        return _short(f"{name} · {proj}" if proj else name, 24)

    def _draw(self):
        scx = int(self.cx - self.win_x)
        scy = int(self.cy - self.win_y)
        frames = self._current_frames()
        frame_idx = self._frame_index(frames)
        content = self._bubble_text()
        label = self._label()
        sig = (self.edge, frame_idx, int(self.hop_dy), scx, scy,
               content, label, self.state)
        if sig == self._last_sig:
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
        self._draw_bubble(c, (ox, oy, ox + sw, oy + sh), label, content)

    def _bubble_text(self):
        dots = "." * (1 + (self.tick_n // 14) % 3)
        if self.state in ("working", "thinking"):
            return self._work_text(self.state, dots)
        if self.state == "asking":
            msg = (self.detail or "")[:20]
            return f"⁉ 等你确认{': ' + msg if msg else ''}"
        if self.state == "done":
            if self.todo and self.todo.get("total"):
                return "done ✓  {}/{}".format(
                    self.todo.get("done", 0), self.todo.get("total", 0))
            return "done ✓"
        if self.state == "sleeping":
            return "zZz" + dots
        return None   # idle 只显示身份标签

    def _work_text(self, state, dots):
        """working/thinking 多行：当前动作 / TODO 进度 / 最近历史。"""
        if state == "working":
            cur = self.detail or (self.history[0] if self.history else "working")
            lines = ["⚙ " + cur]
            past = self.history[1:3]
        else:
            cur = None
            lines = ["🤔 thinking" + dots]
            past = self.history[0:2]
        if self.todo:
            prog = "{}/{}".format(self.todo.get("done", 0),
                                  self.todo.get("total", 0))
            active = self.todo.get("active") or ""
            lines.append(("▸ " + prog + " " + active).rstrip())
        for h in past:
            if h and h != cur:
                lines.append("· " + h)
        return "\n".join(lines)

    def _draw_bubble(self, c, sb, label, content):
        """气泡朝屏幕内侧弹出；首行为浅色身份标签，其下为状态内容。"""
        bg, fg = BUBBLE_STYLE.get(self.state, ("#3b3b4f", "#f5e9d8"))
        # 标签行单独用浅色，内容行用状态色——分两个文本对象叠放
        lab = c.create_text(0, 0, text=label, font=("Segoe UI", 8, "bold"),
                            fill=LABEL_FG, anchor="nw")
        lx0, ly0, lx1, ly1 = c.bbox(lab)
        lw, lh = lx1 - lx0, ly1 - ly0
        cw_ = ch = 0
        body = None
        if content:
            body = c.create_text(0, 0, text=content, font=("Segoe UI", 9, "bold"),
                                fill=fg, anchor="nw", justify="left")
            bx0, by0, bx1, by1 = c.bbox(body)
            cw_, ch = bx1 - bx0, by1 - by0

        pad, gap_lc = 6, 3
        inner_w = max(lw, cw_)
        inner_h = lh + (gap_lc + ch if content else 0)
        bw, bh = inner_w + pad * 2, inner_h + pad * 2

        ox, oy, ex, ey = sb
        scx, scy = (ox + ex) // 2, (oy + ey) // 2
        gap = 8
        if self.edge == "bottom":
            bx, by = scx - bw // 2, oy - bh - gap
        elif self.edge == "top":
            bx, by = scx - bw // 2, ey + gap
        elif self.edge == "left":
            bx, by = ex + gap, scy - bh // 2
        else:
            bx, by = ox - bw - gap, scy - bh // 2
        bx = min(max(bx, 2), CANVAS_W - bw - 2)
        by = min(max(by, 2), CANVAS_H - bh - 2)
        c.create_rectangle(bx, by, bx + bw, by + bh, fill=bg, outline=bg)
        c.coords(lab, bx + pad, by + pad)
        c.tag_raise(lab)
        if body is not None:
            c.coords(body, bx + pad, by + pad + lh + gap_lc)
            c.tag_raise(body)


def main():
    PetManager().run()


if __name__ == "__main__":
    main()
