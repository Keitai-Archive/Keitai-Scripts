import sys

with open(sys.argv[1], "rb") as inf:
    flash = inf.read()

out = bytearray()
for ind in range(0, len(flash), 4):
    d = flash[ind:ind+4]
    out += d[::-1]
    
with open(sys.argv[1]+"_4swapped.bin", "wb") as outf:
    outf.write(out)