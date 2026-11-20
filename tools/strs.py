import re,sys,pefile
f=sys.argv[1]; pat=re.compile(sys.argv[2].encode(),re.I)
d=open(f,'rb').read(); pe=pefile.PE(f,fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase
for m in re.finditer(rb'[\x20-\x7e]{5,}',d):
    s=m.group()
    if pat.search(s):
        try: va=base+pe.get_rva_from_offset(m.start())
        except: va=0
        print(hex(va), s.decode()[:160])
