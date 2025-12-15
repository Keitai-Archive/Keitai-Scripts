import re
import os
import struct
import email.utils
import traceback
from enum import Enum, auto
import argparse

class SpType(Enum):
    SINGLE = auto()
    MULTI = auto()

CONFIGS = {
    "SO505i": {
        "sp_type": SpType.MULTI,
        "draw_area": "240x240",
        "device_name": "SO505i",
        "AppSize_off": 0x30,
        "total_spsize_off": 0x34,
        "AppName_off": 0x3C,
        "PackageURL_off": 0x64,
        "ProfileVer_off": 0x168,
        "AppClass_off": 0x1BC,
        "AppParam_off": None,
        "TargetDevice_off": 0x614,
        "LastModified_off": 0x6F0,
        "jar_off": 0xF60,
        "adf2_SPsize_off": 0x20,
    },
    "SO505iS": {
        "sp_type": SpType.MULTI,
        "draw_area": "240x240",
        "device_name": "SO505iS",
        "AppSize_off": 0x30,
        "total_spsize_off": 0x34,
        "AppName_off": 0x3C,
        "PackageURL_off": 0x64,
        "ProfileVer_off": 0x168,
        "AppClass_off": 0x1BC,
        "AppParam_off": 0x2BC,
        "TargetDevice_off": 0x614,
        "LastModified_off": 0x7F0,
        "jar_off": 0x1064,
        "adf2_SPsize_off": 0x20,
    },
    "SO506i": {
        "sp_type": SpType.MULTI,
        "draw_area": "240x240",
        "device_name": "SO506i",
        "AppSize_off": 0x34,
        "total_spsize_off": 0x38,
        "AppName_off": 0x40,
        "PackageURL_off": 0x68,
        "ProfileVer_off": 0x16C,
        "AppClass_off": 0x1C0,
        "AppParam_off": 0x2C0,
        "TargetDevice_off": 0x818,
        "LastModified_off": 0x8F4,
        "jar_off": 0x11F8,
        "adf2_SPsize_off": 0x20,
    },
}

def main(model_config, input_dir, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    app_filenames = [file for file in os.listdir(input_dir) if os.path.isfile(os.path.join(input_dir, file)) and file.endswith(".APP")]
    app_filenames.sort()

    for app_filename in app_filenames:
        print(f"\n[{app_filename}]")
        try:
            base_name = re.sub(r"\.APP$", "", app_filename)

            with open(os.path.join(input_dir, app_filename), "rb") as inf:
                app_data = inf.read()

            (jam_data, jar_data, sp_data) = convert(app_data, model_config)

            if s := carve_jar_name(re.search(r"PackageURL\x20*=\x20*([^\r\n]+)", jam_data.decode("cp932"))[1]):
                base_name = s

            new_jam_file_path = os.path.join(output_dir, f'{base_name}.jam')
            new_jar_file_path = os.path.join(output_dir, f'{base_name}.jar')
            new_sp_file_path = os.path.join(output_dir, f'{base_name}.sp')
                
            with open(new_jam_file_path, 'wb') as outf:
                outf.write(jam_data)

            with open(new_jar_file_path, 'wb') as outf:
                outf.write(jar_data)

            if len(sp_data) > 0:
                with open(new_sp_file_path, 'wb') as outf:
                    outf.write(sp_data)

            print(f"Successfully processed!")
        except Exception as e:
            traceback.print_exc()
    print(f"\nAll done! => {output_dir}")


def convert(app_data, model_config):
    jar_start = model_config["jar_off"]
    if app_data[jar_start:jar_start+4] != b"PK\x03\x04":
        raise Exception("The JAR offset is incorrect.")

    jar_size = int.from_bytes(app_data[model_config["AppSize_off"] : model_config["AppSize_off"]+4], "little")
    jar_end = jar_start + jar_size

    sp_size = int.from_bytes(app_data[model_config["total_spsize_off"] : model_config["total_spsize_off"]+4], "little")
    sp_end = jar_end + sp_size

    adf2_data = app_data[sp_end:]

    sp_type = model_config["sp_type"]
    try:
        if sp_type == SpType.MULTI:
            sp_sizes = read_spsizes_from_adf(adf2_data, model_config["adf2_SPsize_off"])
        elif sp_type == SpType.SINGLE:
            sp_sizes = [struct.unpack('<I', adf2_data[model_config["adf2_SPsize_off"] : model_config["adf2_SPsize_off"] + 4])[0]]
        else:
            raise Exception("no sp_type input")
    except struct.error:
        print("Failed: bronken ADF file.")
        return

    adf_dict = perse_adf(app_data, adf2_data, model_config)

    if not "TargetDevice" in adf_dict:
        adf_dict["TargetDevice"] = model_config["device_name"]

    adf_dict["DrawArea"] = model_config["draw_area"]

    # Re-format LastModified
    adf_dict["LastModified"] = email.utils.parsedate_to_datetime(adf_dict["LastModified"])
    adf_dict["LastModified"] = format_last_modified(adf_dict["LastModified"])

    # create a jam
    jam_str = ""
    for key, value in adf_dict.items():
        jam_str += f"{key} = {value}\n"

    jam_str += f"AppSize = {jar_size}\n"
    
    if 0 < len(sp_sizes) <= 16:
        jam_str += f"SPsize = {','.join(map(str, sp_sizes))}\n"
    else:
        print("WARM: SPsize detection failed.") ;
    
    jam_str += f"UseNetwork = http\n"
    jam_str += f"UseBrowser = launch\n"

    jam_data = jam_str.encode("cp932", errors="replace")

    sp_data = app_data[jar_end:sp_end]
    sp_data = add_header_to_sp(jam_str, sp_data)

    return (jam_data, app_data[jar_start:jar_end], sp_data)


def read_spsizes_from_adf(adf_content, start_offset):
    integers = []
    offset = start_offset

    while True:
        integer = struct.unpack('<I', adf_content[offset:offset + 4])[0]

        if integer == 0xFFFFFFFF:
            break

        integers.append(integer)
        offset += 4

    return integers


def parse_value(offset, data):
    end = data.find(b"\x00", offset)
    return data[offset:end].decode("cp932")
    

def perse_adf(app_data, adf2_data, model_config):
    adf_dict = {}

    adf_dict["AppName"] = parse_value(model_config["AppName_off"], app_data)
    adf_dict["PackageURL"] = parse_value(model_config["PackageURL_off"], app_data)
    adf_dict["LastModified"] = parse_value(model_config["LastModified_off"], app_data)
    adf_dict["AppClass"] = parse_value(model_config["AppClass_off"], app_data)

    if model_config["ProfileVer_off"] is not None and app_data[model_config["ProfileVer_off"]] != 0:
        adf_dict["ProfileVer"] = parse_value(model_config["ProfileVer_off"], app_data)

    if model_config["AppParam_off"] is not None and app_data[model_config["AppParam_off"]] != 0:
        adf_dict["AppParam"] = parse_value(model_config["AppParam_off"], app_data)

    if model_config["TargetDevice_off"] is not None and app_data[model_config["TargetDevice_off"]] != 0:
        adf_dict["TargetDevice"] = parse_value(model_config["TargetDevice_off"], app_data)

    # adf2 order: download_jam_url, appname, [appver], [appparam], appclass, download_jar_url, 
    if (start := adf2_data.find(b"http:")) != -1:
        if (temp := adf2_data.find(b"\x00\x00", start)) != -1:
            end = temp
        else:
            end = len(adf2_data)

        adf_items = adf2_data[start:end].split(b"\x00")
        adf_items = list(map(lambda b: b.decode("cp932", errors="replace"), adf_items))
    else:
        raise Exception("adf2_data hasn't URL")

    if adf_dict.get("AppParam") != adf_items[2] and adf_dict.get("AppClass") != adf_items[2]:
        adf_dict["AppVer"] = adf_items[2]

    print(f"{adf_dict=}")
    return adf_dict


def add_header_to_sp(jam_str, sp_contents):
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

    return header + sp_contents


def format_last_modified(last_modified_dt):
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    weekday_name = weekdays[last_modified_dt.weekday()]
    month_name = months[last_modified_dt.month - 1]

    last_modified_str = last_modified_dt.strftime(f"{weekday_name}, %d {month_name} %Y %H:%M:%S")
    return last_modified_str


def carve_jar_name(package_url):
    if m := re.match(r'(?:.+?([^\r\n\/:*?"><|=]+)\.jar)+', package_url):
        return m[1]
    else:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser("SO505i APP converter for idkdoja")
    parser.add_argument("input")
    parser.add_argument("model", choices=CONFIGS.keys())
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()

    for key in ["ProfileVer_off", "AppParam_off", "TargetDevice_off"]:
        if CONFIGS[args.model][key] is None:
            print(f"WARN: The key '{key}' has not been defined yet in the {args.model} configuration." )

    output = args.output or os.path.join(os.path.dirname(args.input), "java_output")
    main(CONFIGS[args.model], args.input, output)
