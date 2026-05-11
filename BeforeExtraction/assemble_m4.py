import argparse
import os

PRINT_FS = None
RENAME = True

print("Start")
parser = argparse.ArgumentParser(description="Keitai M4 Assemble")
parser.add_argument("input")
parser.add_argument("-o", "--output", default=None)
parser.add_argument("-e","--detect-extension", action=argparse.BooleanOptionalAction, help="Adds an extension by detecting the file type from its magic number. Misclassification is possible.")
parser.add_argument("-v","--V601N-mode", action=argparse.BooleanOptionalAction, help="Strip the 4-byte header at the start of the file.")
parser.add_argument("-nw","--no-warning", action="store_true", help="No warning is displayed.")

args = parser.parse_args()

do_warning = not args.no_warning
 
out_dir = args.output or os.path.join(
    os.path.dirname(args.input),
    f"{os.path.basename(args.input)}_output"
)
os.makedirs(out_dir, exist_ok=True)

TYPE_DEF = {
    "jar": 512,
    "rms": 1024,
    "ico": 768,
}

TRANSLATION_TABLE = str.maketrans({
    "\\": "＼",
    "/": "／",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "”",
    "<": "＜",
    ">": "＞",
    "|": "｜",
})

def sanitize_filename(filename: str) -> str:
    sanitized = filename.translate(TRANSLATION_TABLE)

    sanitized = "".join(
        " " if ord(c) < 32 else c
        for c in sanitized
    )

    if not sanitized:
        sanitized = "untitled"

    return sanitized

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
            #print("\n")
            #print(" ".join([f"{byte:0=2X}" for byte in data[0x1FFE0:0x1FFF0]]))
            #print(" ".join([f"{byte:0=2X}" for byte in data[0x1FFF0:0x20000]]))
            off = 0
            while data[off : off + 0x10] != b"\xFF" * 0x10:
                marker = data[off+1]
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
                    dir = os.path.join(out_dir, "chunk")
                    os.makedirs(dir, exist_ok=True)
                    with open(os.path.join(dir, f"{fs}_{hex(block_number + off)}_{chunk_id}_{flag}.bin"), "wb") as outf:
                        outf.write(chunk)
               
                if marker == 0xFC and flag != 1:
                    vspace.setdefault(fs, {})
                    if chunk_id in vspace[fs]:
                        if do_warning:
                            print(f"WARN: chunk_id {chunk_id} of fs {fs} is duplicated. (metadata offset: {hex(block_number + off)})")
                    else:
                        vspace[fs][chunk_id] = chunk

                off += 0x10
        data = inf.read(0x20000)
        block_number += 0x20000

file_infos = {}
for fs, fs_dict in vspace.items():
    file_data = bytearray()
    
    for chunk_id, chunk in sorted(fs_dict.items(), key=lambda x: int(x[0])):
        file_data += chunk

    if args.V601N_mode:
        file_genre = int.from_bytes(file_data[:2], "little")
        file_id = int.from_bytes(file_data[2:4], "little")
        if file_genre == 256 or file_genre in TYPE_DEF.values():
            file_data = file_data[4:]
        ext = detect_extension(file_data) if args.detect_extension else "bin"
        file_name = f"region_{file_genre:02d}_{file_id:02d}_{fs:05d}.{ext}"
        file_infos[fs] = {
            "file_genre": file_genre,
            "file_id": file_id,
            "file_name": file_name,
        }
    else:
        ext = detect_extension(file_data) if args.detect_extension else "bin"
        file_name = f"region_{fs:05d}.{ext}"
    
    with open(os.path.join(out_dir, file_name), "wb") as outf:
        outf.write(file_data)
    


# rename base on table file
if RENAME and args.V601N_mode:
    with open(os.path.join(out_dir, file_infos[6]["file_name"]), "rb") as inf:
        filetable = inf.read()
        
    with open(os.path.join(out_dir, file_infos[262]["file_name"]), "rb") as inf:
        filetable += inf.read()

    for i, off in enumerate(range(0, len(filetable), 0x5C)):
        tbl = filetable[off : off + 0x5C]
        if tbl == b"\x00" * 0x5C:
            break
        
        filesize = int.from_bytes(tbl[:4], "little")
        filename = sanitize_filename(
            tbl[0x13 : tbl.find(b"\x00", 0x13)].decode("cp932")
        )
        info = [
            info
            for info in file_infos.values()
            if info["file_genre"] == 256 and info["file_id"] == i
        ]
        
        
        dest_path = os.path.join(out_dir, filename)
        if os.path.isfile(dest_path):
            count = 1
            while os.path.isfile(dest_path):
                name, ext = os.path.splitext(filename)
                dest_path = os.path.join(out_dir, f"{name} ({count}){ext}")
                count += 1
        
        if len(info) > 0:
            src_path = os.path.join(out_dir, info[0]["file_name"])
            
            if os.path.getsize(src_path) != filesize:
                print(f"WARN: different filesize:{filename}, id={i}, assumed={filesize}, actual={os.path.getsize(src_path)}")
            
            os.rename(
                src_path,
                dest_path,
            )
            
        
            
    #with open(os.path.join(out_dir, file_infos[6150]["file_name"]), "rb") as inf:
    #    app_table = inf.read()
    

    for jad in sorted([f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f)) and f.endswith(".jad")]):
        id = int(jad[2 : 8])

        for ext, genre_id in TYPE_DEF.items():
            info = [
                info
                for info in file_infos.values()
                if info["file_genre"] == genre_id and info["file_id"] == id
            ]
            if len(info) > 0:
                src_path = os.path.join(out_dir, info[0]["file_name"])
                os.rename(
                    src_path,
                    os.path.join(out_dir, f"JA{id:06}.{ext}")
                )
            



print("End")
