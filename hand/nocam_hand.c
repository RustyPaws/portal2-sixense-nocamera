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
 *    We put a fake PXCGesture there whose QueryNodeData reports a hand
 *    driven by mouse/keyboard.
 *
 * Controls (only while the game window is focused):
 *   hold ALT            hand is present and CLOSED (grab); mouse moves hand
 *                       (view does not turn), wheel = closer/further
 *   hold ALT + R        mouse tilts the hand (rotate), wheel rolls it
 *                       (key configurable: rotate_key in nocam_hand.ini)
 *   release ALT         hand opens (release); hand returns to centre
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

#define G_PIPELINE        0x10A63E9C   /* CSixenseUtilPipeline* (gesture) */
#define FN_ALLOC          0x1015BAC0   /* void* __cdecl alloc(size)       */
#define FN_PIPELINE_CTOR  0x103FE860   /* __thiscall ctor                 */
#define PIPELINE_SIZE     0x2948
#define OFF_UTIL_VTBL     0x1F80       /* UtilPipeline sub-object         */
#define OFF_GESTURE_PTR   0x1048       /* PXCGesture* returned by QueryGesture */

/* ---------------------------------------------------------------- tunables */
static float cfg_move_scale  = 0.0006f;  /* metres per mouse count          */
static float cfg_depth_step  = 0.03f;    /* metres per wheel notch          */
static float cfg_tilt_scale  = 0.004f;   /* radians per mouse count         */
static float cfg_roll_step   = 0.15f;    /* radians per wheel notch         */
static float cfg_rest_depth  = 0.45f;    /* hand distance from camera (m)   */
static int   cfg_hand_always = 1;        /* report open hand when ALT up    */
static int   cfg_log_nodes   = 0;
static int   cfg_rotate_vk   = 'R';      /* hold with ALT to rotate instead of move */
static int   cfg_alt_open    = 1;        /* 1: every ALT grab is rotate-capable     */
static int   cfg_hold_open   = 2;        /* openness sent while holding+rotatable   */
/* axis signs (+1/-1). Game mirrors world x, so mouse-right needs -x. */
static float sx_move = -1.f, sy_move = 1.f, sx_tilt = 1.f, sy_tilt = 1.f;

/* ---------------------------------------------------------- shared state */
static volatile LONG g_alt, g_shift;
static CRITICAL_SECTION g_cs;
static float h_x, h_up, h_depth, h_tilt_x, h_tilt_up, h_roll;
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

static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

static void hand_reset(void)
{
    h_x = 0; h_up = 0; h_depth = cfg_rest_depth;
    h_tilt_x = 0; h_tilt_up = 0; h_roll = 0;
}

static int game_focused(void)
{
    DWORD pid = 0;
    HWND w = GetForegroundWindow();
    if (!w) return 0;
    GetWindowThreadProcessId(w, &pid);
    return pid == GetCurrentProcessId();
}

/* ------------------------------------------------------- input (LL hook) */
static LRESULT CALLBACK mouse_ll(int code, WPARAM wp, LPARAM lp)
{
    if (code == HC_ACTION && game_focused() && (GetAsyncKeyState(VK_MENU) & 0x8000)) {
        MSLLHOOKSTRUCT *m = (MSLLHOOKSTRUCT *)lp;
        int shift = (GetAsyncKeyState(cfg_rotate_vk) & 0x8000) != 0;   /* rotate modifier */
        if (m->flags & LLMHF_INJECTED)
            return CallNextHookEx(NULL, code, wp, lp);
        if (wp == WM_MOUSEMOVE) {
            POINT c;
            GetCursorPos(&c);
            {
                float dx = (float)(m->pt.x - c.x), dy = (float)(m->pt.y - c.y);
                EnterCriticalSection(&g_cs);
                if (shift) {
                    h_tilt_x  = clampf(h_tilt_x  + sx_tilt * dx * cfg_tilt_scale, -1.2f, 1.2f);
                    h_tilt_up = clampf(h_tilt_up - sy_tilt * dy * cfg_tilt_scale, -1.2f, 1.2f);
                } else {
                    h_x  = clampf(h_x  + sx_move * dx * cfg_move_scale, -0.30f, 0.30f);
                    h_up = clampf(h_up - sy_move * dy * cfg_move_scale, -0.25f, 0.25f);
                }
                LeaveCriticalSection(&g_cs);
            }
            return 1;   /* swallow: the view must not turn while ALT is held */
        }
        if (wp == WM_MOUSEWHEEL) {
            float notches = (float)(short)HIWORD(m->mouseData) / WHEEL_DELTA;
            EnterCriticalSection(&g_cs);
            if (shift) h_roll = clampf(h_roll + notches * cfg_roll_step, -1.5f, 1.5f);
            else       h_depth = clampf(h_depth - notches * cfg_depth_step, 0.20f, 0.80f);
            LeaveCriticalSection(&g_cs);
            return 1;
        }
    }
    return CallNextHookEx(NULL, code, wp, lp);
}

static DWORD WINAPI input_thread(LPVOID p)
{
    MSG msg;
    HHOOK h = SetWindowsHookExA(WH_MOUSE_LL, mouse_ll, GetModuleHandleA(NULL), 0);
    dbg("mouse hook %s", h ? "installed" : "FAILED");
    (void)p;
    while (GetMessageA(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    return 0;
}

/* ------------------------------------------------------ fake PXCGesture */
typedef struct { void **vtbl; } FakeObj;
static void *g_gesture_vtbl[32];
static FakeObj g_gesture = { g_gesture_vtbl };

static void put3(BYTE *p, float a, float b, float c)
{
    ((float *)p)[0] = a; ((float *)p)[1] = b; ((float *)p)[2] = c;
}

static int __stdcall gesture_QueryNodeData(void *self, int user, DWORD label, BYTE *node)
{
    static LONG prev_alt;
    DWORD side = label & 0xFFFF0000u, part = label & 0xFFFFu;
    int alt = (GetAsyncKeyState(VK_MENU) & 0x8000) && game_focused();
    float x, up, depth, tx, tu, roll, px, pu;
    (void)self; (void)user;

    if (!node) return -1;
    if (side != 0x40000u) return -3;            /* only the primary hand   */
    if (!alt && !cfg_hand_always) return -3;    /* no hand in front of cam */

    EnterCriticalSection(&g_cs);
    if (part == 0 && alt != prev_alt) {         /* edge: re-centre hand    */
        hand_reset();
        prev_alt = alt;
    }
    x = h_x; up = h_up; depth = h_depth; tx = h_tilt_x; tu = h_tilt_up; roll = h_roll;
    LeaveCriticalSection(&g_cs);

    px = x; pu = up;
    if (part == 6) {            /* fingertip: 8 cm "above" the palm, rolled */
        px += -sinf(roll) * 0.08f; pu += cosf(roll) * 0.08f;
    } else if (part == 7) {     /* upper hand: 5 cm above, rolled           */
        px += -sinf(roll) * 0.05f; pu += cosf(roll) * 0.05f;
    }

    memset(node, 0, 0x80);
    *(DWORD *)(node + 0x04) = label;
    *(ULONGLONG *)(node + 0x10) = GetTickCount64() * 10000ull;
    put3(node + 0x18, px, depth, pu);                               /* world  */
    put3(node + 0x24, 160.0f - px / depth * 224.0f,                 /* image  */
                      120.0f - pu / depth * 224.0f, depth * 1000.0f);
    {   /* palm normal points back at the camera, tilted by SHIFT+mouse      */
        float nx = sinf(tx), nz = sinf(tu), ny = -cosf(tx) * cosf(tu);
        put3(node + 0x58, nx, ny, nz);
    }
    /* The game grabs while (averaged) openness <= 1 (portal_fire_hand_closed)
     * but only rotates a held object when openness > rot_req_openness (0.5)
     * AND the state is "open". openness 1 + state open satisfies both, so
     * ALT+R keeps holding and enables rotation.                            */
    {
        /* The game decides at grab time whether a hold may rotate, so with
         * alt_open=1 every ALT grab uses the rotate-capable values and the
         * mouse hook alone decides move (ALT) vs rotate (ALT+R).           */
        int rot = alt && (cfg_alt_open || (GetAsyncKeyState(cfg_rotate_vk) & 0x8000));
        /* game scales openness by ~0.9 and truncates: 2 -> 1 (holds: <=1, and
         * rotates: >0.5). 1 would become 0 and never rotate.               */
        *(DWORD *)(node + 0x64) = rot ? (DWORD)cfg_hold_open : alt ? 0 : 100;
        *(DWORD *)(node + 0x68) = (alt && !rot) ? 1 : 2;     /* 1 closed, 2 open */
    }
    *(DWORD *)(node + 0x70) = 100;              /* confidence-ish           */

    if (cfg_log_nodes && part == 0) {
        static DWORD last;   /* at most ~10 lines/s */
        DWORD now = GetTickCount();
        if (now - last >= 100) {
            last = now;
            dbg("node alt=%d R=%d open=%lu state=%lu pos=(%.3f %.3f %.3f) tilt=(%.2f %.2f) roll=%.2f",
                alt, (GetAsyncKeyState(cfg_rotate_vk) & 0x8000) != 0,
                *(DWORD *)(node + 0x64), *(DWORD *)(node + 0x68), x, depth, up, tx, tu, roll);
            {   /* game-side hand state: CSixensePlayerInput @ 0x10A62A78 */
                BYTE *pi = (BYTE *)VA(0x10A62A78);
                __try {
                    dbg("   game: f6b0=%d f75d=%d f2e6=%d open760=%d nrm=(%.2f %.2f %.2f) rot=(%.2f %.2f %.2f) pos680=(%.2f %.2f %.2f)",
                        pi[0x6B0], pi[0x75D], pi[0x2E6], *(int *)(pi + 0x760),
                        *(float *)(pi + 0x68C), *(float *)(pi + 0x690), *(float *)(pi + 0x694),
                        *(float *)(pi + 0x6A4), *(float *)(pi + 0x6A8), *(float *)(pi + 0x6AC),
                        *(float *)(pi + 0x680), *(float *)(pi + 0x684), *(float *)(pi + 0x688));
                } __except (EXCEPTION_EXECUTE_HANDLER) { dbg("   game: read failed"); }
            }
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
    cfg_move_scale  = ini_f(ini, "move_scale",  cfg_move_scale);
    cfg_depth_step  = ini_f(ini, "depth_step",  cfg_depth_step);
    cfg_tilt_scale  = ini_f(ini, "tilt_scale",  cfg_tilt_scale);
    cfg_roll_step   = ini_f(ini, "roll_step",   cfg_roll_step);
    cfg_rest_depth  = ini_f(ini, "rest_depth",  cfg_rest_depth);
    cfg_hand_always = GetPrivateProfileIntA("hand", "hand_always", cfg_hand_always, ini);
    cfg_log_nodes   = GetPrivateProfileIntA("hand", "log_nodes",   cfg_log_nodes,   ini);
    {   /* rotate_key: a letter/digit (e.g. R) or a virtual-key number (16 = Shift) */
        char k[16];
        GetPrivateProfileStringA("hand", "rotate_key", "R", k, sizeof k, ini);
        if (k[0] && !k[1] && ((k[0] >= 'A' && k[0] <= 'Z') || (k[0] >= '0' && k[0] <= '9'))) cfg_rotate_vk = k[0];
        else if (k[0] >= 'a' && k[0] <= 'z' && !k[1]) cfg_rotate_vk = k[0] - 32;
        else if (atoi(k) > 0) cfg_rotate_vk = atoi(k);
    }
    cfg_alt_open = GetPrivateProfileIntA("hand", "alt_open", cfg_alt_open, ini);
    cfg_hold_open = GetPrivateProfileIntA("hand", "hold_openness", cfg_hold_open, ini);
    if (GetPrivateProfileIntA("hand", "invert_x", 0, ini))      sx_move = -sx_move;
    if (GetPrivateProfileIntA("hand", "invert_y", 0, ini))      sy_move = -sy_move;
    if (GetPrivateProfileIntA("hand", "invert_tilt_x", 0, ini)) sx_tilt = -sx_tilt;
    if (GetPrivateProfileIntA("hand", "invert_tilt_y", 0, ini)) sy_tilt = -sy_tilt;
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
        g_client = (BYTE *)GetModuleHandleA("client_sixense.dll");
        dbg("sixenseInit: client_sixense.dll at %p", g_client);
        if (g_client) {
            __try { install_fake_pipeline(); }
            __except (EXCEPTION_EXECUTE_HANDLER) { dbg("install_fake_pipeline crashed: %08lx", GetExceptionCode()); }
        }
        CreateThread(NULL, 0, input_thread, NULL, 0, NULL);
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
        hand_reset();
        sprintf(path, "%s\\sixense_real.dll", g_dir);
        g_real = LoadLibraryA(path);
        dbg("nocam_hand loaded; real sixense: %p", g_real);
    }
    return TRUE;
}
