"""Tiny Win32 debugger: launches portal2.exe, reports access violations (32-bit WOW64 target)."""
import ctypes, ctypes.wintypes as W, sys, struct, os

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
G = r"C:\Program Files (x86)\Steam\steamapps\common\Portal 2 Sixense Perceptual Pack"

DEBUG_ONLY_THIS_PROCESS = 2
EXCEPTION_DEBUG_EVENT, CREATE_THREAD, CREATE_PROCESS, EXIT_THREAD, EXIT_PROCESS, LOAD_DLL = 1, 2, 3, 4, 5, 6
DBG_CONTINUE, DBG_NOT_HANDLED = 0x00010002, 0x80010001


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("r", W.LPWSTR), ("d", W.LPWSTR), ("t", W.LPWSTR)] + \
               [(n, W.DWORD) for n in "x y xs ys xc yc fa fl".split()] + \
               [("sw", W.WORD), ("cb2", W.WORD), ("r2", ctypes.c_void_p), ("i", W.HANDLE), ("o", W.HANDLE), ("e", W.HANDLE)]


class PINFO(ctypes.Structure):
    _fields_ = [("hp", W.HANDLE), ("ht", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class EXREC(ctypes.Structure):
    pass


EXREC._fields_ = [("code", W.DWORD), ("flags", W.DWORD), ("rec", ctypes.c_void_p), ("addr", ctypes.c_void_p),
                  ("n", W.DWORD), ("info", ctypes.c_size_t * 15)]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("code", W.DWORD), ("pid", W.DWORD), ("tid", W.DWORD), ("pad", W.DWORD), ("u", ctypes.c_byte * 160)]


class WOW64_CONTEXT(ctypes.Structure):
    _fields_ = [("ContextFlags", W.DWORD), ("dr", W.DWORD * 6), ("fl", ctypes.c_byte * 112),
                ("seg", W.DWORD * 4), ("Edi", W.DWORD), ("Esi", W.DWORD), ("Ebx", W.DWORD), ("Edx", W.DWORD),
                ("Ecx", W.DWORD), ("Eax", W.DWORD), ("Ebp", W.DWORD), ("Eip", W.DWORD), ("SegCs", W.DWORD),
                ("EFlags", W.DWORD), ("Esp", W.DWORD), ("SegSs", W.DWORD), ("ext", ctypes.c_byte * 512)]


k32.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
k32.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
k32.OpenThread.restype = W.HANDLE
k32.Wow64GetThreadContext.argtypes = [W.HANDLE, ctypes.c_void_p]

si = STARTUPINFO(); si.cb = ctypes.sizeof(si); pi = PINFO()
cmd = '"%s\\portal2.exe" -game portal2_sixense -condebug -novid -windowed -w 1280 -h 720 ' % G + " ".join(sys.argv[1:])
if not k32.CreateProcessW(None, cmd, None, None, False, DEBUG_ONLY_THIS_PROCESS, None, G, ctypes.byref(si), ctypes.byref(pi)):
    sys.exit("CreateProcess failed %d" % ctypes.get_last_error())

if os.environ.get("AFFINITY"):
    k32.SetProcessAffinityMask.argtypes=[W.HANDLE, ctypes.c_size_t]
    print("affinity set:", k32.SetProcessAffinityMask(pi.hp, int(os.environ["AFFINITY"],16)))
k32.DebugSetProcessKillOnExit(False)
mods = {}
threads = {pi.tid: pi.ht}
tstart = {}


def name_of(h):
    buf = ctypes.create_unicode_buffer(520)
    if h and k32.GetFinalPathNameByHandleW(h, buf, 520, 0):
        return buf.value.split("\\")[-1]
    return "?"


def sym(a):
    for b, (n, end) in mods.items():
        if b <= a < end:
            return "%s+%x" % (n, a - b)
    return hex(a)


def readmem(addr, n):
    b = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
    k32.ReadProcessMemory(pi.hp, addr, b, n, ctypes.byref(got))
    return b.raw[:got.value]


def modsize(base):
    hdr = readmem(base, 0x400)
    if len(hdr) < 0x100: return base + 0x1000
    pe = struct.unpack_from("<I", hdr, 0x3C)[0]
    return base + struct.unpack_from("<I", hdr, pe + 0x50)[0]


ev = DEBUG_EVENT(); av_seen = 0
while True:
    if not k32.WaitForDebugEvent(ctypes.byref(ev), 60000):
        k32.DebugSetProcessKillOnExit(False); k32.DebugActiveProcessStop(pi.pid); print("no crash for 60s idle - detached, game left running"); break
    cont = DBG_CONTINUE
    u = bytes(ev.u)
    if ev.code == CREATE_PROCESS:
        hfile, hproc, hthr, base = struct.unpack_from("<QQQQ", u, 0)
        threads[ev.tid] = hthr
    elif ev.code == CREATE_THREAD:
        threads[ev.tid] = struct.unpack_from("<Q", u, 0)[0]; tstart[ev.tid] = struct.unpack_from("<Q", u, 16)[0]
    elif ev.code == EXIT_THREAD:
        tstart.pop(ev.tid, None)
    elif ev.code == LOAD_DLL:
        hfile, base = struct.unpack_from("<QQ", u, 0)
        n = name_of(hfile); mods[base] = (n, modsize(base))
        if hfile: k32.CloseHandle(W.HANDLE(hfile))
    elif ev.code == EXCEPTION_DEBUG_EVENT:
        rec = EXREC.from_buffer_copy(u[:ctypes.sizeof(EXREC)])
        first = struct.unpack_from("<I", u, ctypes.sizeof(EXREC))[0]
        if rec.code in (0x80000003, 0x4000001F, 0x406D1388):   # breakpoints / thread naming
            cont = DBG_CONTINUE if rec.code != 0x406D1388 else DBG_NOT_HANDLED
        else:
            cont = DBG_NOT_HANDLED
            if rec.code == 0xC0000005 or not first:
                av_seen += 1
                if av_seen <= 6 or not first:
                    ctx = WOW64_CONTEXT(); ctx.ContextFlags = 0x10007
                    k32.Wow64GetThreadContext(threads.get(ev.tid), ctypes.byref(ctx))
                    print("%s-chance %08x at %s  info=%s" % ("first" if first else "SECOND", rec.code, sym(rec.addr or 0),
                          [hex(rec.info[i]) for i in range(min(rec.n, 2))]))
                    print("  eax=%x ebx=%x ecx=%x edx=%x esi=%x edi=%x ebp=%x esp=%x" % (
                        ctx.Eax, ctx.Ebx, ctx.Ecx, ctx.Edx, ctx.Esi, ctx.Edi, ctx.Ebp, ctx.Esp))
                    import collections
                    print("  live threads:", len(tstart)+1, collections.Counter(sym(a).split("+")[0] if "+" in sym(a) else "wow64:"+sym(a)[-5:] for a in tstart.values()).most_common(8))
                    fr = readmem(ctx.Ebp - 8, 8)
                    if len(fr)==8: print("  [ebp-8] threadidx=%d  [ebp-4] cache=%#x" % struct.unpack("<iI", fr))
                    st = readmem(ctx.Esp, 0x3000)
                    hits = []
                    for i in range(0, len(st) - 3, 4):
                        v = struct.unpack_from("<I", st, i)[0]; s = sym(v)
                        if "+" in s and "ntdll" not in s.lower() and "kernel" not in s.lower(): hits.append(s)
                    print("  stack:", hits[:40])
    elif ev.code == EXIT_PROCESS:
        print("process exit code %#x" % struct.unpack_from("<I", u, 0)[0]); break
    k32.ContinueDebugEvent(ev.pid, ev.tid, cont)
