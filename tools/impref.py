import sys,struct,pefile
f=sys.argv[1]; pe=pefile.PE(f); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
want=set(sys.argv[2:])
for e in pe.DIRECTORY_ENTRY_IMPORT:
    for i in e.imports:
        n=(i.name or b'').decode()
        if n in want or any(w in n for w in want):
            p=struct.pack('<I',i.address); j=0; print("==",n,hex(i.address))
            while True:
                j=code.find(p,j)
                if j<0:break
                print("   ref %x"%(tva+j-2)); j+=1
