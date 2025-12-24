import argparse
import os

parser = argparse.ArgumentParser(description=".sp to .scr converter")
parser.add_argument("input", help="An .sp file that contains a size header.")
parser.add_argument("out_dir")
parser.add_argument("-2", "--idkdoja2", action="store_true", help="Output the file name for idkdoja 2.x's scr.")
args = parser.parse_args()

HEADER_SIZE = 0x40
filename = os.path.splitext(os.path.basename(args.input))[0]
with open(args.input, "rb") as inf:
    sp_data = inf.read()

if len(sp_data) < HEADER_SIZE:
    raise ValueError("The file size is smaller than the header size.")

sp_sizes = []
for off in range(0, HEADER_SIZE, 4):
    sp_size = int.from_bytes(sp_data[off : off + 4], "little")
    if sp_size == 0xFF_FF_FF_FF:
        break
    sp_sizes.append(sp_size)

if sum(sp_sizes) != len(sp_data) - HEADER_SIZE:
    raise ValueError("Header information and actual filesize do not match.")

if args.idkdoja2 and len(sp_sizes) > 1:
    raise ValueError("In DoJa 2.x, the number of scratchpad partitions is either one or zero.")

os.makedirs(args.out_dir, exist_ok=True)

start_off = HEADER_SIZE
for i, sp_size in enumerate(sp_sizes):
    end_off = start_off + sp_size
    out_filename = f"{filename}.scr" if args.idkdoja2 else f"{filename}{i}.scr"
    with open(os.path.join(args.out_dir, out_filename), "wb") as outf:
        outf.write(sp_data[start_off : end_off])
    start_off = end_off