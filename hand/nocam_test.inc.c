/*
 * Scripted test hand (included by nocam_hand.c).
 *
 * Enabled when the environment variable NOCAM_TEST names a script file
 * (tools/hand_test.py sets it and starts portal2.exe directly). The script
 * starts on the first hand query, i.e. once a map is running, and replaces the
 * webcam hand. One command per line, '#' starts a comment:
 *
 *   wait <ms>                       sleep
 *   hand <right> <up> <depth>       palm position, metres (tracker convention)
 *   normal <right> <up> <depth>     palm normal (towards camera = 0 0 -1)
 *   open <openness 0..100> [state]  state: 1 closed, 2 open (default 2)
 *   tip <right> <up> <depth>        fingertip offset from the palm (default 0 0.08 0)
 *   upper <right> <up> <depth>      upper-hand offset from the palm (default 0 0.05 0)
 *   source script|cam               script: this file sets the raw hand values
 *                                   (default). cam: feed the real webcam path
 *                                   (as tracker.py would).
 *   fist 0|1                        cam: closed fist
 *   jitter <metres>                 alternate the position by +-this each frame
 *   nohand | showhand               hand not visible / visible again
 *   cmd <console command>           run in the game's main thread
 *   log <ms>                        game-state line every <ms> (0 = off)
 *   mark <text>                     write a marker line
 *   expect held 0|1                 PASS/FAIL: is the player holding an object
 *   quit                            "quit" the game
 *
 * Output goes to nocam_test.log next to sixense.dll.
 */

static int   t_on;                       /* test mode active                */
static char  t_script[MAX_PATH];
static FILE *t_log;
static ULONGLONG t_start;
static volatile LONG t_started;
static struct { int valid; float pos[3], nrm[3]; int open, state; } t_hand =
    { 1, { 0.f, 0.f, 0.45f }, { 0.f, 0.f, -1.f }, 100, 2 };
static volatile LONG t_log_ms;
static float t_jitter;
static int   t_source, t_fist;
static float t_tip[3] = { 0.f, 0.08f, 0.f }, t_upper[3] = { 0.f, 0.05f, 0.f };
static int   t_flip;
static char  t_cmds[32][512];
static int   t_cmd_head, t_cmd_tail;

static void tlog(const char *fmt, ...)
{
    va_list ap;
    if (!t_log) return;
    fprintf(t_log, "%7.3f ", (GetTickCount64() - t_start) / 1000.0);
    va_start(ap, fmt);
    vfprintf(t_log, fmt, ap);
    va_end(ap);
    fputc('\n', t_log);
    fflush(t_log);
}

static void test_init(void)
{
    char path[MAX_PATH];
    DWORD n = GetEnvironmentVariableA("NOCAM_TEST", t_script, sizeof t_script);
    if (!n || n >= sizeof t_script) return;
    t_on = 1;
    t_start = GetTickCount64();
    sprintf(path, "%s\\nocam_test.log", g_dir);
    t_log = fopen(path, "w");
    tlog("test mode, script %s", t_script);
}

static void test_queue_cmd(const char *s)
{
    EnterCriticalSection(&g_cs);
    if ((t_cmd_head + 1) % 32 != t_cmd_tail) {
        strncpy(t_cmds[t_cmd_head], s, 511);
        t_cmds[t_cmd_head][511] = 0;
        t_cmd_head = (t_cmd_head + 1) % 32;
    }
    LeaveCriticalSection(&g_cs);
}

/* engine client (0x109EAA84), vt+0x1B0: __thiscall ClientCmd(const char *) */
typedef void (__fastcall *clientcmd_t)(void *self, void *edx, const char *cmd);

static void test_run_cmds(void)          /* main thread */
{
    char cmd[512];
    for (;;) {
        EnterCriticalSection(&g_cs);
        if (t_cmd_tail == t_cmd_head) { LeaveCriticalSection(&g_cs); return; }
        strcpy(cmd, t_cmds[t_cmd_tail]);
        t_cmd_tail = (t_cmd_tail + 1) % 32;
        LeaveCriticalSection(&g_cs);
        if (!strncmp(cmd, "!held ", 6)) {           /* expect held N */
            int want = atoi(cmd + 6), got = player_holds();
            tlog("%s expect held %d: got %d", want == got ? "PASS" : "FAIL", want, got);
            continue;
        }
        __try {
            void *eng = *(void **)VA(0x109EAA84);
            ((clientcmd_t)(*(void ***)eng)[0x1B0 / 4])(eng, NULL, cmd);
            tlog("cmd %s", cmd);
        } __except (EXCEPTION_EXECUTE_HANDLER) { tlog("cmd FAILED %s", cmd); }
    }
}

static DWORD WINAPI test_thread(LPVOID p)
{
    FILE *f = fopen(t_script, "r");
    char line[600];
    (void)p;
    if (!f) { tlog("cannot open script"); return 0; }
    while (fgets(line, sizeof line, f)) {
        char *s = line, *e;
        float a, b, c;
        int i, j;
        while (*s == ' ' || *s == '\t') s++;
        for (e = s + strlen(s); e > s && (e[-1] == '\n' || e[-1] == '\r' || e[-1] == ' '); ) *--e = 0;
        if (!*s || *s == '#') continue;
        if (sscanf(s, "wait %d", &i) == 1) { Sleep(i); continue; }
        EnterCriticalSection(&g_cs);
        if (sscanf(s, "hand %f %f %f", &a, &b, &c) == 3) {
            t_hand.pos[0] = a; t_hand.pos[1] = b; t_hand.pos[2] = c;
        } else if (sscanf(s, "normal %f %f %f", &a, &b, &c) == 3) {
            t_hand.nrm[0] = a; t_hand.nrm[1] = b; t_hand.nrm[2] = c;
        } else if (sscanf(s, "open %d", &i) == 1) {
            j = 2;
            sscanf(s, "open %d %d", &i, &j);
            t_hand.open = i; t_hand.state = j;
        } else if (sscanf(s, "tip %f %f %f", &a, &b, &c) == 3) {
            t_tip[0] = a; t_tip[1] = b; t_tip[2] = c;
        } else if (sscanf(s, "upper %f %f %f", &a, &b, &c) == 3) {
            t_upper[0] = a; t_upper[1] = b; t_upper[2] = c;
        } else if (!strncmp(s, "source ", 7)) {
            t_source = !strcmp(s + 7, "cam") ? 1 : 0;
        } else if (sscanf(s, "fist %d", &i) == 1) {
            t_fist = i;
        } else if (sscanf(s, "jitter %f", &a) == 1) {
            t_jitter = a;
        } else if (!strcmp(s, "nohand")) {
            t_hand.valid = 0;
        } else if (!strcmp(s, "showhand")) {
            t_hand.valid = 1;
        } else if (sscanf(s, "log %d", &i) == 1) {
            t_log_ms = i;
        }
        LeaveCriticalSection(&g_cs);
        if (!strncmp(s, "cmd ", 4)) test_queue_cmd(s + 4);
        else if (!strncmp(s, "mark ", 5)) tlog("==== %s", s + 5);
        else if (sscanf(s, "expect held %d", &i) == 1) { sprintf(line, "!held %d", i); test_queue_cmd(line); }
        else if (!strcmp(s, "quit")) test_queue_cmd("quit");
        tlog("> %s", s);
    }
    fclose(f);
    tlog("script done");
    return 0;
}

static void test_log_state(void)         /* main thread, from part 0 query */
{
    static ULONGLONG last;
    ULONGLONG now = GetTickCount64();
    BYTE *pi = (BYTE *)VA(0x10A62A78);   /* CSixensePlayerInput */
    if (!t_log_ms || now - last < (ULONGLONG)t_log_ms) return;
    last = now;
    __try {
        BYTE *pipe = *(BYTE **)VA(0x10A63E9C);
        tlog("held=%d hand v=%d pos=(%.2f %.2f %.2f) n=(%.2f %.2f %.2f) open=%d/%d | game armed=%d tipin=%d img=%dx%d"
             " f6b0=%d f75d=%d f2e6=%d open760=%d"
             " nrm=(%.2f %.2f %.2f) rot=(%.2f %.2f %.2f) pos680=(%.2f %.2f %.2f)",
             player_holds(), t_hand.valid, t_hand.pos[0], t_hand.pos[1], t_hand.pos[2],
             t_hand.nrm[0], t_hand.nrm[1], t_hand.nrm[2], t_hand.open, t_hand.state,
             *(BYTE *)VA(0x10A63E96), *(BYTE *)VA(0x10A63B10),
             pipe ? *(int *)(pipe + 0x2940) : -1, pipe ? *(int *)(pipe + 0x2944) : -1,
             pi[0x6B0], pi[0x75D], pi[0x2E6], *(int *)(pi + 0x760),
             *(float *)(pi + 0x68C), *(float *)(pi + 0x690), *(float *)(pi + 0x694),
             *(float *)(pi + 0x6A4), *(float *)(pi + 0x6A8), *(float *)(pi + 0x6AC),
             *(float *)(pi + 0x680), *(float *)(pi + 0x684), *(float *)(pi + 0x688));
        tlog("   img palm=(%.1f %.1f) tip=(%.1f %.1f) valid palm=%d tip=%d upper=%d edge=%.3f use_tip=%d",
             *(float *)VA(0x10A63CBC), *(float *)VA(0x10A63CC0), *(float *)VA(0x10A63C3C), *(float *)VA(0x10A63C40),
             *(BYTE *)VA(0x10A63E5A), *(BYTE *)VA(0x10A63E5B), *(BYTE *)VA(0x10A63E61),
             *(float *)(*(BYTE **)VA(0x10A63F6C) + 0x2C), *(int *)(*(BYTE **)VA(0x10A63FC4) + 0x30));
    } __except (EXCEPTION_EXECUTE_HANDLER) { tlog("game state read failed"); }
}

/* Returns 1 and fills node when test mode handles this query (or -3 result). */
static int test_query(DWORD label, BYTE *node, int *result)
{
    DWORD part = label & 0xFFFFu;
    float p[3], d;
    if (!t_on) return 0;
    if (!InterlockedExchange(&t_started, 1)) {
        tlog("first hand query - starting script");
        CreateThread(NULL, 0, test_thread, NULL, 0, NULL);
    }
    if (part == 0) { test_run_cmds(); test_log_state(); t_flip = !t_flip; }
    if (t_source) return 0;               /* the real webcam code handles it */

    EnterCriticalSection(&g_cs);
    if (!t_hand.valid) { LeaveCriticalSection(&g_cs); *result = -3; return 1; }
    p[0] = t_hand.pos[0] + (t_flip ? t_jitter : -t_jitter);
    p[1] = t_hand.pos[1]; p[2] = t_hand.pos[2];
    if (part == 6)      { p[0] += t_tip[0];   p[1] += t_tip[1];   p[2] += t_tip[2]; }
    else if (part == 7) { p[0] += t_upper[0]; p[1] += t_upper[1]; p[2] += t_upper[2]; }
    d = p[2] > 0.05f ? p[2] : 0.05f;
    memset(node, 0, 0x80);
    *(DWORD *)(node + 0x04) = label;
    *(ULONGLONG *)(node + 0x10) = GetTickCount64() * 10000ull;
    put3(node + 0x18, -p[0], p[2], p[1]);
    put3(node + 0x24, 160.0f + p[0] / d * 224.0f, 120.0f - p[1] / d * 224.0f, p[2] * 1000.0f);
    put3(node + 0x58, -t_hand.nrm[0], t_hand.nrm[2], t_hand.nrm[1]);
    *(DWORD *)(node + 0x64) = (DWORD)t_hand.open;
    *(DWORD *)(node + 0x68) = (DWORD)t_hand.state;
    *(DWORD *)(node + 0x70) = 100;
    LeaveCriticalSection(&g_cs);
    *result = 0;
    return 1;
}

/* source cam: what tracker.py would write (fingertip like a real open hand) */
static int test_cam(CamShm *o)
{
    int i;
    EnterCriticalSection(&g_cs);
    memset(o, 0, sizeof *o);
    o->magic = CAM_MAGIC;
    o->tick_ms = GetTickCount64();
    o->valid = t_hand.valid;
    o->closed = t_fist;
    o->openness = t_fist ? 10 : 90;
    for (i = 0; i < 3; i++) {
        o->hand[i] = o->tip[i] = o->upper[i] = t_hand.pos[i];
        o->normal[i] = t_hand.nrm[i];
    }
    o->tip[1] += t_fist ? -0.01f : 0.09f;     /* index tip: curled / extended */
    o->upper[1] += 0.05f;                     /* middle knuckle               */
    LeaveCriticalSection(&g_cs);
    return o->valid ? 1 : -1;
}
