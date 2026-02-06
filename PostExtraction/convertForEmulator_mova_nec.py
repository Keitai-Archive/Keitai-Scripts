import re
import sys
import os
import struct
import shutil
import email.utils
import traceback
from enum import Enum, auto
import argparse
import copy

class SpType(Enum):
    SINGLE = auto()
    MULTI = auto()

CONFIGS = {
    "N504iS": {
        "device_name": "N504iS",
        "draw_area": "160x180",
        "sp_type": SpType.SINGLE,
        "start_spsize": 0x5C,
        "start_adf": 0x6C,
    },
    "N505iS": {
        "device_name": "N505iS",
        "draw_area": "240x240",
        "sp_type": SpType.MULTI,
        "start_spsize": 0x8C,
        "start_adf": 0xD4,
    },
    "N506i": {
        "device_name": "N506i",
        "draw_area": "240x270",
        "sp_type": SpType.MULTI,
        "start_spsize": 0x8C,
        "start_adf": 0xD4,
    },
    "N506iS": {
        "device_name": "N506iS",
        "draw_area": "240x270",
        "sp_type": SpType.MULTI,
        "start_spsize": 0x8C,
        "start_adf": 0xD4,
    },
}

def main(model_config, input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    region_files = [file for file in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, file)) and file.startswith("region_")]
    region_files.sort()

    app_file_sets = []

    app_file_set = {
        "jar": None,
        "sp": None,
        "adf": None
    }

    for region_file in region_files:
        with open(os.path.join(input_dir, region_file), "rb") as inf:
            region_data = inf.read()

        # order: JAR [SP] ADF
        if app_file_set["jar"] is None and region_data[:4] == b"PK\x03\x04":
            app_file_set["jar"] = region_file
        elif app_file_set["jar"] is not None and app_file_set["sp"] is None:
            try:
                (adf_dict, _, _) = perse_adf(region_data, model_config["start_adf"], model_config["draw_area"], model_config["device_name"])

                if not all(key in adf_dict for key in ["AppName", "PackageURL", "AppClass", "LastModified"]):
                    raise Exception("Missing required value.")
                
                app_file_set["adf"] = region_file
            except Exception as e:
                app_file_set["sp"] = region_file
                #print(region_file, e)
        elif app_file_set["jar"] is not None and app_file_set["sp"] is not None:
            app_file_set["adf"] = region_file
        
        if app_file_set["adf"] is not None:
            app_file_sets.append(copy.deepcopy(app_file_set))

            app_file_set = {
                "jar": None,
                "sp": None,
                "adf": None
            }

    for app_file_set in app_file_sets:
        jar_filename = app_file_set["jar"]
        sp_filename = app_file_set.get("sp")
        adf_filename = app_file_set["adf"]

        print(f"\n[{jar_filename}]")
        print(f"JAR: {jar_filename}")
        print(f"SP: {sp_filename}")
        print(f"ADF: {adf_filename}")
        
        try:
            jar_file_path = os.path.join(input_dir, jar_filename)
            adf_file_path = os.path.join(input_dir, adf_filename)

            if sp_filename is None:
                print(f"WARN: No SP file found for {jar_filename}")
                sp_data = b""
            else:
                sp_file_path = os.path.join(input_dir, sp_filename)

                with open(sp_file_path, "rb") as file:
                    sp_data = file.read()

            with open(jar_file_path, "rb") as file:
                jar_data = file.read()

            with open(adf_file_path, "rb") as file:
                adf_data = file.read()

            jar_size = len(jar_data)

            out_adf_data, out_sp_data, jar_name = convert(adf_data, sp_data, jar_size, model_config)

            i = 1
            if os.path.exists(os.path.join(output_dir, f"{jar_name}.jam")):
                while os.path.exists(os.path.join(output_dir, f"{jar_name} ({i}).jam")):
                    i += 1
                jar_name = f"{jar_name} ({i})"

            out_jam_file_path = os.path.join(output_dir, f'{jar_name}.jam')
            out_jar_file_path = os.path.join(output_dir, f'{jar_name}.jar')
            out_sp_file_path = os.path.join(output_dir, f'{jar_name}.sp')
                
            with open(out_jam_file_path, 'wb') as adf_file:
                adf_file.write(out_adf_data)

            shutil.copy(jar_file_path, out_jar_file_path)

            if sp_filename is not None:
                with open(out_sp_file_path, 'wb') as sp_file:
                    sp_file.write(out_sp_data)

            print(f"Successfully processed! => {jar_name}")
        except Exception as e:
            traceback.print_exc()
    print(f"\nAll done! => {output_dir}")


def convert(adf_data, sp_data, jar_size, model_config):
    sp_type = model_config["sp_type"]
    start_spsize = model_config["start_spsize"]
    start_adf = model_config["start_adf"]
    draw_area = model_config["draw_area"]
    device_name = model_config["device_name"]
    
    try:
        if sp_type == SpType.MULTI:
            sp_sizes = read_spsizes_from_adf(adf_data, start_spsize)
        elif sp_type == SpType.SINGLE:
            sp_sizes = [struct.unpack('<I', adf_data[start_spsize:start_spsize + 4])[0]]
            if sp_sizes[0] == 0: sp_sizes = []
        else:
            raise Exception("no sp_type input")
    except struct.error:
        print("Failed: bronken ADF file.")
        return

    (adf_dict, jam_download_url, other_items) = perse_adf(adf_data, start_adf, draw_area, device_name)
    print(f"{adf_dict=}, {jam_download_url=}, {other_items=}")

    if len(sp_sizes) != 0 and sum(sp_sizes) != len(sp_data):
        print("WARN: Mismatch between spsize and actual size.")

    # Re-format LastModified
    adf_dict["LastModified"] = email.utils.parsedate_to_datetime(adf_dict["LastModified"])
    adf_dict["LastModified"] = format_last_modified(adf_dict["LastModified"])

    # Create a jam
    jam_str = ""
    for key, value in adf_dict.items():
        jam_str += f"{key} = {value}\n"

    jam_str += f"AppSize = {jar_size}\n"
    
    if 0 < len(sp_sizes) <= 16:
        jam_str += f"SPsize = {','.join(map(str, sp_sizes))}\n"
    else:
        print("WARN: SPsize detection failed.")
    
    jam_str += f"UseNetwork = http\n"
    jam_str += f"UseBrowser = launch\n"

    new_adf_data = jam_str.encode("cp932")

    new_sp_data = add_header_to_sp(jam_str, sp_data)

    if m := re.match(r'(?:.+?([^\r\n\/:*?"><|=]+)\.jar)+', adf_dict["PackageURL"]):
        jar_name = m[1]
    else:
        jar_name = ""

    return (new_adf_data, new_sp_data, jar_name)


def read_spsizes_from_adf(adf_data, start_offset):
    integers = []
    offset = start_offset

    while True:
        integer = struct.unpack('<I', adf_data[offset:offset + 4])[0]

        if integer == 0xFFFFFFFF:
            break

        integers.append(integer)
        offset += 4

    return integers


def perse_adf(adf_data, start_adf, draw_area, device_name):
    adf_dict = {}

    # Parse adf. order: AppName [AppVer] PackageURL [ConfigurationVer] AppClass [AppParam] LastModified [TargetDevice] [ProfileVer] jar_download_url
    adf_items = filter(None, adf_data[start_adf:].split(b"\00"))
    adf_items = list(map(lambda b: b.decode("cp932", errors="replace"), adf_items))

    adf_dict["AppName"] = adf_items[0]

    if not adf_items[1].startswith("http"):
        adf_dict["AppVer"] = adf_items[1]
    else:
        adf_items.insert(1, None)

    adf_dict["PackageURL"] = adf_items[2]
    
    if adf_items[3] in ["CLDC-1.1", "CLDC-1.0"]:
        adf_dict["ConfigurationVer"] = adf_items[3]
    else:
        adf_items.insert(3, None)

    adf_dict["AppClass"] = adf_items[4]

    if not adf_items[5].startswith(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        adf_dict["AppParam"] = adf_items[5]
    else:
        adf_items.insert(5, None)

    adf_dict["LastModified"] = adf_items[6]

    other_items = []
    if len(adf_items) > 6:
        for adf_item in adf_items[7:]:
            if re.search(r"(SH|SO|F|D|N|P)\d{3}", adf_item):
                adf_dict["TargetDevice"] = adf_item
            elif adf_item.startswith(("DoJa-1.0", "DoJa-2.0", "DoJa-2.1", "DoJa-2.2", "DoJa-3.0", "DoJa-3.5", "DoJa-4.0", "DoJa-4.1", "DoJa-5.0", "DoJa-5.1")):
                adf_dict["ProfileVer"] = adf_item
            elif adf_item.startswith("http"):
                jam_download_url = adf_item
            elif adf_item.endswith(".gif"):
                adf_dict["AppIcon"] = adf_item
            elif m := re.search(r"\d{3}x\d{3}", adf_item):
                adf_dict["DrawArea"] = m.group(0)
            else:
                other_items.append(adf_item)

    if not "TargetDevice" in adf_dict:
        adf_dict["TargetDevice"] = device_name

    if not "DrawArea" in adf_dict:
        adf_dict["DrawArea"] = draw_area

    return (adf_dict, jam_download_url, other_items)


def add_header_to_sp(jam_str, sp_datas):
    def create_header_sp(sp_sizes):
        header = bytearray()
        for size in sp_sizes:
            header += size.to_bytes(4, byteorder='little')
        while len(header) < 64:
            header += bytes([255])
        return header

    sp_size_match = re.search(r'SPsize\s*=\s*([\d,]+)', jam_str)
    if sp_size_match:
        sp_size_str = sp_size_match.group(1)
        sp_sizes = [int(size) for size in sp_size_str.split(',')]
        header = create_header_sp(sp_sizes)
    else:
        header = create_header_sp([0])

    return header + sp_datas


def format_last_modified(last_modified_dt):
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    weekday_name = weekdays[last_modified_dt.weekday()]
    month_name = months[last_modified_dt.month - 1]

    last_modified_str = last_modified_dt.strftime(f"{weekday_name}, %d {month_name} %Y %H:%M:%S")
    return last_modified_str


if __name__ == "__main__":
    parser = argparse.ArgumentParser("N504i type ADF converter for idkdoja")
    parser.add_argument("input")
    parser.add_argument("model", choices=CONFIGS.keys())
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    output = args.output or os.path.join(os.path.dirname(args.input), "java_output")
    main(CONFIGS[args.model], args.input, output)
