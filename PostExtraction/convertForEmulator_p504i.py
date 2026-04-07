import re
import sys
import os
import datetime
import struct
import shutil
import fnmatch
import email.utils
import traceback


def main():
    java_directory = sys.argv[1]

    output_folder = os.path.join(os.getcwd(), "output")
    os.makedirs(output_folder, exist_ok=True)
    
    adf_dir = os.path.join(java_directory, "JAM")
    jar_dir = os.path.join(java_directory, "JAR")
    sp_dir = os.path.join(java_directory, "SCR")
    
    adf_filenames = [file for file in os.listdir(adf_dir) if file.lower().endswith(".jam")]

    for adf_filename in adf_filenames:
        print(f"\n[{adf_filename}]")
        try:
            base_name = re.sub(r"\.JAM$", "", adf_filename)
            adf_file_path = os.path.join(adf_dir, adf_filename)
            jar_file_path = os.path.join(jar_dir, f'{base_name}.JAR')
            sp_file_path = os.path.join(sp_dir, f'{base_name}.SCR')

            if not os.path.exists(jar_file_path):
                print(f"Failed: No JAR file found.")
                continue

            with open(adf_file_path, "rb") as file:
                adf_content = file.read()

            with open(jar_file_path, "rb") as file:
                jar_content = file.read()

            if os.path.exists(sp_file_path):
                with open(sp_file_path, "rb") as file:
                    sp_content = file.read()
            else:
                sp_content = bytes()
                print(f"WARM: No SP file found for {adf_file_path}")

            new_adf_content, new_sp_content, new_scr_contents, jar_name = convert(adf_content, jar_content, sp_content)

            while os.path.exists(new_jam_file_path := os.path.join(output_folder, f'{jar_name}.jam')):
                jar_name += "_"

            new_jar_file_path = os.path.join(output_folder, f'{jar_name}.jar')
            new_sp_file_path = os.path.join(output_folder, f'{jar_name}.sp')
                
            with open(new_jam_file_path, 'wb') as adf_file:
                adf_file.write(new_adf_content)

            shutil.copy(jar_file_path, new_jar_file_path)

            with open(new_sp_file_path, 'wb') as sp_file:
                sp_file.write(new_sp_content)
                
            for i, new_scr_content in enumerate(new_scr_contents):
                new_scr_file_path = os.path.join(output_folder, f'{jar_name}{i}.scr')
                with open(new_scr_file_path, 'wb') as scr_file:
                    scr_file.write(new_scr_content)

            print(f"Successfully processed!")
        except Exception as e:
            traceback.print_exc()
    print(f"\nAll done! => {output_folder}")
    

def carve_value(content, start_off):
    end_off = content.find(b"\x00", start_off)
    
    if end_off == -1:
        end_off = len(content)
    
    if start_off == end_off:
        return None
    
    return content[start_off:end_off]


def read_spsizes_from_adf(adf_content, start_offset):
    integers = []
    offset = start_offset

    while True:
        integer = struct.unpack('<I', adf_content[offset:offset + 4])[0]

        if integer == 0:
            break

        integers.append(integer)
        offset += 4

    return integers


def convert(adf_content, jar_content, sp_content):
    adf_dict = {}

    if adf_content[0x3A5:0x3A5+5] == b"http:":
        print("P504i ADF type")
        adf_dict["AppName"] = carve_value(adf_content, 0).decode("cp932")
        
        if v := carve_value(adf_content, 0x11):
            adf_dict["AppVer"] = v.decode("cp932")
        
        sp_sizes = [int.from_bytes(adf_content[0x20:0x24], "little")]
        adf_dict["AppClass"] = carve_value(adf_content, 0x24).decode("cp932")
        
        #if v := carve_value(adf_content, 0x???):
        #    adf_dict["AppParam"] = v.decode("cp932")
        
        adf_dict["PackageURL"] = carve_value(adf_content, 0x3A5).decode("cp932")
        
        if v := carve_value(adf_content, 0x4A5):
            adf_dict["ConfigurationVer"] = v.decode("cp932")
        
        adf_dict["LastModified"] = carve_value(adf_content, 0x4AE).decode("cp932")
        adf_dict["LastModified"] = email.utils.parsedate_to_datetime(adf_dict["LastModified"])
        adf_dict["LastModified"] = format_last_modified(adf_dict["LastModified"])
        
        if v := carve_value(adf_content, 0x7DD):
            adf_dict["ProfileVer"] = v.decode("cp932")
        
        if v := carve_value(adf_content, 0x7E6):
            adf_dict["TargetDevice"] = v.decode("cp932")
        
    elif adf_content[0x3E5:0x3E5+5] == b"http:":
        print("P505i ADF type")
        adf_dict["AppName"] = carve_value(adf_content, 0).decode("cp932")
        
        if v := carve_value(adf_content, 0x11):
            adf_dict["AppVer"] = v.decode("cp932")
        
        sp_sizes = read_spsizes_from_adf(adf_content, 0x24)
        adf_dict["AppClass"] = carve_value(adf_content, 0x64).decode("cp932")
        
        if v := carve_value(adf_content, 0x164):
            adf_dict["AppParam"] = v.decode("cp932")
        
        adf_dict["PackageURL"] = carve_value(adf_content, 0x3E5).decode("cp932")
        
        if v := carve_value(adf_content, 0x4E5):
            adf_dict["ConfigurationVer"] = v.decode("cp932")
        
        adf_dict["LastModified"] = carve_value(adf_content, 0x4EE).decode("cp932")
        adf_dict["LastModified"] = email.utils.parsedate_to_datetime(adf_dict["LastModified"])
        adf_dict["LastModified"] = format_last_modified(adf_dict["LastModified"])
        
        if v := carve_value(adf_content, 0x81D):
            adf_dict["ProfileVer"] = v.decode("cp932")
        
        if v := carve_value(adf_content, 0x826):
            adf_dict["TargetDevice"] = v.decode("cp932")
    else:
        raise ValueError("Unknown ADF type")
    
    if not all([adf_dict["AppName"], adf_dict["AppClass"], adf_dict["LastModified"]]):
        raise ValueError("AppName or AppClass or LastModified is missing")
        
    print(adf_dict)
    
    # create a jam
    jam_str = ""
    for key, value in adf_dict.items():
        jam_str += f"{key} = {value}\n"

    jam_str += f"AppSize = {len(jar_content)}\n"
    
    if 0 < len(sp_sizes) <= 16:
        jam_str += f"SPsize = {','.join(map(str, sp_sizes))}\n"
    else:
        print("WARM: SPsize detection failed.") ;
    
    jam_str += f"UseNetwork = http\n"
    jam_str += f"UseBrowser = launch\n"

    new_adf_content = jam_str.encode("cp932", errors="replace")
    new_sp_content = add_header_to_sp(jam_str, sp_content)
    
    new_scr_contents = []
    start = 0
    print(f"{sp_sizes=}")
    
    off = 0
    for sp_size in sp_sizes:
        new_scr_contents.append(sp_content[off:off+sp_size])
        off += sp_size

    if m := re.match(r'(?:.+?([^\r\n\/:*?"><|=]+)\.jar)+', adf_dict["PackageURL"]):
        jar_name = m[1]
    else:
        jar_name = ""

    return (new_adf_content, new_sp_content, new_scr_contents, jar_name)


def add_header_to_sp(jam_str, sp_contents):
    def create_header_sp(sp_sizes):
        header = bytearray()
        for size in sp_sizes:
            header += size.to_bytes(4, byteorder='little')
        while len(header) < 64:
            header += bytes([255])
        return header

    if m := re.search(r'SPsize\s*=\s*([\d,]+)', jam_str):
        sp_size_str = m.group(1)
        sp_sizes = [int(size) for size in sp_size_str.split(',')]
        header = create_header_sp(sp_sizes)
    else:
        header = create_header_sp([0])

    return header + sp_contents

def format_last_modified(last_modified_dt):
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    weekday_name = weekdays[last_modified_dt.weekday()]
    month_name = months[last_modified_dt.month - 1]

    last_modified_str = last_modified_dt.strftime(f"{weekday_name}, %d {month_name} %Y %H:%M:%S")
    return last_modified_str

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} java_directory")
    else:
        main()
