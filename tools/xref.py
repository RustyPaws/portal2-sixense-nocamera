import sys,struct,pefile
from capstone import *
f=sys.argv[1]; pe=pefile.PE(f); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
md=Cs(CS_ARCH_X86,CS_MODE_32)
def find_str(s):
    d=pe.__data__; i=d.find(s.encode()+b'\0')
    return base+pe.get_rva_from_offset(i) if i>=0 else None
def dis(va,n=40):
    off=va-tva
    for ins in md.disasm(code[off:off+n*8],va):
        print("   %x: %s %s"%(ins.address,ins.mnemonic,ins.op_str)); n-=1
        if n<=0: break
def funcstart(va):
    off=va-tva
    # walk back to CC/90 padding followed by typical prologue
    o=off
    while o>0:
        if code[o-1] in (0xcc,0x90,0xc3) and code[o] in (0x55,0x83,0x81,0x56,0x53,0x8b,0x6a,0x51,0x57,0xa1,0x68,0xb8,0xe9):
            if code[o-1]!=0xc3 or code[o-2] in (0xcc,0x90): return tva+o
        o-=1
for arg in sys.argv[2:]:
    va=int(arg,16) if arg.startswith('0x') else find_str(arg)
    print("==",arg,hex(va) if va else None)
    if not va: continue
    pat=struct.pack('<I',va); i=0
    while True:
        i=code.find(pat,i)
        if i<0: break
        ref=tva+i; fs=funcstart(i+tva)
        print(" ref at %x  (func ~%x)"%(ref,fs)); i+=1
