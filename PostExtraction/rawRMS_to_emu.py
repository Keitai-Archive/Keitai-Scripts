import os
import argparse
import struct

parser = argparse.ArgumentParser(description="Raw RMS converter")
parser.add_argument("input")
parser.add_argument("-o", "--out_dir", default=None)
args = parser.parse_args()

with open(args.input, "rb") as inf:
    rms_data = inf.read()

out_dir = args.out_dir or os.path.join(
    os.path.dirname(args.input),
    os.path.splitext(os.path.basename(args.input))[0] + "_RMS",
)
os.makedirs(out_dir, exist_ok=True)


def get_rms_partitions(rms_data):
    rms_partitions = []
    header_size = 5 + 1 + rms_data[5] + 0xC + 4
    partition_num = int.from_bytes(rms_data[header_size - 4: header_size], "big")

    off = header_size

    print("Started", hex(off), f"{partition_num=}")
    for i in range(partition_num):
        partition = int.from_bytes(rms_data[off : off + 4], "big")
        print("partition", partition,  "offset", hex(off))

        if partition != (i+1) :
            raise Exception("")

        size = int.from_bytes(rms_data[off + 4 : off + 8], "big")
        content = rms_data[off + 8 : off + 8 + size]
        rms_partitions.append(content)
        off += 8 + size
            
    return rms_partitions


rms_partitions = get_rms_partitions(rms_data)
if len(rms_partitions) == 0:
     raise Exception("Failed to detect RMS.")

total_size = 0
with open(os.path.join(out_dir, "mexa_01"), "wb") as out01f, open(os.path.join(out_dir, "mexa_02"), "wb") as out02f:
    for i, rms_partition in enumerate(rms_partitions):
        out02f.write(rms_partition)

        size = len(rms_partition)
        out01f.write(struct.pack(">IIII", 0, i+1, total_size, size))
        total_size += size

        with open(os.path.join(out_dir, f"{i+1}.rms"), "wb") as outrf:
            outrf.write(rms_partition)

print(f"=> {out_dir}")
