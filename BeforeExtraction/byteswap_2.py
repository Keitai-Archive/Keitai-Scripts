import sys

# with open(sys.argv[1], "rb") as inf:
    # with open(sys.argv[1]+"out.bin", "wb") as outf:
        # while True:
            # d = inf.read(2)
            # if not d:
                # break
            # assert len(d) == 2
            # outf.write(d[::-1])



with open(sys.argv[1], "rb") as inf:
    flash = inf.read()

out = bytearray()
for ind in range(0, len(flash), 2):
    d = flash[ind:ind+2:]
    out += d[::-1]
    
with open(sys.argv[1]+"_2swapped.bin", "wb") as outf:
    outf.write(out)