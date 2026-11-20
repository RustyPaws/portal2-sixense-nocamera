import sys,struct
from minidump.minidumpfile import MinidumpFile
m=MinidumpFile.parse(sys.argv[1])
e=m.exception.exception_records[0]
r=m.get_reader()
mods=[(x.baseaddress,x.size,x.name.split(chr(92))[-1]) for x in m.modules.modules]
def sym(v):
    for b,s,n in mods:
        if b<=v<b+s: return "%s+%x"%(n,v-b)
ctx=None
for t in m.threads.threads:
    if t.ThreadId==e.ThreadId: ctx=t.ContextObject
# exception context
import minidump
ec=e.ThreadContext
data=m.file_handle if False else None
f=open(sys.argv[1],'rb'); f.seek(ec.Rva); raw=f.read(ec.DataSize)
# x86 CONTEXT: Eax at 0xb0, Ecx 0xac, Edx 0xa8, Ebx 0xa4, Esi 0xa0, Edi 0x9c, Ebp 0xb4, Eip 0xb8, Esp 0xc4
g=lambda o: struct.unpack_from('<I',raw,o)[0]
for n,o in [('eax',0xb0),('ecx',0xac),('edx',0xa8),('ebx',0xa4),('esi',0xa0),('edi',0x9c),('ebp',0xb4),('eip',0xb8),('esp',0xc4)]:
    print(n,hex(g(o)))
esp=g(0xc4)
rd=r.get_buffered_reader()
rd.move(esp); buf=rd.read(0x1000)
for i in range(0,len(buf),4):
    v=struct.unpack_from('<I',buf,i)[0]; s=sym(v)
    if s and ('client_sixense' in s or 'engine' in s): print(hex(esp+i),hex(v),s)
