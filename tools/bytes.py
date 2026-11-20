import sys,pefile
from capstone import *
pe=pefile.PE(sys.argv[1],fast_load=True); b=pe.OPTIONAL_HEADER.ImageBase
md=Cs(CS_ARCH_X86,CS_MODE_32)
for a in sys.argv[2:]:
    va=int(a,16); o=pe.get_offset_from_rva(va-b); d=pe.__data__[o:o+16]
    for i in md.disasm(d,va,count=2): print("%x  %-14s %s %s"%(i.address,i.bytes.hex(),i.mnemonic,i.op_str))
