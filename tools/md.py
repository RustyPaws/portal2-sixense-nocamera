import sys
from minidump.minidumpfile import MinidumpFile
m=MinidumpFile.parse(sys.argv[1])
e=m.exception.exception_records[0]
rec=e.ExceptionRecord
print("code",hex(rec.ExceptionCode.value if hasattr(rec.ExceptionCode,'value') else rec.ExceptionCode),"addr",hex(rec.ExceptionAddress),"params",[hex(x) for x in rec.ExceptionInformation[:rec.NumberParameters]])
print("thread",e.ThreadId)
addr=rec.ExceptionAddress
for mod in m.modules.modules:
    if mod.baseaddress<=addr<mod.baseaddress+mod.size: print("in",mod.name,hex(mod.baseaddress),"rva",hex(addr-mod.baseaddress))
for mod in m.modules.modules:
    if 'sixense' in mod.name.lower() or 'client' in mod.name.lower(): print(" mod",mod.name,hex(mod.baseaddress))
# context
for t in m.threads.threads:
    if t.ThreadId==e.ThreadId:
        ctx=t.ContextObject
        print(ctx)
