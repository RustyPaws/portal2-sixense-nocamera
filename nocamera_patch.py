"""
Portal 2 Sixense Perceptual Pack - play without the Creative Senz3D camera.

Usage
-----
    hand\build.bat                      build the fake-hand proxy sixense.dll
    python nocamera_patch.py            apply  (creates <dll>.orig backups)
    python nocamera_patch.py --revert   restore the originals
    python nocamera_patch.py --status   show current state
    ... --game "<game folder>"          (or env P2SX_GAME; default: Steam path)

What it patches
---------------
portal2_sixense/bin/client_sixense.dll  (camera requirement)
  The file itself is left original: the proxy sixense.dll applies A-D, F to
  the loaded image (in memory) before the game code runs.
  Every Intel pipeline's vt+0x40 returns its PXC session, which is NULL when
  the Intel Perceptual runtime/camera is absent; creating a pipeline then
  dereferences NULL (0x10402B87, camera-frame-rate branch). So pipelines are
  never created; all other users of the pipeline globals
  (0x10A63E9C / EA0 / EA4) null-check them.
    A  0x10402B21  skip Intel pipeline creation at startup
    B  0x103FFD07  no periodic camera-reconnect attempt
    C  0x103FFA52  "create pipeline if missing" does nothing
    D  0x103EC2C0  camera-present check always true -> no "Connect Senz3D
                   camera" dialog / forced pause
    F  0x103FEFFF  frame-rate change never recreates the pipeline

bin/vstdlib.dll  (crash on CPUs with many threads - not camera related)
  CThreadPool::Start sizes an "all cores" pool as m_nLogicalProcessors. On a
  32-thread CPU that pushes live tier0 threads past datacache's per-thread
  lock table and the server crashes in datacache.dll+0x20D3 during map load.
    E  0x1000B3B8  that pool uses 4 threads instead of one per logical CPU

Patched instructions that held absolute addresses also have their base
relocations disabled, otherwise Windows "fixes up" our new bytes when the DLL
is loaded at a different base.
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Portal 2 Sixense Perceptual Pack"


def _game_dir():
    """--game <dir> on the command line, else $P2SX_GAME, else the Steam default."""
    if "--game" in sys.argv:
        i = sys.argv.index("--game")
        path = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        return path
    return os.environ.get("P2SX_GAME", DEFAULT_GAME)


GAME = os.path.normpath(_game_dir())

# Camera patches A-D, F. They are NOT written to disk any more: the proxy
# sixense.dll (hand/nocam_hand.c, g_mem_patches) applies the same bytes to the
# loaded image. Kept here to recognise and undo files patched by older versions
# and for --status.
CLIENT = {
    "client_sixense.dll": {
        "path": os.path.join(GAME, "portal2_sixense", "bin", "client_sixense.dll"),
        "sha256": "a0b7e7e1360579af2f7b8028fad6fbbd2703a405eb0c39b881fe321f05c01026",
        "patches": [
            ("A: skip Intel pipeline creation at startup", 0x10402B21,
             "0F 84 16 02 00 00",    # je  10402D3D
             "E9 17 02 00 00 90"),   # jmp 10402D3D
            ("B: no periodic camera reconnect attempt", 0x103FFD07,
             "8B 0D 84 AA 9E 10",    # mov ecx,[109EAA84]
             "E9 EC 00 00 00 90"),   # jmp 103FFDF8
            ("C: create-pipeline-if-missing does nothing", 0x103FFA52,
             "0F 85 D2 00 00 00",    # jne 103FFB2A
             "E9 D3 00 00 00 90"),   # jmp 103FFB2A
            ("D: camera-present check always true", 0x103EC2C0,
             "A1 9C 3E A6 10 85",    # mov eax,[10A63E9C] ; test..
             "B8 01 00 00 00 C3"),   # mov eax,1 ; ret
            ("F: never recreate pipeline on frame-rate change", 0x103FEFFF,
             "0F 84 2B 01 00 00",    # je  103FF130 (only if no pipeline)
             "E9 2C 01 00 00 90"),   # jmp 103FF130 (always)
        ],
    }
}

TARGETS = {
    "vstdlib.dll": {
        "path": os.path.join(GAME, "bin", "vstdlib.dll"),
        "sha256": "0ec1dd55d8a2ec3a86c5be61e836ab99a01e27191b030522d9c226e0c5cd32cf",
        "patches": [
            ("E: cap all-cores thread pool at 4 threads", 0x1000B3B8,
             "0F B6 40 05",          # movzx eax, byte [eax+5]  (logical CPUs)
             "6A 04 58 90"),         # push 4 ; pop eax ; nop
        ],
    },
}

IMAGE_BASE = 0x10000000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _u32(d, o):
    return int.from_bytes(d[o:o + 4], "little")


def _u16(d, o):
    return int.from_bytes(d[o:o + 2], "little")


def va_to_offset(data, va):
    pe = _u32(data, 0x3C)
    nsec = _u16(data, pe + 6)
    sec = pe + 24 + _u16(data, pe + 20)
    rva = va - IMAGE_BASE
    for i in range(nsec):
        s = sec + i * 40
        vsize, vaddr, rawsz, rawp = _u32(data, s + 8), _u32(data, s + 12), _u32(data, s + 16), _u32(data, s + 20)
        if vaddr <= rva < vaddr + max(vsize, rawsz):
            return rva - vaddr + rawp
    raise ValueError("VA %#x not in any section" % va)


def reloc_entries(data):
    """Yield (file offset of entry, target rva, type) for every base relocation."""
    opt = _u32(data, 0x3C) + 24
    rva, size = _u32(data, opt + 96 + 5 * 8), _u32(data, opt + 96 + 5 * 8 + 4)
    if not rva:
        return
    off = va_to_offset(data, IMAGE_BASE + rva)
    end = off + size
    while off < end:
        page, blk = _u32(data, off), _u32(data, off + 4)
        if blk < 8:
            break
        for e in range(off + 8, off + blk, 2):
            w = _u16(data, e)
            yield e, page + (w & 0xFFF), w >> 12
        off += blk


def neutralize_relocs(data, va, length):
    lo, hi = va - IMAGE_BASE, va - IMAGE_BASE + length
    n = 0
    for e, target, typ in list(reloc_entries(data)):
        if typ == 3 and target < hi and target + 4 > lo:   # HIGHLOW -> ABSOLUTE (no-op)
            data[e:e + 2] = b"\0\0"
            n += 1
    return n


def site_state(data, va, orig, new):
    off = va_to_offset(data, va)
    cur = bytes(data[off:off + len(orig)])
    return "original" if cur == orig else "patched" if cur == new else "UNKNOWN " + cur.hex()


def parse(t):
    return [(n, va, bytes.fromhex(o), bytes.fromhex(p)) for n, va, o, p in t["patches"]]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--apply"
    if mode not in ("--apply", "--revert", "--status"):
        sys.exit(__doc__)

    for dll, t in TARGETS.items():
        path, backup = t["path"], t["path"] + ".orig"
        if not os.path.isfile(path):
            sys.exit("%s not found at %s" % (dll, path))

        if mode == "--revert":
            if os.path.isfile(backup):
                shutil.copy2(backup, path)
                print("%s: restored original from backup" % dll)
            else:
                print("%s: no backup, left as is" % dll)
            continue

        data = bytearray(open(path, "rb").read())
        patches = parse(t)
        states = [site_state(data, va, o, p) for _, va, o, p in patches]

        if mode == "--status":
            for (name, va, _, _), st in zip(patches, states):
                print("%-18s %-46s %#x  %s" % (dll, name, va, st))
            continue

        if all(s == "patched" for s in states):
            print("%s: already patched" % dll)
            continue
        if any(s.startswith("UNKNOWN") for s in states):
            sys.exit("%s: unexpected bytes (different game version?)\n%s" % (dll, states))
        if any(s == "patched" for s in states):
            sys.exit("%s: partially patched - run --revert first" % dll)

        if sha256(path) != t["sha256"]:
            print("WARNING: %s hash differs from the known original; patch sites still match." % dll)
        if not os.path.isfile(backup):
            shutil.copy2(path, backup)
            print("%s: backup written to %s" % (dll, backup))

        for name, va, orig, new in patches:
            off = va_to_offset(data, va)
            data[off:off + len(new)] = new
            nrel = neutralize_relocs(data, va, len(new))
            print("%-18s %-46s VA %#x  (relocs disabled: %d)" % (dll, name, va, nrel))
        with open(path, "wb") as f:
            f.write(data)
    client_original(mode)
    hand_proxy(mode)
    autoexec(mode)
    if mode == "--apply":
        print("Done.")


def client_original(mode):
    """client_sixense.dll must be the untouched original; the proxy patches it in memory."""
    t = CLIENT["client_sixense.dll"]
    path, backup = t["path"], t["path"] + ".orig"
    if not os.path.isfile(path):
        sys.exit("client_sixense.dll not found at %s" % path)
    data = bytearray(open(path, "rb").read())
    states = [site_state(data, va, o, p) for _, va, o, p in parse(t)]
    if mode == "--status":
        for (name, va, _, _), st in zip(parse(t), states):
            where = "file original, patched in memory by proxy" if st == "original" else "file " + st
            print("%-18s %-46s %#x  %s" % ("client_sixense.dll", name, va, where))
        return
    if all(st == "original" for st in states):
        if mode == "--apply":
            print("client_sixense.dll: original file (camera patches are applied in memory)")
        return
    if os.path.isfile(backup):        # left over from the old on-disk patching
        shutil.copy2(backup, path)
        os.remove(backup)
        print("client_sixense.dll: on-disk patches removed, original restored")
    else:
        print("client_sixense.dll: patched file but no .orig backup - use Steam 'verify files'")


# Fake-hand proxy: our sixense.dll (built from hand/) replaces the mod's
# sixense.dll, which is kept as sixense_real.dll and forwarded to.
MOD_BIN = os.path.join(GAME, "portal2_sixense", "bin")
PROXY_SRC = os.path.join(HERE, "hand", "out", "sixense.dll")
SIXENSE = os.path.join(MOD_BIN, "sixense.dll")
SIXENSE_REAL = os.path.join(MOD_BIN, "sixense_real.dll")


AUTOEXEC_SRC = os.path.join(HERE, "game", "autoexec.cfg")
AUTOEXEC_DST = os.path.join(GAME, "portal2_sixense", "cfg", "autoexec.cfg")
MARK = "[nocamera]"


def autoexec(mode):
    """Install our autoexec.cfg; never clobber a user's own autoexec."""
    ours = os.path.isfile(AUTOEXEC_DST) and MARK in open(AUTOEXEC_DST, errors="ignore").read()
    if mode == "--status":
        print("%-18s %s" % ("autoexec.cfg", "installed" if ours else "not installed"))
    elif mode == "--revert":
        if ours:
            os.remove(AUTOEXEC_DST)
            print("autoexec.cfg: removed")
    elif mode == "--apply" and os.path.isfile(AUTOEXEC_SRC):
        if os.path.isfile(AUTOEXEC_DST) and not ours:
            print("autoexec.cfg: you have your own - add the lines from game/autoexec.cfg by hand")
        else:
            shutil.copy2(AUTOEXEC_SRC, AUTOEXEC_DST)
            print("autoexec.cfg: installed")


def hand_proxy(mode):
    installed = os.path.isfile(SIXENSE_REAL)
    if mode == "--status":
        print("%-18s fake-hand proxy %s" % ("sixense.dll", "installed" if installed else "not installed"))
    elif mode == "--revert":
        if installed:
            shutil.copy2(SIXENSE_REAL, SIXENSE)
            os.remove(SIXENSE_REAL)
            print("sixense.dll: original restored")
    elif mode == "--apply":
        if not os.path.isfile(PROXY_SRC):
            print("sixense.dll: proxy not built (run hand\\build.bat) - skipped")
            return
        if not installed:
            shutil.copy2(SIXENSE, SIXENSE_REAL)
        shutil.copy2(PROXY_SRC, SIXENSE)
        ini_src = os.path.join(HERE, "hand", "nocam_hand.ini")
        ini_dst = os.path.join(MOD_BIN, "nocam_hand.ini")
        if os.path.isfile(ini_src) and not os.path.isfile(ini_dst):
            shutil.copy2(ini_src, ini_dst)     # never overwrite the user's tuning
        print("sixense.dll: fake-hand proxy installed (original kept as sixense_real.dll)")


if __name__ == "__main__":
    main()
