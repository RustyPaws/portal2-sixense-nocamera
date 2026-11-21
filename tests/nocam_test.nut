// VScript helpers for tools/hand_test.py (copied to scripts/vscripts/).
// Load with "cmd script_execute nocam_test", then e.g. "cmd script TestList()".

function TestPrint(s) { printl("TEST " + s) }

function TestList(pat = "prop")
{
    for (local e = Entities.First(); e; e = Entities.Next(e)) {
        local c = e.GetClassname()
        if (c.find(pat) != null)
            TestPrint("ent " + c + " '" + e.GetName() + "' " + e.GetModelName() + " " + e.GetOrigin())
    }
}

function TestPlayer()
{
    local p = GetPlayer()
    TestPrint("player " + p.GetOrigin() + " fwd " + p.GetForwardVector())
}

// Put the first entity of class `cls` (or named `cls`) in front of the player.
function TestPlace(cls = "prop_weighted_cube", dist = 70, up = 40)
{
    local e = Entities.FindByClassname(null, cls)
    if (!e) e = Entities.FindByName(null, cls)
    if (!e) { TestPrint("place: no " + cls); return }
    local p = GetPlayer()
    local f = p.GetForwardVector()
    e.SetOrigin(p.GetOrigin() + Vector(f.x * dist, f.y * dist, up))
    e.SetVelocity(Vector(0, 0, 0))
    TestPrint("placed " + cls + " at " + e.GetOrigin())
}

function TestWhere(cls = "prop_weighted_cube")
{
    local e = Entities.FindByClassname(null, cls)
    if (!e) e = Entities.FindByName(null, cls)
    if (!e) { TestPrint("where: no " + cls); return }
    TestPrint("where " + cls + " " + e.GetOrigin())
}

// Turn the player's view to look at entity `cls` (class or name).
function TestLook(cls = "prop_weighted_cube")
{
    local e = Entities.FindByClassname(null, cls)
    if (!e) e = Entities.FindByName(null, cls)
    if (!e) { TestPrint("look: no " + cls); return }
    local p = GetPlayer()
    local d = e.GetCenter() - p.EyePosition()
    local yaw = atan2(d.y, d.x) * 180.0 / PI
    local pitch = -atan2(d.z, sqrt(d.x * d.x + d.y * d.y)) * 180.0 / PI
    p.SetAngles(pitch, yaw, 0)
    TestPrint("look pitch " + pitch + " yaw " + yaw + " eye " + p.EyePosition())
}

// Assertions: TestMark() remembers where `cls` is; TestMoved(min) / TestStill(max)
// print "TEST PASS ..." or "TEST FAIL ..." (distance since the mark).
::nocam_mark <- null
function TestMark(cls = "prop_weighted_cube")
{
    local e = Entities.FindByClassname(null, cls)
    if (!e) e = Entities.FindByName(null, cls)
    ::nocam_mark = e ? e.GetOrigin() : null
}
function _TestDist(cls)
{
    local e = Entities.FindByClassname(null, cls)
    if (!e) e = Entities.FindByName(null, cls)
    if (!e || ::nocam_mark == null) return -1
    return (e.GetOrigin() - ::nocam_mark).Length()
}
function TestMoved(what, cls = "prop_weighted_cube", min = 16)
{
    local d = _TestDist(cls)
    TestPrint((d >= min ? "PASS " : "FAIL ") + what + ": moved " + d)
}
function TestStill(what, cls = "prop_weighted_cube", max = 4)
{
    local d = _TestDist(cls)
    TestPrint((d >= 0 && d <= max ? "PASS " : "FAIL ") + what + ": moved " + d)
}

// Rotation check: TestMarkAng() remembers the angles of `cls`, TestTurned(min)
// passes when any angle changed by at least `min` degrees.
::nocam_ang <- null
function _TestEnt(cls)
{
    local e = Entities.FindByClassname(null, cls)
    return e ? e : Entities.FindByName(null, cls)
}
function TestMarkAng(cls) { local e = _TestEnt(cls); ::nocam_ang = e ? e.GetAngles() : null }
function TestTurned(what, cls, min = 10)
{
    local e = _TestEnt(cls)
    if (!e || ::nocam_ang == null) { TestPrint("FAIL " + what + ": no entity"); return }
    local a = e.GetAngles(), d = a - ::nocam_ang
    local m = 0.0
    foreach (v in [d.x, d.y, d.z]) { v = fabs(v); if (v > 180) v = 360 - v; if (v > m) m = v }
    TestPrint((m >= min ? "PASS " : "FAIL ") + what + ": turned " + m + " deg, now " + a)
}
