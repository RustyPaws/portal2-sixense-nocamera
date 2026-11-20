import pefile, sys
for f in sys.argv[1:]:
    pe = pefile.PE(f)
    print("==", f, hex(pe.OPTIONAL_HEADER.ImageBase))
    for e in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
        n = e.dll.decode()
        syms = [ (i.name or b'#%d'%i.ordinal).decode() for i in e.imports]
        if n.lower() in ('kernel32.dll','user32.dll','advapi32.dll','gdi32.dll','msvcr100.dll','msvcp100.dll'):
            print(" ", n, len(syms)); continue
        print(" ", n, syms[:40])
    if hasattr(pe,'DIRECTORY_ENTRY_EXPORT'):
        print("  EXPORTS:", [ (s.name or b'').decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols][:60])
