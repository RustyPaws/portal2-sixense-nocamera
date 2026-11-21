"""
Webcam hand tracker for the Portal 2 Sixense Perceptual Pack fake hand.

Replaces the Creative Senz3D: MediaPipe finds your hand in an ordinary webcam
image and the result is written ~20-30x/s into the shared memory block
"nocam_hand", which the proxy sixense.dll (hand/nocam_hand.c) feeds to the game.

    run.bat                      settings window with camera preview
    run.bat --no-gui             old preview window only (--no-preview: console)
    run.bat --hand Right ...     any setting can also be given on the command line

Settings are changed live in the window and saved to settings.json next to this
file (command-line options override the file for that run).

Make a FIST to grab, move it to move the object, tilt it to rotate, open the
hand to drop. Rotation is relative: however your fist is turned when you grab
counts as "straight"; only tilting it from there turns the object. Twisting the
fist around the axis pointing out of the palm is not seen by the game.

Shared memory layout (little endian, 128 bytes) - must match nocam_hand.c:
    0   u32  magic 'NCH1'
    4   u32  seq          (odd while writing, even when consistent)
    8   u64  tick_ms      (GetTickCount64 at write time)
    16  i32  valid        (1 = hand visible)
    20  i32  closed       (1 = fist)
    24  i32  openness     (0..100)
    28  f32  hand[3]      palm centre  (right, up, depth) metres
    40  f32  tip[3]       index fingertip
    52  f32  upper[3]     middle-finger knuckle ("upper hand")
    64  f32  normal[3]    palm normal  (right, up, depth), towards the camera = -depth
    76  u32  cfg_magic    'NCC1' when the fields below are valid
    80  f32  tip_height   live proxy setting (metres)
    84  i32  toggle_grab  live proxy setting (0/1)
"""
import argparse
import ctypes
import json
import math
import mmap
import os
import struct
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "settings.json")
SHM_NAME = "nocam_hand"
SHM_SIZE = 128
MAGIC = 0x3148434E      # 'NCH1'
CFG_MAGIC = 0x3143434E  # 'NCC1'
LAYOUT = struct.Struct("<IIQiii3f3f3f3fIfi")

GetTickCount64 = ctypes.windll.kernel32.GetTickCount64
GetTickCount64.restype = ctypes.c_uint64

# MediaPipe landmark ids
WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP, MIDDLE_TIP = 0, 4, 5, 8, 9, 12
RING_MCP, RING_TIP, PINKY_MCP, PINKY_TIP = 13, 16, 17, 20
PALM = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)
FINGERS = ((INDEX_MCP, INDEX_TIP), (MIDDLE_MCP, MIDDLE_TIP), (RING_MCP, RING_TIP), (PINKY_MCP, PINKY_TIP))
BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))

# ----------------------------------------------------------------- settings
DEFAULTS = {
    "camera": 0, "hand": "any", "fov": 65.0, "palm_width": 0.08,
    "depth_scale": 1.0, "move_scale": 1.0,
    "smooth": 1.0, "responsive": 1.0,
    "fist": 35.0, "release": 55.0, "toggle_grab": False,
    "rotate_mode": "relative", "rotate_gain": 1.5, "rotate_dead": 6.0, "flip_normal": False,
    "point_frames": 3, "tip_height": 0.045,
}

# (key, label, kind, min, max, step, hint) grouped by section - used by the GUI and --help
SPEC = [
    ("Сглаживание", [
        ("smooth", "Сглаживание в покое", "float", 0.2, 5.0, 0.1, "больше = спокойнее, когда рука стоит"),
        ("responsive", "Отзывчивость в движении", "float", 0.2, 5.0, 0.1, "больше = меньше задержка при быстрых движениях"),
    ]),
    ("Захват", [
        ("fist", "Кулак, если раскрытость ниже", "float", 5, 90, 1, "порог сжатия (0 = кулак, 100 = ладонь)"),
        ("release", "Ладонь, если раскрытость выше", "float", 10, 95, 1, "порог отпускания; держи выше порога кулака"),
        ("toggle_grab", "Оригинальный режим (сжал - взял, сжал - бросил)", "bool", 0, 0, 0,
         "выкл = держишь, пока кулак сжат"),
    ]),
    ("Вращение", [
        ("rotate_mode", "Режим", "choice", ("relative", "absolute", "point"), 0, 0,
         "relative: от позы в момент захвата; absolute: как есть; point: вращать указательным пальцем"),
        ("rotate_gain", "Усиление поворота", "float", 0.3, 4.0, 0.1, "куб поворачивается на столько градусов за градус кулака"),
        ("rotate_dead", "Мёртвая зона, градусы", "float", 0, 30, 1, "мелкие наклоны (дрожание) не крутят куб"),
        ("flip_normal", "Инвертировать вращение", "bool", 0, 0, 0, "если куб крутится в обратную сторону"),
    ]),
    ("Прицел и движение", [
        ("tip_height", "Высота пальца (прицел), м", "float", 0.0, 0.12, 0.005, "0.045 = пистолет прямо, больше = вверх"),
        ("move_scale", "Масштаб движения", "float", 0.3, 3.0, 0.05, "сколько игра двигает руку на сантиметр"),
        ("depth_scale", "Масштаб расстояния", "float", 0.5, 2.0, 0.05, "поправка оценки расстояния до руки"),
        ("palm_width", "Ширина ладони, м", "float", 0.05, 0.12, 0.002, "по костяшкам указательный - мизинец"),
    ]),
    ("Камера", [
        ("hand", "Рука", "choice", ("any", "Right", "Left"), 0, 0, "какую руку отслеживать"),
        ("camera", "Номер камеры", "int", 0, 5, 1, "0 = первая веб-камера"),
        ("fov", "Угол обзора камеры, °", "float", 40, 100, 1, "горизонтальный FOV веб-камеры"),
    ]),
]


def load_settings(path=SETTINGS_FILE):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_settings(cfg, path=SETTINGS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: cfg[k] for k in DEFAULTS}, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- helpers
class Shm:
    def __init__(self):
        self.m = mmap.mmap(-1, SHM_SIZE, tagname=SHM_NAME)
        self.seq = 0

    def write(self, valid, closed, openness, hand, tip, upper, normal, cfg):
        self.seq += 1                                   # odd: writing
        self.m[4:8] = struct.pack("<I", self.seq)
        data = LAYOUT.pack(MAGIC, self.seq, GetTickCount64(), int(valid), int(closed), int(openness),
                           *hand, *tip, *upper, *normal,
                           CFG_MAGIC, float(cfg["tip_height"]), int(bool(cfg["toggle_grab"])))
        self.m[0:LAYOUT.size] = data
        self.seq += 1                                   # even: consistent
        self.m[4:8] = struct.pack("<I", self.seq)


class OneEuro:
    """One Euro filter (Casiez et al. 2012) for vectors: strong smoothing while
    the value is (almost) still, little lag while it moves fast.
    min_cutoff (Hz): lower = smoother at rest. beta: how fast smoothing drops
    with speed (units/s). Resets after the hand was lost."""
    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t=None):
        x = np.asarray(x, dtype=np.float64)
        t = time.perf_counter() if t is None else t
        if self.v is None:
            self.v, self.dv, self.t = x, np.zeros_like(x), t
            return self.v
        dt = max(t - self.t, 1e-3)
        self.t = t
        dx = (x - self.v) / dt
        self.dv = self.dv + self._alpha(self.d_cutoff, dt) * (dx - self.dv)
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self.dv))
        self.v = self.v + self._alpha(cutoff, dt) * (x - self.v)
        return self.v

    def reset(self):
        self.v = self.dv = self.t = None


NEUTRAL = np.array([0.0, 0.0, -1.0])   # palm facing the camera = object unrotated


def rotate_towards(ref, cur, v, gain=1.0, dead_deg=0.0):
    """Rotate v by the rotation that takes unit vector ref to cur, scaled by gain.
    Angles below dead_deg are ignored, larger ones start from 0 (no jump)."""
    axis = np.cross(ref, cur)
    s, c = float(np.linalg.norm(axis)), float(np.dot(ref, cur))
    ang = math.atan2(s, c)
    dead = math.radians(dead_deg)
    if s < 1e-6 or ang <= dead:
        return v.copy()
    ang = (ang - dead) * gain
    k = axis / s                                   # Rodrigues
    return v * math.cos(ang) + np.cross(k, v) * math.sin(ang) + k * np.dot(k, v) * (1 - math.cos(ang))


def pick_hand(result, want):
    best = None
    for i, (lms, hd) in enumerate(zip(result.hand_landmarks, result.handedness)):
        label, score = hd[0].category_name, hd[0].score
        if want != "any" and label.lower() != want.lower():
            continue
        if best is None or score > best[2]:
            best = (i, label, score)
    return best


# ---------------------------------------------------------------- tracker
class Tracker:
    """Camera + MediaPipe + filters. step() processes one frame; settings in
    self.cfg can change at any time (call apply() afterwards)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cap, self.cam_index = None, None
        self.lm = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=os.path.join(HERE, "models", "hand_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        self.shm = Shm()
        self.s_depth = OneEuro(0.4, 1.5)    # palm-width distance estimate: the noisiest
        self.s_pos, self.s_tip, self.s_up = OneEuro(1, 6), OneEuro(1, 6), OneEuro(1, 6)   # metres
        self.s_nrm = OneEuro(0.8, 0.4)      # unit vector (rad/s)
        self.s_open = OneEuro(1.5, 0.02)    # 0..100
        self.filters = {self.s_depth: (0.4, 1.5), self.s_pos: (1.0, 6.0), self.s_tip: (1.0, 6.0),
                        self.s_up: (1.0, 6.0), self.s_nrm: (0.8, 0.4), self.s_open: (1.5, 0.02)}
        self.t0, self.last_ms = time.time(), -1
        self.closed = False
        self.normal_ref = None                    # relative rotation reference
        self.pointing, self.point_votes = False, 0
        self.locked_normal, self.locked_pos, self.point_ref, self.point_nref = NEUTRAL.copy(), None, None, None
        self.apply()

    def apply(self):
        c = self.cfg
        if c["camera"] != self.cam_index:
            if self.cap:
                self.cap.release()
            self.cam_index = c["camera"]
            self.cap = cv2.VideoCapture(int(c["camera"]), cv2.CAP_DSHOW)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        k, r = 1.0 / max(float(c["smooth"]), 0.05), float(c["responsive"])
        for flt, (mc, beta) in self.filters.items():
            flt.min_cutoff, flt.beta = mc * k, beta * r

    def close(self):
        if self.cap:
            self.cap.release()

    def step(self):
        """Returns (annotated BGR frame or None, status dict)."""
        c = self.cfg
        ok, frame = self.cap.read() if self.cap and self.cap.isOpened() else (False, None)
        if not ok:
            time.sleep(0.03)
            return None, {"text": "камера %d не открывается" % c["camera"], "hand": False}
        frame = cv2.flip(frame, 1)                       # mirror: your right = screen right
        h, w = frame.shape[:2]
        f = (w / 2) / math.tan(math.radians(c["fov"]) / 2)  # focal length in pixels
        ms = max(int((time.time() - self.t0) * 1000), self.last_ms + 1)
        self.last_ms = ms
        res = self.lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), ms)
        pick = pick_hand(res, c["hand"])

        if pick is None:
            for s in self.filters:
                s.reset()
            self.closed, self.normal_ref = False, None
            self.shm.write(0, 0, 100, (0, 0, 0.45), (0, 0.08, 0.45), (0, 0.05, 0.45), (0, 0, -1), c)
            return frame, {"text": "руки не видно", "hand": False}

        i, label, score = pick
        px = np.array([[p.x * w, p.y * h] for p in res.hand_landmarks[i]])        # pixels
        wl = np.array([[p.x, -p.y, p.z] for p in res.hand_world_landmarks[i]])   # metres: right, up, away

        palm_px = np.linalg.norm(px[INDEX_MCP] - px[PINKY_MCP])
        palm_px = max(palm_px, 0.6 * np.linalg.norm(px[WRIST] - px[MIDDLE_MCP]))  # side-on hands
        depth = float(np.clip(f * c["palm_width"] / max(palm_px, 1.0) * c["depth_scale"], 0.15, 1.2))
        depth = float(self.s_depth([depth])[0])          # smooth before it scales x/y too

        def to3d(pt):  # pixel -> (right, up, depth) metres at the hand's distance
            return np.array([(pt[0] - w / 2) * depth / f * c["move_scale"],
                             -(pt[1] - h / 2) * depth / f * c["move_scale"], depth])

        centre, tip, upper = to3d(px[list(PALM)].mean(axis=0)), to3d(px[INDEX_TIP]), to3d(px[MIDDLE_MCP])
        n = np.cross(wl[INDEX_MCP] - wl[WRIST], wl[PINKY_MCP] - wl[WRIST])
        if label == "Left":
            n = -n
        if c["flip_normal"]:
            n = -n
        normal = n / (np.linalg.norm(n) or 1.0)          # (right, up, depth)

        ratios = [np.linalg.norm(wl[t] - wl[WRIST]) / max(np.linalg.norm(wl[m] - wl[WRIST]), 1e-4)
                  for m, t in FINGERS]
        openness = float(np.clip((np.mean(ratios) - 1.15) / (1.85 - 1.15) * 100, 0, 100))

        centre, tip, upper = self.s_pos(centre), self.s_tip(tip), self.s_up(upper)
        normal = self.s_nrm(normal)
        normal = normal / (np.linalg.norm(normal) or 1.0)
        openness = float(self.s_open([openness])[0])
        if self.closed and openness > c["release"]:
            self.closed = False
        elif not self.closed and openness < c["fist"]:
            self.closed = True
        closed = self.closed

        mode = c["rotate_mode"]
        if mode == "absolute":
            send_pos, send_normal, state = centre, normal, "FIST" if closed else "open"
        elif mode == "relative":
            # the fist pose at the moment of grabbing is "neutral"; only the tilt since then rotates
            if not closed:
                self.normal_ref, send_normal, state = None, NEUTRAL, "open"
            else:
                if self.normal_ref is None:
                    self.normal_ref = normal.copy()
                send_normal = rotate_towards(self.normal_ref, normal, NEUTRAL, c["rotate_gain"], c["rotate_dead"])
                state = "FIST"
            send_pos = centre
        else:                                            # point: extend the index finger to rotate
            point_pose = ratios[0] > 1.6 and max(ratios[1:]) < 1.4
            fdir = wl[INDEX_TIP] - wl[INDEX_MCP]
            fdir = fdir / (np.linalg.norm(fdir) or 1.0)
            if not closed:
                self.pointing, self.point_votes, self.point_ref = False, 0, None
                self.locked_normal = NEUTRAL.copy()
                send_pos, send_normal, state = centre, self.locked_normal, "open"
            else:
                self.point_votes = self.point_votes + 1 if point_pose != self.pointing else 0
                if self.point_votes >= c["point_frames"]:
                    self.pointing, self.point_votes = point_pose, 0
                    if self.pointing:
                        self.point_ref, self.point_nref, self.locked_pos = fdir, self.locked_normal, centre
                    else:
                        self.point_ref = None
                if self.pointing and self.point_ref is not None:
                    self.locked_normal = rotate_towards(self.point_ref, fdir, self.point_nref, c["rotate_gain"])
                    send_pos, send_normal, state = self.locked_pos, self.locked_normal, "ROTATE"
                else:
                    send_pos, send_normal, state = centre, self.locked_normal, "MOVE"

        off = send_pos - centre                          # keep tip/upper with a locked position
        self.shm.write(1, closed, openness, send_pos, tip + off, upper + off, send_normal, c)

        col = {"ROTATE": (255, 128, 0), "MOVE": (0, 0, 255), "FIST": (0, 0, 255)}.get(state, (0, 255, 0))
        for b0, b1 in BONES:
            cv2.line(frame, tuple(px[b0].astype(int)), tuple(px[b1].astype(int)), col, 2)
        tilt = math.degrees(math.acos(float(np.clip(np.dot(send_normal, NEUTRAL), -1, 1))))
        return frame, {"hand": True, "label": label, "score": score, "depth": depth, "openness": openness,
                       "closed": closed, "state": state, "tilt": tilt,
                       "text": "%s %.0f%%  %.2f м  раскрытость %3.0f  %s  поворот %.0f°" % (
                           label, score * 100, depth, openness, state, tilt)}


# -------------------------------------------------------------------- GUI
def run_gui(tracker, cfg):
    import tkinter as tk
    from tkinter import ttk

    PW, PH = 400, 300                                   # preview size
    root = tk.Tk()
    root.title("nocam hand tracker")
    saved = dict(cfg)
    vars_ = {}
    state = {"img": None, "frames": 0, "fps": 0.0, "fps_t": time.time()}

    def dirty():
        root.title("nocam hand tracker" + (" *" if any(cfg[k] != saved[k] for k in DEFAULTS) else ""))

    def on_change(key, kind):
        v = vars_[key].get()
        cfg[key] = int(round(float(v))) if kind == "int" else float(v) if kind == "float" else v
        if cfg["release"] < cfg["fist"] + 5:             # keep a hysteresis gap
            cfg["release"] = min(cfg["fist"] + 5, 95)
            vars_["release"].set(cfg["release"])
        tracker.apply()
        dirty()

    left = ttk.Frame(root, padding=6)
    left.grid(row=0, column=0, sticky="n")
    preview = ttk.Label(left)
    preview.pack()
    status = ttk.Label(left, text="", width=60, wraplength=PW)
    status.pack(anchor="w", pady=(6, 0))
    bar = ttk.Progressbar(left, length=PW, maximum=100)
    bar.pack(anchor="w")
    ttk.Label(left, text="раскрытость руки (риски - пороги кулака и ладони)", foreground="#777").pack(anchor="w")
    marks = tk.Canvas(left, width=PW, height=8, highlightthickness=0)
    marks.pack(anchor="w")

    right = ttk.Frame(root, padding=6)
    right.grid(row=0, column=1, sticky="n")
    tabs = ttk.Notebook(right)
    tabs.pack(fill="both")
    for section, items in SPEC:
        box = ttk.Frame(tabs, padding=(8, 4))
        tabs.add(box, text=section)
        for key, label, kind, lo, hi, step, hint in items:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=1)
            if kind == "bool":
                vars_[key] = tk.BooleanVar(value=cfg[key])
                ttk.Checkbutton(row, text=label, variable=vars_[key],
                                command=lambda k=key, t=kind: on_change(k, t)).pack(anchor="w")
            elif kind == "choice":
                ttk.Label(row, text=label).pack(side="left")
                vars_[key] = tk.StringVar(value=cfg[key])
                cb = ttk.Combobox(row, textvariable=vars_[key], values=lo, state="readonly", width=12)
                cb.pack(side="right")
                cb.bind("<<ComboboxSelected>>", lambda e, k=key, t=kind: on_change(k, t))
            else:
                vars_[key] = tk.DoubleVar(value=cfg[key])
                tk.Scale(row, label=label, variable=vars_[key], from_=lo, to=hi, resolution=step,
                         orient="horizontal", length=300,
                         command=lambda v, k=key, t=kind: on_change(k, t)).pack(fill="x")
            ttk.Label(box, text=hint, foreground="#777", font=("Segoe UI", 8)).pack(anchor="w")

    def set_all(values):
        cfg.update(values)
        for k, var in vars_.items():
            var.set(cfg[k])
        tracker.apply()
        dirty()

    def do_save():
        save_settings(cfg)
        saved.update(cfg)
        dirty()

    buttons = ttk.Frame(right, padding=(0, 6))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Сохранить", command=do_save).pack(side="left")
    ttk.Button(buttons, text="Отменить изменения", command=lambda: set_all(dict(saved))).pack(side="left", padx=4)
    ttk.Button(buttons, text="По умолчанию", command=lambda: set_all(dict(DEFAULTS))).pack(side="left")
    root.bind("<Control-s>", lambda e: do_save())

    def draw_marks():
        marks.delete("all")
        for v, col in ((cfg["fist"], "red"), (cfg["release"], "green")):
            x = int(v / 100 * PW)
            marks.create_line(x, 0, x, 8, fill=col, width=2)

    def tick():
        frame, st = tracker.step()
        if frame is not None:
            small = cv2.resize(frame, (PW, PH))
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            state["img"] = tk.PhotoImage(data=b"P6 %d %d 255\n" % (PW, PH) + rgb.tobytes(), format="PPM")
            preview.configure(image=state["img"])
        state["frames"] += 1
        if time.time() - state["fps_t"] >= 1.0:
            state["fps"] = state["frames"] / (time.time() - state["fps_t"])
            state["frames"], state["fps_t"] = 0, time.time()
        status.configure(text="%4.1f fps   %s" % (state["fps"], st["text"]),
                         foreground="#c00" if st.get("closed") else "")
        bar["value"] = st.get("openness", 0)
        draw_marks()
        root.after(1, tick)

    def on_close():
        if any(cfg[k] != saved[k] for k in DEFAULTS):
            from tkinter import messagebox
            ans = messagebox.askyesnocancel("nocam hand tracker", "Сохранить изменения настроек?")
            if ans is None:
                return
            if ans:
                do_save()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    print("tracking - shared memory '%s' ready. Close the window to stop." % SHM_NAME)
    root.after(1, tick)
    root.mainloop()


def run_console(tracker, preview):
    title = "nocam hand tracker (fist = grab, tilt = rotate)"
    frames, fps_t, fps = 0, time.time(), 0.0
    print("tracking - shared memory '%s' ready. Ctrl+C or Esc to stop." % SHM_NAME)
    while True:
        frame, st = tracker.step()
        frames += 1
        if time.time() - fps_t >= 1.0:
            fps, frames, fps_t = frames / (time.time() - fps_t), 0, time.time()
            if not preview:
                print("%4.1f fps  %s" % (fps, st["text"]))
        if preview and frame is not None:
            cv2.putText(frame, "%4.1f fps" % fps, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            cv2.imshow(title, frame)
            if cv2.waitKey(1) & 0xFF in (27, ord("q")) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
    cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-gui", action="store_true", help="no settings window (plain preview)")
    ap.add_argument("--no-preview", action="store_true", help="console only (implies --no-gui)")
    for section, items in SPEC:
        for key, label, kind, lo, hi, step, hint in items:
            opt = "--" + key.replace("_", "-")
            if kind == "bool":
                ap.add_argument(opt, action="store_true", default=None, help="%s (%s)" % (label, hint))
            else:
                typ = {"int": int, "float": float}.get(kind, str)
                ap.add_argument(opt, type=typ, default=None, choices=lo if kind == "choice" else None,
                                help="%s (%s)" % (label, hint))
    # old flags
    ap.add_argument("--point", action="store_true", help="same as --rotate-mode point")
    ap.add_argument("--absolute", action="store_true", help="same as --rotate-mode absolute")
    a = ap.parse_args()

    cfg = load_settings()
    for key in DEFAULTS:
        v = getattr(a, key, None)
        if v is not None:
            cfg[key] = v
    if a.point:
        cfg["rotate_mode"] = "point"
    if a.absolute:
        cfg["rotate_mode"] = "absolute"

    tracker = Tracker(cfg)
    try:
        if a.no_gui or a.no_preview:
            run_console(tracker, not a.no_preview)
        else:
            run_gui(tracker, cfg)
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
