# Portal 2 Sixense Perceptual Pack — play without the Senz3D camera

**По-русски, просто и по шагам: [README.ru.md](README.ru.md)**

The Perceptual Pack expects a Creative Senz3D depth camera and the Intel
Perceptual Computing runtime. Without them the game crashes at startup or nags
"Connect Senz3D camera" and freezes the player. This project:

1. **Patches `client_sixense.dll` in memory** (the file on disk stays original)
   so the game never creates the Intel camera pipelines and never asks for the
   camera. The proxy `sixense.dll` does this from its `DllMain`, before any of the
   client code runs.
2. **Fixes a crash on CPUs with many threads** (≥ 32 logical CPUs) in
   `vstdlib.dll`; this is not camera related.
3. **Fakes the hand** the camera would track: a proxy `sixense.dll` feeds the
   game's own camera code the hand an ordinary webcam sees (`tracker/`, MediaPipe).
   Grabbing, moving, rotating and scaling objects and tweaking portals then work
   as designed.

No game files are included. The patcher edits your installed copy and keeps
backups.

## Controls

| Action                         | Input                                   |
| ------------------------------ | --------------------------------------- |
| Move / look / jump             | WASD / mouse / Space                    |
| Fire portals                   | Left / right click                      |
| Normal "use" pickup            | E                                       |
| **Grab with the hand**         | make a **fist** in front of the webcam (`tracker\run.bat`) |
| Move the held object           | move the fist; closer to the camera = further away |
| **Rotate held object**         | tilt the fist (relative to how it was when you grabbed) |
| Drop                           | open the hand                           |
| Scaling mode / portal tweak    | V / G (the game's own binds), then the hand as above |

The portal gun follows the hand. Without the tracker running no hand is visible.

## Install

Requirements: Python 3 (`pip install pefile capstone` only for `tools/`),
Visual Studio 2019/2022 (any edition) with the C++ workload to build the proxy
DLL - or take the ready zip from GitHub Releases and skip `handuild.bat`.

CI (`.github/workflows/build.yml`) builds the DLL on every push and pull request
(the zip is attached to the run) and publishes a release for every `v*` tag:
`git tag v1.0 && git push origin v1.0`.

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
| `portal2_sixense/bin/client_sixense.dll`     | none on disk; A–D, F applied in memory by the proxy | — (an old patched file is restored from `.orig`) |
| `bin/vstdlib.dll`                            | patch E                                  | `.orig` backup |
| `portal2_sixense/bin/sixense.dll`            | replaced by the proxy                    | original kept as `sixense_real.dll` |
| `portal2_sixense/bin/nocam_hand.ini`         | fake-hand settings (never overwritten)   | — |
| `portal2_sixense/cfg/autoexec.cfg`           | `sixense_intel_enabled 1`                | removed on revert |

## Tuning — `portal2_sixense/bin/nocam_hand.ini`

Read at game start.

| Key | Default | Meaning |
| --- | --- | --- |
| `hold_openness` | 2 | openness reported while carrying (game: scaled to 1 = holds and can rotate) |
| `log_nodes` | 0 | log hand + game state to `nocam_hand.log` (~10 lines/s) |
| `tip_height` | 0.045 | fingertip height above the palm (m). It aims the portal gun; 0.08 points it at the ceiling |
| `toggle_grab` | 0 | 0 = hold to carry. 1 = the original toggle: close = grab, open = keep holding and rotate, close again = drop |
| `camera_timeout_ms` | 300 | webcam tracker data older than this is ignored |

## Patches

All VAs are at the DLL's preferred base `0x10000000`. A–D, F live in
`g_mem_patches` in `hand/nocam_hand.c`: the proxy rebases each address to where
Windows actually loaded the client, checks the original bytes (allowing for
already-rebased absolute addresses) and writes all five or none. E is written to
`vstdlib.dll` on disk; there the patched instruction's base relocation is
disabled, otherwise Windows "fixes up" the new bytes when the DLL is rebased.

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
* The hand comes from the shared memory block `nocam_hand` that
  `tracker/tracker.py` writes (layout in both files). No tracker = no hand.

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

## How grabbing works in the game

Reverse engineered in `client_sixense.dll`; the proxy depends on all of it:

* The frame handler (`0x103FEC10`, `this` = pipeline + `0x1F80`) needs the depth
  image size at pipeline + `0x2940/0x2944`. If it is 0, the "fingertip inside the
  image" flag `0x10A63B10` stays 0 and **nothing can ever be grabbed**. The proxy
  sets 320×240, which is also what its image coordinates assume.
* The game only takes a new hand frame when the position changed. A real camera
  always jitters; the proxy alternates the position by ±0.3 mm each frame.
* `0x103FBAD1`: an open hand (state 2, averaged openness above a threshold) arms
  the grab (`0x10A63E96`). A closed hand (state 1) for longer than
  `sixense_intel_hand_close_time` sets `CSixensePlayerInput+0x75D`, and
  `0x1040098E` then presses USE (`IN_USE`, `0x20`) once. In Portal USE toggles:
  it picks up or drops.
* A held object rotates 1:1 with the palm normal only with state 2 and openness
  above `sixense_intel_1to1_rot_req_openness`.
* The portal gun aims along palm → fingertip (`sixense_intel_use_fingertip 1`).
  The palm position moves the gun model 1:1.

Hold to carry (default): a closed hand reports state 1 until the player holds
something (`GetHeldObject` `0x103B7320` of `GetLocalPlayer` `0x1008BC60`), then
2/2 so tilting rotates. On opening, the proxy re-arms and pulses "closed" until
the object is dropped. Opening the hand never grabs anything.

## Testing (`tools/hand_test.py`)

Starts `portal2.exe` directly (the Steam client must be running), lets the proxy
play a hand script and prints the results. A run takes about 20 s:

```bat
python tools\hand_test.py tests\grab_cam.txt     :: webcam path: grab, lift, tilt, drop
```

* The script language is described in `hand/nocam_test.inc.c`: hand position,
  normal, openness, `source cam` (feed the real webcam code path), `fist 0|1`,
  console commands, `expect held 0|1`.
* VScript helpers (`tests/nocam_test.nut`): place/look at an entity, PASS/FAIL
  checks for movement and rotation.
* `cmd jpeg` takes screenshots; the runner combines them into
  `portal2_sixense/bin/nocam_test_sheet.jpg`.
* Output: `nocam_test.log` (hand + game state, PASS/FAIL) and console lines
  containing `TEST`.

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
