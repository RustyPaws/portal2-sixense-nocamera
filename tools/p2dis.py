import sys,pefile
from capstone import *
f=sys.argv[1]; pe=pefile.PE(f); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
md=Cs(CS_ARCH_X86,CS_MODE_32)
# string resolution
lo=base; hi=base+pe.OPTIONAL_HEADER.SizeOfImage
def strat(v):
    try:
        off=pe.get_offset_from_rva(v-base); d=pe.__data__[off:off+80]
        s=d.split(b'\0')[0]
        if len(s)>=4 and all(32<=c<127 for c in s): return s.decode()
    except: pass
imp={}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    for i in e.imports: imp[i.address]=(e.dll.decode()+'!'+(i.name.decode() if i.name else '#%d'%i.ordinal))
va=int(sys.argv[2],16); n=int(sys.argv[3])
for ins in md.disasm(code[va-tva:va-tva+n*10],va):
    c=''
    for tok in ins.op_str.replace('[',' ').replace(']',' ').replace(',',' ').split():
        if tok.startswith('0x'):
            v=int(tok,16)
            if v in imp: c+=' ; '+imp[v]
            elif lo<=v<hi:
                s=strat(v)
                if s: c+=' ; "%s"'%s
    print("%x: %-8s %s%s"%(ins.address,ins.mnemonic,ins.op_str,c)); n-=1
    if n<=0: break
