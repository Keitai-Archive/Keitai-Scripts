import argparse
import os

PRINT_FS = None

print("Start")
parser = argparse.ArgumentParser(description="Keitai M4 Assemble")
parser.add_argument("input")
parser.add_argument("-o", "--output", default=None)
parser.add_argument("-e","--add_extension", action=argparse.BooleanOptionalAction, help="Add extension to output files based on content type (uncomplete)")
parser.add_argument("-v","--V601N_mode", action=argparse.BooleanOptionalAction, help="Strip the 4-byte header at the start of the file.")

args = parser.parse_args()

output = args.output or os.path.join(
    os.path.dirname(args.input),
    f"{os.path.basename(args.input)}_output"
)
os.makedirs(output, exist_ok=True)

def detect_extension(data):
    if data[:4] == b"\x50\x4B\x03\x04":
        return "jar"
    elif (
        data[:4] == b"\xff\xd8\xff\xe0"
            and data[6:0xA] == b"JFIF"
        or data[:4] == b"\xff\xd8\xff\xe1"
            and data[6:0xA] == b"Exif"
        or data[:4] == b"\xFF\xD8\xFF\xDB"
        or data[:4] == b"\xFF\xD8\xFF\xEE"
       ):
        return "jpg"
    elif data[:4] == b"melo":
        return "mld"
    elif data.find(b".jam") != -1:
        return "adf"
    elif data[:4] == b"MMMD":
        return "mmf"
    elif data.find(b"MIDlet-Name:") != -1:
        return "jad"
    else:
        return "bin"
    

vspace = {}
with open(args.input, "rb") as file:
    data = file.read(0x20000)
    block_number = 0
    while len(data) > 0:
        if data[0x1FFF9:0x1FFFE] == b"\x55\x55\x55\xFF\xFF":
            off = 0
            while data[off : off + 0x10] != b"\xFF" * 0x10:
                chunk_id = data[off+2]
                fs = int.from_bytes(data[off + 3 : off + 5], "little")
                loc = int.from_bytes(data[off + 8 : off + 0xA], "little")
                size = int.from_bytes(data[off + 0xC : off + 0x10], "little")

                unknown = int.from_bytes(data[off + 0x6 : off + 0x8], "little")

                chunk = data[
                    0x1FFE0 - (loc * 0x80) : 0x1FFE0 - (loc * 0x80) + size
                ]

                vspace.setdefault(fs, {})
                vspace[fs].setdefault(chunk_id, []).append({
                    "unknown": unknown,
                    "chunk": chunk
                })
                

                
                if PRINT_FS and fs == PRINT_FS:
                    print(
                        f"{block_number+off:07X}" + ":"
                        , " ".join([f"{byte:0=2X}" for byte in data[off + 0 : off + 0x10]]) 
                        , f"(chunk_id={hex(chunk_id)}, {size=}, {unknown=})"
                    )
                
                
                if chunk_id in vspace[fs]:
                    pass
                    #print(f"WARN: chunk_id {chunk_id} of fs {fs} is duplicated. ({hex(block_number + off)})")
                

                off += 0x10
        data = file.read(0x20000)
        block_number += 0x20000

for fs, fs_dict in vspace.items():
    file_data = bytearray()
    for chunk_id, chunk_dicts in sorted(fs_dict.items(), key=lambda x: int(x[0])):
        for chunk_dict in chunk_dicts:
            if chunk_dict["unknown"] == 1: break
            file_data += chunk_dict["chunk"]
            break

    if args.V601N_mode:
        file_data = file_data[4:]

    ext = detect_extension(file_data) if args.add_extension else "bin"
    file_name = f"region_{fs:05d}.{ext}"

    if len(file_data) > 0:
        print(f"fs {fs}: {file_name}")
        with open(os.path.join(output, file_name), "wb") as file:
            file.write(file_data)

print("End")
