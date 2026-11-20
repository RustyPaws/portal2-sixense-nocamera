import sys,struct,re
d=open(sys.argv[1],'rb').read()
off,ln=struct.unpack_from('<ii',d,8)  # lump 0 = entities
e=d[off:off+ln].decode('latin1')
for blk in re.findall(r'\{[^{}]*\}',e):
    if re.search(sys.argv[2],blk): print(blk.strip()[:900]); print('--')
