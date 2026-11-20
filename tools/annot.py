import sys,json,re,subprocess
cv=json.load(open('cvars.json')); m={}
for k,v in cv.items():
    k=int(k,16); m[k]=v; m[k+0x1c]=v+'(parent)'
for line in sys.stdin:
    line=line.rstrip()
    for h in re.findall(r'0x10[0-9a-f]{6}',line):
        if int(h,16) in m: line+='   <'+m[int(h,16)]+'>'
    print(line)
