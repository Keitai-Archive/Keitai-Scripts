import sys
import re

CHUNK_SIZE = 4

with open(sys.argv[1], "rb") as inf, open(sys.argv[2], "rb") as inf2, \
     open(sys.argv[1]+"_4interleaved.bin", "wb") as outf:
    while True:
        chunk1 = inf.read(CHUNK_SIZE)
        chunk2 = inf2.read(CHUNK_SIZE)
        
        if not chunk1 and not chunk2: break
        
        if chunk1:
            outf.write(chunk1)
        if chunk2:
            outf.write(chunk2)