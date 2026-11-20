import sys,struct,pefile
f=sys.argv[1]; pe=pefile.PE(f,fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
for a in sys.argv[2:]:
    t=int(a,16); print("== callers of",a)
    for i in range(len(code)-5):
        if code[i] in (0xe8,0xe9):
            rel=struct.unpack_from('<i',code,i+1)[0]
            if tva+i+5+rel==t: print("  %s at %x"%('call' if code[i]==0xe8 else 'jmp',tva+i))
    # also absolute refs (vtables)
    p=struct.pack('<I',t); j=0; d=pe.__data__
    while True:
        j=d.find(p,j)
        if j<0:break
        print("  ptr at %x"%(base+pe.get_rva_from_offset(j))); j+=1
