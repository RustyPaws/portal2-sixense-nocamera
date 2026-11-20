import sys,pefile
pe=pefile.PE(sys.argv[1],fast_load=True); b=pe.OPTIONAL_HEADER.ImageBase
for a in sys.argv[2:]:
    o=pe.get_offset_from_rva(int(a,16)-b); print(a, pe.__data__[o:o+48])
