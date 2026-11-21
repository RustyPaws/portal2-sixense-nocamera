"""
Tiny test runner for the fake hand: starts portal2.exe directly (not through a
Steam launch; the Steam client just has to be running), lets the proxy
sixense.dll play a hand script (see hand/nocam_test.inc.c) and prints the
results.

    python tools/hand_test.py tests/grab.txt [--map sp_a1_sx_perc_scaling]
                              [--timeout 120] [--no-install] [--grep TEST]

Output: nocam_test.log (hand + game state) and new console.log lines that
contain --grep (default "TEST", print them from VScript: printl("TEST ...")).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = os.environ.get("P2SX_GAME",
                      r"C:\Program Files (x86)\Steam\steamapps\common\Portal 2 Sixense Perceptual Pack")
MOD = os.path.join(GAME, "portal2_sixense")
BIN = os.path.join(MOD, "bin")
CONSOLE = os.path.join(MOD, "console.log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--map", default="sp_a1_sx_perc_scaling")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--no-install", action="store_true", help="do not copy hand/out/sixense.dll")
    ap.add_argument("--grep", default="TEST")
    ap.add_argument("--args", default="", help="extra game command line")
    a = ap.parse_args()

    if subprocess.run(["tasklist", "/FI", "IMAGENAME eq portal2.exe"], capture_output=True,
                      text=True).stdout.count("portal2.exe"):
        sys.exit("portal2.exe is already running - close it first")
    if not a.no_install:
        if not os.path.isfile(os.path.join(BIN, "sixense_real.dll")):
            sys.exit("proxy not installed - run nocamera_patch.py once")
        shutil.copy2(os.path.join(HERE, "hand", "out", "sixense.dll"), os.path.join(BIN, "sixense.dll"))

    vs = os.path.join(MOD, "scripts", "vscripts")
    os.makedirs(vs, exist_ok=True)
    shutil.copy2(os.path.join(HERE, "tests", "nocam_test.nut"), os.path.join(vs, "nocam_test.nut"))

    shots = os.path.join(MOD, "screenshots")
    old_shots = set(os.listdir(shots)) if os.path.isdir(shots) else set()

    con_start = os.path.getsize(CONSOLE) if os.path.isfile(CONSOLE) else 0
    env = dict(os.environ, NOCAM_TEST=os.path.abspath(a.script))
    cmd = [os.path.join(GAME, "portal2.exe"), "-game", "portal2_sixense", "-novid", "-windowed",
           "-w", "1024", "-h", "640", "-condebug", "+sv_cheats", "1", "+map", a.map] + a.args.split()
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=GAME, env=env)
    try:
        p.wait(a.timeout)
        status = "exit %d after %.0fs" % (p.returncode, time.time() - t0)
    except subprocess.TimeoutExpired:
        p.kill()
        status = "TIMEOUT after %.0fs - killed" % a.timeout

    print("==== game:", status)
    print("==== nocam_test.log")
    try:
        print(open(os.path.join(BIN, "nocam_test.log"), errors="replace").read())
    except OSError as e:
        print("  (none: %s)" % e)
    new = sorted(set(os.listdir(shots)) - old_shots) if os.path.isdir(shots) else []
    print("==== screenshots (cmd jpeg):")
    for n in new:
        print("  " + os.path.join(shots, n))
    if new:   # one contact sheet, numbered in order
        sheet = os.path.join(BIN, "nocam_test_sheet.jpg")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(HERE, "tools", "sheet.ps1"), sheet,
                        "|".join(os.path.join(shots, n) for n in new)])
        print("  sheet: " + sheet)
    print("==== console.log lines with %r" % a.grep)
    if os.path.isfile(CONSOLE):
        with open(CONSOLE, "rb") as f:
            f.seek(con_start if os.path.getsize(CONSOLE) >= con_start else 0)
            for line in f.read().decode("utf-8", "replace").splitlines():
                if a.grep in line:
                    print(line)


if __name__ == "__main__":
    main()
