import argparse
import os

PRINT_FS = None

print("Start")
parser = argparse.ArgumentParser(description="Keitai M4 Assemble")
parser.add_argument("input")
parser.add_argument("-o", "--output", default=None)
parser.add_argument("-e","--add-extension", action=argparse.BooleanOptionalAction, help="Adds an extension by detecting the file type from its magic number. Misclassification is possible.")
parser.add_argument("-v","--V601N-mode", action=argparse.BooleanOptionalAction, help="Strip the 4-byte header at the start of the file.")
parser.add_argument("-nw","--no-warning", action="store_true", help="No warning is displayed.")

args = parser.parse_args()

do_warning = not args.no_warning
 
out_dir = args.output or os.path.join(
    os.path.dirname(args.input),
    f"{os.path.basename(args.input)}_output"
)
os.makedirs(out_dir, exist_ok=True)


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
    elif data[:4] == b"\x89PNG":
        return "png"
    elif data[:4] == b"melo":
        return "mld"
    elif data.find(b".jam") != -1:
        return "adf"
    elif data[:4] == b"MMMD":
        return "mmf"
    
    if data.find(b"MIDlet-Name:") != -1:
        try:
             data.decode("UTF-8")
             return "jad"
        except:
            pass
        
    return "bin"
    
vspace = {}

with open(args.input, "rb") as inf:
    data = inf.read(0x20000)
    block_number = 0

    while len(data) > 0:
        if data[0x1FFFA:0x1FFFE] == b"\x55\x55\xFF\xFF":
            off = 0
            while data[off : off + 0x10] != b"\xFF" * 0x10:
                chunk_id = data[off+2]
                fs = int.from_bytes(data[off + 3 : off + 5], "little")
                loc = int.from_bytes(data[off + 8 : off + 0xA], "little")
                size = int.from_bytes(data[off + 0xC : off + 0x10], "little")
                flag = int.from_bytes(data[off + 0x6 : off + 0x8], "little")

                chunk = data[
                    0x1FFE0 - (loc * 0x80) : 0x1FFE0 - (loc * 0x80) + size
                ]

                if fs == PRINT_FS:
                    print(
                        f"{block_number+off:07X}" + ":"
                        , " ".join([f"{byte:0=2X}" for byte in data[off + 0 : off + 0x10]]) 
                        , f"(chunk_id={hex(chunk_id)}, {size=}, {flag=})"
                    )
               
                if flag != 1:
                    vspace.setdefault(fs, {})
                    if chunk_id in vspace[fs]:
                        if do_warning or fs == PRINT_FS:
                            print(f"WARN: chunk_id {chunk_id} of fs {fs} is duplicated. (metadata offset: {hex(block_number + off)})")
                    else:
                        vspace[fs][chunk_id] = chunk

                off += 0x10
        data = inf.read(0x20000)
        block_number += 0x20000

for fs, fs_dict in vspace.items():
    file_data = bytearray()
    for chunk_id, chunk in sorted(fs_dict.items(), key=lambda x: int(x[0])):
        file_data += chunk

    if args.V601N_mode:
        file_data = file_data[4:]

    ext = detect_extension(file_data) if args.add_extension else "bin"
    file_name = f"region_{fs:05d}.{ext}"

    if len(file_data) > 0:
        with open(os.path.join(out_dir, file_name), "wb") as outf:
            outf.write(file_data)

print("End")
