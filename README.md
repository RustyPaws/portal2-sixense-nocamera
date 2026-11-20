# Portal 2 Sixense Perceptual Pack — play without the Senz3D camera

The Perceptual Pack expects a Creative Senz3D depth camera and the Intel
Perceptual Computing runtime. Without them the game crashes at startup or nags
"Connect Senz3D camera" and freezes the player. This project:

1. **Byte-patches** `client_sixense.dll` so the game never creates the Intel
   camera pipelines and never asks for the camera.
2. **Fixes a crash on CPUs with many threads** (≥ 32 logical CPUs) in
   `vstdlib.dll`; this is not camera related.
3. **Fakes the hand** the camera would track: a proxy `sixense.dll` feeds the
   game's own camera code a virtual hand driven by keyboard and mouse. Grabbing,
   moving, rotating and scaling objects and tweaking portals then work as designed.

No game files are included. The patcher edits your installed copy and keeps
backups.

## Controls

| Action                         | Input                                   |
| ------------------------------ | --------------------------------------- |
| Move / look / jump             | WASD / mouse / Space                    |
| Fire portals                   | Left / right click                      |
| Normal "use" pickup            | E                                       |
| **Grab with the hand**         | hold **Alt** (mouse moves the hand, view stays still) |
| Hand closer / further          | Alt + mouse wheel                       |
| **Rotate held object**         | hold **Alt + R** + mouse                |
| Roll                           | Alt + R + mouse wheel                   |
| Scaling mode / portal tweak    | V / G (the game's own binds), then Alt as above |

Release Alt to open the hand (drop) and return to normal mouse-look.

## Install

Requirements: Python 3 (`pip install pefile capstone` only for `tools/`),
Visual Studio 2022 with the C++ workload (to build the proxy DLL).

```bat
hand\build.bat                       :: builds hand\out\sixense.dll (32-bit)
python nocamera_patch.py             :: apply everything (game must be closed)
python nocamera_patch.py --status    :: show what is applied
python nocamera_patch.py --revert    :: restore all originals
```

The game folder defaults to the Steam location. Override it with
`--game "D:\...\Portal 2 Sixense Perceptual Pack"` or the `P2SX_GAME`
environment variable.

What gets installed:

| File (in the game folder)                    | Change                                   | Undo |
| -------------------------------------------- | ---------------------------------------- | ---- |
| `portal2_sixense/bin/client_sixense.dll`     | patches A–D, F                           | `.orig` backup |
| `bin/vstdlib.dll`                            | patch E                                  | `.orig` backup |
| `portal2_sixense/bin/sixense.dll`            | replaced by the proxy                    | original kept as `sixense_real.dll` |
| `portal2_sixense/bin/nocam_hand.ini`         | fake-hand settings (never overwritten)   | — |
| `portal2_sixense/cfg/autoexec.cfg`           | `sixense_intel_enabled 1`, unbinds Alt and R | removed on revert |

## Tuning — `portal2_sixense/bin/nocam_hand.ini`

Read at game start.

| Key | Default | Meaning |
| --- | --- | --- |
| `invert_x`, `invert_y` | 0 | flip hand movement axes |
| `invert_tilt_x`, `invert_tilt_y` | 0 | flip rotation axes |
| `move_scale` | 0.0006 | metres of hand movement per mouse count |
| `depth_step` | 0.03 | metres per wheel notch |
| `tilt_scale`, `roll_step` | 0.004, 0.15 | radians per mouse count / wheel notch while rotating |
| `rest_depth` | 0.45 | resting hand distance from the virtual camera |
| `rotate_key` | R | rotate modifier: a letter, or a virtual-key number (16 = Shift) |
| `alt_open` | 1 | every Alt grab can rotate (0 = old behaviour) |
| `hold_openness` | 2 | openness reported while holding (see notes) |
| `hand_always` | 1 | show an open hand while Alt is up |
| `log_nodes` | 0 | log hand + game state to `nocam_hand.log` (~10 lines/s) |

## Patches

All VAs are at the DLL's preferred base `0x10000000`. Patched instructions that
contained absolute addresses also get their base relocation disabled. Otherwise
Windows "fixes up" the new bytes when the DLL is rebased, which caused a real
crash during development.

| Id | DLL | VA | Change | Why |
| -- | --- | -- | ------ | --- |
| A | client_sixense | `0x10402B21` | `je` → `jmp` | skip Intel pipeline creation at startup. Each pipeline's `vt+0x40` returns a NULL PXC session without the runtime, which is dereferenced at `0x10402B87` (frame-rate branch, default 60) |
| B | client_sixense | `0x103FFD07` | → `jmp 0x103FFDF8` | no periodic "reconnect camera" re-creation |
| C | client_sixense | `0x103FFA52` | `jne` → `jmp` | "create pipeline if missing" does nothing |
| D | client_sixense | `0x103EC2C0` | `mov eax,1; ret` | camera-present check is always true: no "Connect Camera" dialog or forced pause |
| F | client_sixense | `0x103FEFFF` | `je` → `jmp` | `sixense_intel_camera_frame_rate` change callback never tears down / recreates the pipeline |
| E | vstdlib | `0x1000B3B8` | `movzx eax,[eax+5]` → `push 4; pop eax` | `CThreadPool::Start` sized an "all cores" pool as the logical CPU count. On 32 threads, live tier0 threads exceed datacache's 32-slot per-thread table, so the server crashes in `datacache.dll+0x20D3` during map load |

## How the fake hand works (`hand/nocam_hand.c`)

The proxy `sixense.dll` forwards all 40 other exports to `sixense_real.dll`
(`forwards.h`) and hooks `sixenseInit`, which the client calls right before its
Sixense/Intel initialisation:

* It constructs the game's own gesture pipeline (alloc `0x1015BAC0`, size
  `0x2948`, ctor `0x103FE860`) and stores it in the global `0x10A63E9C`.
* It copies and patches the pipeline vtables:
  * UtilPipeline sub-object at `+0x1F80`: `vt+0x54` IsDisconnected → false,
    `+0x60` Init → true, `+0x64` AcquireFrame → true, `+0x68` ReleaseFrame and
    `+0x6C` Close → no-op.
  * Main vtable slot 0, the destructor → no-op, which avoids a crash at exit.
* It writes a fake `PXCGesture*` to `pipeline+0x1048` (what `QueryGesture`
  returns). The game's frame handler `0x103FEC10` calls `QueryNodeData`
  (vtable slot 7, stdcall `(this, user, label, GeoNode*)`) for primary/secondary
  hand (`0x40000`/`0x80000`), fingertip (`|6`) and upper hand (`|7`).
* A low-level mouse hook (`WH_MOUSE_LL`) swallows mouse input while Alt is held
  and turns it into hand position, depth, tilt and roll.

GeoNode fields the game reads (0x80 bytes):

| Offset | Field |
| --- | --- |
| `+0x04` | label |
| `+0x10` | timestamp |
| `+0x18/1C/20` | world position: x, depth, up (metres; the game uses `(-x, up, depth)`) |
| `+0x24/28` | image position (px) |
| `+0x58/5C/60` | palm normal (drives rotation) |
| `+0x64` | openness 0–100 |
| `+0x68` | openness state (1 closed, 2 open) |

Openness notes: the game averages and scales openness (≈ ×0.9, truncated) into
`CSixensePlayerInput(0x10A62A78)+0x760`. It **holds** while that is ≤ 1
(`sixense_intel_portal_fire_hand_closed`). It **rotates** only while it is
> `sixense_intel_1to1_rot_req_openness` (0.5) and the state is "open". So the
hand reports openness 2 (→ 1) with state "open" while held.

Other addresses that are useful when extending this:

* `sixense_intel_enabled` ConVar `0x10A67348`: int value at parent (`0x10A67364`) `+0x30`.
* Hydra path `0x103EE9B0`.
* Per-frame camera nag `0x103FFC18`.
* PXC loader `0x10448F80`.
* Engine client `0x109EAA84`: `vt+0x1B0` executes a client command.

## tools/

These are the ad-hoc Python scripts used for the reverse engineering. They
contain hard-coded paths, so adjust them before use.

| Script | Purpose |
| --- | --- |
| `p2dis.py` + `annot.py` | disassemble with import, string and cvar-name annotations |
| `cvarmap.py` | map ConVar objects to names |
| `xref.py`, `calls.py`, `guard.py`, `impref.py`, `strs.py` | cross references, callers, null-check audit, import uses, strings |
| `minidbg.py` | tiny Win32 debugger for the 32-bit game: crash address, registers, stack, live-thread count, affinity |
| `md.py`, `stk.py` | read Source engine `.mdmp` crash dumps |
| `bytes.py`, `rd.py`, `imports.py`, `ents.py` | bytes at a VA, raw data, imports/exports, BSP entity lump |

## Troubleshooting

* **Frozen controls**: in the console, `sixense_intel_enabled 0` switches to plain
  keyboard/mouse, without the hand.
* **Crash on map load with many CPUs**: patch E is not applied, or a Steam update
  restored `bin/vstdlib.dll`. Re-run the patcher.
* **Steam "verify files"** restores the originals. Run the patcher again afterwards.
