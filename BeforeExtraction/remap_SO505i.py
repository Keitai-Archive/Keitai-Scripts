import sys
import re
import os
import argparse

parser = argparse.ArgumentParser(description="SO505i FTL Remapper")
parser.add_argument("input", help="A file with the same name and the .oob extension must be present in the same folder.")
parser.add_argument("-o", "--output", default=None)
args = parser.parse_args()

def detect_sector_per_block(oob_data):
    start = 0 
    for i in range(len(oob_data) // 0x10):
        if oob_data[(i*0x10) + 0xB : (i*0x10) + 0xD] != b"\xFF" * 2:
            break
        else:
            start += 1

    count = 0
    for i in range(start, len(oob_data) // 0x10):
        block_id = (int.from_bytes(oob_data[(i*0x10) + 0xB : (i*0x10) + 0xD], "big") & 0x0FFF) >> 1

        if count == 0:
            reference_block_id = block_id
            count += 1
        else:
            if reference_block_id == block_id:
                count +=1
            else:
                return count


def main():
    with open(args.input, "rb") as inf:
        main_data = inf.read()

    with open(re.sub(r"\.bin$", ".oob", args.input), "rb") as inf:
        oob_data = inf.read()


    output = args.output or os.path.join(
            os.path.dirname(args.input),
            f"{os.path.splitext(os.path.basename(args.input))[0]}_remapped.bin"
    )

    sector_per_block = detect_sector_per_block(oob_data)
    if sector_per_block == 0: raise ValueError("Failure to detect a sector_per_block.")
    print("sector_per_block:", hex(sector_per_block))

    data_sector_size = 0x200
    oob_sector_size = 0x10

    data_block_size = data_sector_size * sector_per_block
    oob_block_size = oob_sector_size * sector_per_block

    assert len(main_data) % data_sector_size == 0
    assert len(oob_data) % oob_sector_size == 0
    assert (len(oob_data) // oob_sector_size) * data_sector_size == len(main_data)
    
    
    chunk_dict = {}

    for sector_i in range(len(oob_data) // oob_sector_size):
        oob_sector = oob_data[sector_i * oob_sector_size : (sector_i+1) * oob_sector_size]
        block_id = (int.from_bytes(oob_sector[0xB:0xD], "big") & 0x0FFF) >> 1

        if not block_id in chunk_dict: chunk_dict[block_id] = b""


        if oob_sector[0x6:0x8] in (b"\xff\xff", b"\x00\x00") or oob_sector[0xB:0xD] in (b"\xff\xff", b"\x00\x00"):
            continue

        if oob_sector[:4] == b"\x00\x00\x00\x00":
            pass

            
        if block_id in chunk_dict:
            chunk_dict[block_id] += main_data[sector_i * data_sector_size : (sector_i+1) * data_sector_size]
        else:
            print(f"WAMN: {hex(block_id)}")
    
    with open(output, "wb") as outf:
        for block_id, main_data in sorted(chunk_dict.items()):
            outf.seek(block_id * data_block_size)
            outf.write(main_data)
            #print(hex(block_id))

if __name__ == "__main__":
    main()
