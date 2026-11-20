import sys,struct,pefile
from capstone import *
f=sys.argv[1]; pe=pefile.PE(f,fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
md=Cs(CS_ARCH_X86,CS_MODE_32)
for g in sys.argv[2:]:
    p=struct.pack('<I',int(g,16)); j=0
    print("=====",g)
    while True:
        j=code.find(p,j)
        if j<0: break
        # find instruction start: try offsets back 1..3
        for back in (1,2,3,4):
            st=j-back; ins=list(md.disasm(code[st:st+80],tva+st,count=5))
            if ins and ins[0].address+ins[0].size>j+3 and g[2:] in ins[0].op_str.replace('0x',''):
                print("  "+" | ".join("%x %s %s"%(i.address,i.mnemonic,i.op_str) for i in ins)); break
        j+=1
