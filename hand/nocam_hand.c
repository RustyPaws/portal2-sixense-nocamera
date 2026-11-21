/*
 * nocam_hand - fake Senz3D hand for Portal 2 Sixense Perceptual Pack.
 *
 * Built as a proxy "sixense.dll": every export forwards to sixense_real.dll
 * (see sixense.def) except sixenseInit, which first installs the fake hand.
 *
 * How it works (addresses are client_sixense.dll VAs at image base 0x10000000):
 *  - We construct the game's own Intel gesture pipeline object
 *    (alloc 0x2948 via 0x1015BAC0, ctor 0x103FE860) and store it in the
 *    global 0x10A63E9C, where the game expects it.
 *  - Its UtilPipeline vtable (object+0x1F80) is copied and patched so the
 *    pipeline reports "connected", Init/AcquireFrame succeed and
 *    ReleaseFrame/Close do nothing. The game then calls its own frame
 *    handler (vt+0x30 = 0x103FEC10) every frame.
 *  - That handler gets PXCGesture* from object+0x1048 and calls
 *    QueryNodeData(user, label, GeoNode*) (vtable slot 7, stdcall).
 *    We put a fake PXCGesture there whose QueryNodeData reports the hand
 *    seen by the webcam tracker (tracker/tracker.py, shared memory
 *    "nocam_hand"). Without the tracker no hand is visible.
 *
 * GeoNode fields used by the game (reverse engineered):
 *   +0x04 label, +0x10 timestamp, +0x18/1C/20 world pos (x, depth, up; m),
 *   +0x24/28/2C image pos (px), +0x58/5C/60 palm normal,
 *   +0x64 openness (0..100), +0x68 openness state (1 closed, 2 open)
 */
#include <windows.h>
#include <stdio.h>
#include <math.h>
#include <string.h>
#include "forwards.h"

#define CLIENT_BASE 0x10000000u
static BYTE *g_client;
#define VA(x) ((void *)(g_client + ((DWORD)(x) - CLIENT_BASE)))

/* ------------------------------------------------ in-memory camera patches */
/* client_sixense.dll stays the original file on disk; these are applied to  */
/* the loaded image from DllMain, before any of its code runs. `reloc` is the */
/* offset of an absolute address inside `orig` that the loader has already  */
/* rebased (-1 if none). New bytes contain no absolute addresses.           */
typedef struct {
    const char *name; DWORD va; int len, reloc;
    BYTE orig[8], patch[8];
} MemPatch;

static const MemPatch g_mem_patches[] = {
    { "A: skip Intel pipeline creation at startup", 0x10402B21, 6, -1,
      { 0x0F, 0x84, 0x16, 0x02, 0x00, 0x00 }, { 0xE9, 0x17, 0x02, 0x00, 0x00, 0x90 } },
    { "B: no periodic camera reconnect attempt",   0x103FFD07, 6, 2,
      { 0x8B, 0x0D, 0x84, 0xAA, 0x9E, 0x10 }, { 0xE9, 0xEC, 0x00, 0x00, 0x00, 0x90 } },
    { "C: create-pipeline-if-missing does nothing", 0x103FFA52, 6, -1,
      { 0x0F, 0x85, 0xD2, 0x00, 0x00, 0x00 }, { 0xE9, 0xD3, 0x00, 0x00, 0x00, 0x90 } },
    { "D: camera-present check always true",       0x103EC2C0, 6, 1,
      { 0xA1, 0x9C, 0x3E, 0xA6, 0x10, 0x85 }, { 0xB8, 0x01, 0x00, 0x00, 0x00, 0xC3 } },
    { "F: never recreate pipeline on frame-rate change", 0x103FEFFF, 6, -1,
      { 0x0F, 0x84, 0x2B, 0x01, 0x00, 0x00 }, { 0xE9, 0x2C, 0x01, 0x00, 0x00, 0x90 } },
};

static void apply_mem_patches(void);   /* defined after dbg() */

#define G_PIPELINE        0x10A63E9C   /* CSixenseUtilPipeline* (gesture) */
#define FN_ALLOC          0x1015BAC0   /* void* __cdecl alloc(size)       */
#define FN_PIPELINE_CTOR  0x103FE860   /* __thiscall ctor                 */
#define PIPELINE_SIZE     0x2948
#define OFF_UTIL_VTBL     0x1F80       /* UtilPipeline sub-object         */
#define OFF_GESTURE_PTR   0x1048       /* PXCGesture* returned by QueryGesture */
/* Depth image size (UtilPipeline +0xF48 sub-object, +0x9C0/+0x9C4). The frame
 * handler (0x103FED68) clears "fingertip inside the image" (0x10A63B10) when
 * it is 0, and without that flag a closed hand never grabs (0x103FBB61).    */
#define OFF_IMAGE_W       0x2940
#define OFF_IMAGE_H       0x2944
#define IMAGE_W           320          /* matches the image coords we send */
#define IMAGE_H           240

/* ---------------------------------------------------------------- tunables */
static int   cfg_log_nodes   = 0;
static int   cfg_hold_open   = 2;        /* openness sent while holding+rotatable   */
static float cfg_tip_height  = 0.045f;   /* fingertip this far above the palm (m): it
                                            aims the portal gun; 0.08 points it upwards */

/* ---------------------------------------------------------- shared state */
static CRITICAL_SECTION g_cs;
static FILE *g_log;
static char g_dir[MAX_PATH];

static void dbg(const char *fmt, ...)
{
    va_list ap;
    if (!g_log) return;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    fflush(g_log);
}

static void apply_mem_patches(void)
{
    static int done;
    enum { n = sizeof g_mem_patches / sizeof g_mem_patches[0] };
    DWORD delta = (DWORD)g_client - CLIENT_BASE;
    BYTE want[n][8];
    int i, bad = 0;

    if (done || !g_client) return;
    done = 1;
    /* verify every site first: all or nothing */
    for (i = 0; i < n; i++) {
        const MemPatch *p = &g_mem_patches[i];
        const BYTE *cur = (const BYTE *)VA(p->va);
        memcpy(want[i], p->orig, p->len);
        if (p->reloc >= 0) *(DWORD *)(want[i] + p->reloc) += delta;
        if (memcmp(cur, p->patch, p->len) == 0) continue;          /* patched on disk */
        if (memcmp(cur, want[i], p->len) != 0) {
            dbg("mem patch %s: unexpected bytes %02x %02x %02x %02x %02x %02x", p->name,
                cur[0], cur[1], cur[2], cur[3], cur[4], cur[5]);
            bad = 1;
        }
    }
    if (bad) { dbg("different client_sixense.dll version - camera patches NOT applied"); return; }
    for (i = 0; i < n; i++) {
        const MemPatch *p = &g_mem_patches[i];
        BYTE *at = (BYTE *)VA(p->va);
        DWORD old;
        if (memcmp(at, p->patch, p->len) == 0) { dbg("mem patch %s: already in file", p->name); continue; }
        if (!VirtualProtect(at, p->len, PAGE_EXECUTE_READWRITE, &old)) {
            dbg("mem patch %s: VirtualProtect failed (%lu)", p->name, GetLastError());
            continue;
        }
        memcpy(at, p->patch, p->len);
        VirtualProtect(at, p->len, old, &old);
        dbg("mem patch %s: applied at %p", p->name, at);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
}

/* ------------------------------------------------------ fake PXCGesture */
typedef struct { void **vtbl; } FakeObj;
static void *g_gesture_vtbl[32];
static FakeObj g_gesture = { g_gesture_vtbl };

/* ------------------------------------------- webcam tracker (tracker.py) */
/* Shared memory written by tracker/tracker.py - keep layouts in sync.     */
#pragma pack(push, 1)
typedef struct {
    DWORD magic;               /* 'NCH1'                                    */
    volatile DWORD seq;        /* odd while the tracker is writing          */
    ULONGLONG tick_ms;         /* GetTickCount64 at write                   */
    int valid, closed, openness;
    float hand[3], tip[3], upper[3], normal[3];   /* (right, up, depth)     */
    DWORD cfg_magic;           /* 'NCC1': live settings below are valid      */
    float tip_height;          /* overrides nocam_hand.ini while tracking    */
    int toggle_grab;
} CamShm;
#pragma pack(pop)
#define CAM_MAGIC 0x3148434Eu
#define CAM_CFG_MAGIC 0x3143434Eu
static int cfg_camera_timeout = 300;          /* ms before data is stale  */
static const CamShm *g_cam;

static void put3(BYTE *p, float a, float b, float c);
static int player_holds(void);
#include "nocam_test.inc.c"

static int cam_read(CamShm *out)
{
    static ULONGLONG next_try;
    int i;
    if (t_on && t_source == 1) return test_cam(out);   /* scripted tracker */
    if (!g_cam) {
        HANDLE m;
        ULONGLONG now = GetTickCount64();
        if (now < next_try) return 0;
        next_try = now + 1000;                    /* tracker not running yet */
        m = OpenFileMappingA(FILE_MAP_READ, FALSE, "nocam_hand");
        if (!m) return 0;
        g_cam = (const CamShm *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, sizeof(CamShm));
        if (!g_cam) return 0;
        dbg("webcam tracker connected");
    }
    for (i = 0; i < 4; i++) {                     /* seqlock read            */
        DWORD s = g_cam->seq;
        if (s & 1) continue;
        MemoryBarrier();
        *out = *g_cam;
        MemoryBarrier();
        if (g_cam->seq == s) break;
    }
    if (i == 4 || out->magic != CAM_MAGIC) return 0;
    if (GetTickCount64() - out->tick_ms > (ULONGLONG)cfg_camera_timeout) return 0;   /* stale */
    return out->valid ? 1 : -1;                   /* -1: tracker running, no hand */
}

static void put3(BYTE *p, float a, float b, float c)
{
    ((float *)p)[0] = a; ((float *)p)[1] = b; ((float *)p)[2] = c;
}

/* The game only takes a new hand frame when the position changed (a real
 * camera always jitters), so a still hand would never open or close. Every
 * frame alternates the position by +-0.3 mm.                               */
static int g_flip;
#define JITTER (g_flip ? 0.0003f : -0.0003f)

/* Grab logic of the game (reverse engineered, 0x103FBAD1 / 0x1040098E):
 *  - an OPEN hand (state 2, averaged openness above a threshold) arms it;
 *  - a CLOSED hand (state 1) for > hand_close_time then presses USE once,
 *    which in Portal toggles: picks up, or drops what is held;
 *  - a held object rotates only with state 2 and openness > 0.5 (we send 2,
 *    which the game scales to 1).
 * So the original is "close = grab, open = keep holding/rotate, close again =
 * drop" (toggle_grab=1). Default: hold-to-carry - while closed we report 2/2
 * once something is held, and on opening we re-arm and pulse "closed" until
 * the object is dropped. Nothing is ever grabbed by opening the hand.       */
static int cfg_toggle_grab = 0;
typedef void *(__cdecl *getplayer_t)(int slot);
typedef void *(__cdecl *getheld_t)(void *player);

static int player_holds(void)            /* main thread only */
{
    __try {
        void *pl = ((getplayer_t)VA(0x1008BC60))(-1);
        return pl && ((getheld_t)VA(0x103B7320))(pl) != NULL;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
}

static void grab_values(int closed, DWORD *open, DWORD *state)
{
    int held, armed;
    if (cfg_toggle_grab) {
        if (closed) { *open = 0; *state = 1; } else { *open = 100; *state = 2; }
        return;
    }
    held = player_holds();
    armed = *(volatile BYTE *)VA(0x10A63E96);
    if (closed && held)              { *open = (DWORD)cfg_hold_open; *state = 2; }  /* carry */
    else if (closed)                 { *open = 0;   *state = 1; }  /* grab / carry  */
    else if (held && armed)          { *open = 0;   *state = 1; }  /* drop pulse    */
    else                             { *open = 100; *state = 2; }  /* open / re-arm */
}

static int __stdcall gesture_QueryNodeData(void *self, int user, DWORD label, BYTE *node)
{
    DWORD side = label & 0xFFFF0000u, part = label & 0xFFFFu;
    CamShm c;
    float p[3], d, ux, uy, ul;
    DWORD open, state;
    (void)self; (void)user;

    if (!node) return -1;
    if (side != 0x40000u) return -3;            /* only the primary hand   */
    if (part == 0) g_flip = !g_flip;
    {
        int r;
        if (test_query(label, node, &r)) return r; /* NOCAM_TEST script hand */
    }
    if (cam_read(&c) != 1) return -3;           /* no tracker / no hand    */
    if (c.cfg_magic == CAM_CFG_MAGIC) {         /* live settings from the tracker window */
        cfg_tip_height = c.tip_height;
        cfg_toggle_grab = c.toggle_grab;
    }

    /* Fingertip/upper: a fixed length from the palm towards the knuckles
       (keeps roll). The real index fingertip points down in a fist and far
       up with an open hand, which tips the portal gun.                    */
    ux = c.upper[0] - c.hand[0]; uy = c.upper[1] - c.hand[1];
    ul = sqrtf(ux * ux + uy * uy);
    if (ul < 1e-4f) { ux = 0.f; uy = 1.f; } else { ux /= ul; uy /= ul; }
    p[0] = c.hand[0] + JITTER; p[1] = c.hand[1]; p[2] = c.hand[2];
    if (part == 6)      { p[0] += ux * cfg_tip_height;          p[1] += uy * cfg_tip_height; }
    else if (part == 7) { p[0] += ux * cfg_tip_height * 0.625f; p[1] += uy * cfg_tip_height * 0.625f; }
    d = p[2] > 0.05f ? p[2] : 0.05f;

    /* node space = (-right, depth, up); the game mirrors world x */
    memset(node, 0, 0x80);
    *(DWORD *)(node + 0x04) = label;
    *(ULONGLONG *)(node + 0x10) = c.tick_ms * 10000ull;
    put3(node + 0x18, -p[0], p[2], p[1]);
    put3(node + 0x24, 160.0f + p[0] / d * 224.0f, 120.0f - p[1] / d * 224.0f, p[2] * 1000.0f);
    put3(node + 0x58, -c.normal[0], c.normal[2], c.normal[1]);
    grab_values(c.closed, &open, &state);       /* fist = grab/carry, open = drop */
    *(DWORD *)(node + 0x64) = open;
    *(DWORD *)(node + 0x68) = state;            /* 1 closed, 2 open */
    *(DWORD *)(node + 0x70) = 100;

    if (cfg_log_nodes && part == 0) {
        static DWORD last;                      /* at most ~10 lines/s */
        if (GetTickCount() - last >= 100) {
            BYTE *pi = (BYTE *)VA(0x10A62A78);  /* CSixensePlayerInput */
            last = GetTickCount();
            dbg("cam closed=%d open=%d sent=%lu/%lu hand=(%.3f %.3f %.3f) n=(%.2f %.2f %.2f)",
                c.closed, c.openness, open, state, c.hand[0], c.hand[1], c.hand[2],
                c.normal[0], c.normal[1], c.normal[2]);
            __try {
                dbg("   game: f6b0=%d f75d=%d open760=%d armed=%d rot=(%.2f %.2f %.2f) pos680=(%.2f %.2f %.2f)",
                    pi[0x6B0], pi[0x75D], *(int *)(pi + 0x760), *(BYTE *)VA(0x10A63E96),
                    *(float *)(pi + 0x6A4), *(float *)(pi + 0x6A8), *(float *)(pi + 0x6AC),
                    *(float *)(pi + 0x680), *(float *)(pi + 0x684), *(float *)(pi + 0x688));
            } __except (EXCEPTION_EXECUTE_HANDLER) { dbg("   game: read failed"); }
        }
    }
    return 0;
}

static void *__stdcall gesture_DynamicCast(void *self, int cuid) { (void)cuid; return self; }
static void  __stdcall gesture_Release(void *self) { (void)self; }

/* Unknown slots: cdecl so a wrong argument-count guess cannot unbalance the
 * caller's stack more than its frame tolerates; we log which slot was used. */
#define STUB(n) static int __cdecl gesture_stub##n(void) { dbg("PXCGesture slot %d called (unhandled)", n); return -1; }
STUB(2) STUB(3) STUB(4) STUB(5) STUB(6) STUB(8) STUB(9) STUB(10) STUB(11) STUB(12) STUB(13) STUB(14) STUB(15)

/* --------------------------------------------- UtilPipeline overrides */
static void *g_util_vtbl[64];
static void *g_main_vtbl[64];
/* scalar deleting destructor: do nothing (object is abandoned at exit; the
 * real dtor would tear down our fake members and crash) */
static void *__fastcall pipe_dtor(void *ecx, void *edx, int flags) { (void)edx; (void)flags; dbg("pipeline destructor skipped"); return ecx; }
static int  __fastcall pipe_false(void *ecx, void *edx)            { (void)ecx; (void)edx; return 0; }
static int  __fastcall pipe_true(void *ecx, void *edx)             { (void)ecx; (void)edx; return 1; }
static int  __fastcall pipe_acquire(void *ecx, void *edx, int wait) { (void)ecx; (void)edx; (void)wait; return 1; }
static void __fastcall pipe_nop(void *ecx, void *edx)              { (void)ecx; (void)edx; }

static int install_fake_pipeline(void)
{
    typedef void *(__cdecl *alloc_t)(size_t);
    typedef void *(__fastcall *ctor_t)(void *self, void *edx);
    BYTE *obj;
    void ***slot = (void ***)VA(G_PIPELINE);
    int i;

    if (*slot) { dbg("pipeline already present (%p), leaving it", *slot); return 1; }

    obj = (BYTE *)((alloc_t)VA(FN_ALLOC))(PIPELINE_SIZE);
    if (!obj) { dbg("alloc failed"); return 0; }
    ((ctor_t)VA(FN_PIPELINE_CTOR))(obj, NULL);
    dbg("pipeline constructed at %p", obj);

    memcpy(g_util_vtbl, *(void **)(obj + OFF_UTIL_VTBL), sizeof g_util_vtbl);
    g_util_vtbl[0x54 / 4] = (void *)pipe_false;    /* IsDisconnected -> no  */
    g_util_vtbl[0x60 / 4] = (void *)pipe_true;     /* Init                  */
    g_util_vtbl[0x64 / 4] = (void *)pipe_acquire;  /* AcquireFrame(wait)    */
    g_util_vtbl[0x68 / 4] = (void *)pipe_nop;      /* ReleaseFrame          */
    g_util_vtbl[0x6C / 4] = (void *)pipe_nop;      /* Close                 */
    *(void **)(obj + OFF_UTIL_VTBL) = g_util_vtbl;

    memcpy(g_main_vtbl, *(void **)obj, sizeof g_main_vtbl);
    g_main_vtbl[0] = (void *)pipe_dtor;
    *(void **)obj = g_main_vtbl;

    for (i = 0; i < 32; i++) g_gesture_vtbl[i] = (void *)gesture_stub15;
    g_gesture_vtbl[0] = (void *)gesture_DynamicCast;
    g_gesture_vtbl[1] = (void *)gesture_Release;
    g_gesture_vtbl[2] = (void *)gesture_stub2;  g_gesture_vtbl[3] = (void *)gesture_stub3;
    g_gesture_vtbl[4] = (void *)gesture_stub4;  g_gesture_vtbl[5] = (void *)gesture_stub5;
    g_gesture_vtbl[6] = (void *)gesture_stub6;  g_gesture_vtbl[7] = (void *)gesture_QueryNodeData;
    g_gesture_vtbl[8] = (void *)gesture_stub8;  g_gesture_vtbl[9] = (void *)gesture_stub9;
    g_gesture_vtbl[10] = (void *)gesture_stub10; g_gesture_vtbl[11] = (void *)gesture_stub11;
    g_gesture_vtbl[12] = (void *)gesture_stub12; g_gesture_vtbl[13] = (void *)gesture_stub13;
    g_gesture_vtbl[14] = (void *)gesture_stub14;
    *(void **)(obj + OFF_GESTURE_PTR) = &g_gesture;
    *(DWORD *)(obj + OFF_IMAGE_W) = IMAGE_W;
    *(DWORD *)(obj + OFF_IMAGE_H) = IMAGE_H;

    *slot = (void **)obj;
    dbg("fake gesture pipeline installed");
    return 1;
}

/* ------------------------------------------------------------- config */
static float ini_f(const char *ini, const char *k, float def)
{
    char buf[64], d[64];
    sprintf(d, "%g", def);
    GetPrivateProfileStringA("hand", k, d, buf, sizeof buf, ini);
    return (float)atof(buf);
}

static void load_config(void)
{
    char ini[MAX_PATH];
    sprintf(ini, "%s\\nocam_hand.ini", g_dir);
    cfg_log_nodes   = GetPrivateProfileIntA("hand", "log_nodes",   cfg_log_nodes,   ini);
    cfg_hold_open = GetPrivateProfileIntA("hand", "hold_openness", cfg_hold_open, ini);
    cfg_camera_timeout = GetPrivateProfileIntA("hand", "camera_timeout_ms", cfg_camera_timeout, ini);
    cfg_toggle_grab = GetPrivateProfileIntA("hand", "toggle_grab", cfg_toggle_grab, ini);
    cfg_tip_height  = ini_f(ini, "tip_height",  cfg_tip_height);
}

/* --------------------------------------------------------- export glue */
static HMODULE g_real;
typedef int (*sixenseInit_t)(void);

__declspec(dllexport) int sixenseInit(void)
{
    static int done;
    sixenseInit_t real = (sixenseInit_t)GetProcAddress(g_real, "sixenseInit");
    if (!done) {
        done = 1;
        if (!g_client) g_client = (BYTE *)GetModuleHandleA("client_sixense.dll");
        dbg("sixenseInit: client_sixense.dll at %p", g_client);
        apply_mem_patches();                       /* no-op if DllMain did it */
        if (g_client) {
            __try { install_fake_pipeline(); }
            __except (EXCEPTION_EXECUTE_HANDLER) { dbg("install_fake_pipeline crashed: %08lx", GetExceptionCode()); }
        }
    }
    return real ? real() : -1;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID r)
{
    (void)r;
    if (reason == DLL_PROCESS_ATTACH) {
        char path[MAX_PATH], *s;
        DisableThreadLibraryCalls(inst);
        InitializeCriticalSection(&g_cs);
        GetModuleFileNameA(inst, g_dir, MAX_PATH);
        if ((s = strrchr(g_dir, '\\')) != NULL) *s = 0;
        sprintf(path, "%s\\nocam_hand.log", g_dir);
        g_log = fopen(path, "w");
        load_config();
        test_init();
        sprintf(path, "%s\\sixense_real.dll", g_dir);
        g_real = LoadLibraryA(path);
        dbg("nocam_hand loaded; real sixense: %p", g_real);
        /* We are a static import of client_sixense.dll: it is already mapped
           and rebased, but none of its code has run yet. */
        g_client = (BYTE *)GetModuleHandleA("client_sixense.dll");
        dbg("DllMain: client_sixense.dll at %p", g_client);
        apply_mem_patches();
    }
    return TRUE;
}
