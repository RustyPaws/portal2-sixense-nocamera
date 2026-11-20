import sys,struct,pefile,re,json
f=sys.argv[1]; pe=pefile.PE(f,fast_load=True); base=pe.OPTIONAL_HEADER.ImageBase
text=[s for s in pe.sections if s.Name.startswith(b'.text')][0]
code=text.get_data(); tva=base+text.VirtualAddress
def s(v):
    try:
        o=pe.get_offset_from_rva(v-base); return pe.__data__[o:o+96].split(b'\0')[0].decode('latin1')
    except: return None
out={}
# pattern: 68 <help?> ... 68 <flags> 68 <default> 68 <name> B9 <obj> E8
for m in re.finditer(rb'\x68(....)\xb9(....)\xe8',code,re.S):
    name=struct.unpack('<I',m.group(1))[0]; obj=struct.unpack('<I',m.group(2))[0]
    n=s(name)
    if n and re.match(r'^[a-z_][a-z0-9_]+$',n) and 0x10900000<obj<0x10b00000:
        out[obj]=n
json.dump({hex(k):v for k,v in out.items()},open(sys.argv[2],'w'))
print(len(out))
for q in sys.argv[3:]:
    q=int(q,16)
    for k,v in out.items():
        if k==q or k+0x1c==q: print(hex(q),v)
