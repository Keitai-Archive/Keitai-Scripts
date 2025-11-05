import sys
import os
import zipfile
import re
import io
import argparse


utf8_pattern = re.compile(
    rb'(?:[\x09\x0A\x0D\x20-\x7E]|'  # ASCII
    rb'[\xC2-\xDF][\x80-\xBF]|'      # 2byte
    rb'\xE0[\xA0-\xBF][\x80-\xBF]|'  # 3byte
    rb'[\xE1-\xEC\xEE\xEF][\x80-\xBF]{2}|'  # 3byte
    rb'\xED[\x80-\x9F][\x80-\xBF]|'  # 3byte
    rb'\xF0[\x90-\xBF][\x80-\xBF]{2}|'  # 4byte
    rb'[\xF1-\xF3][\x80-\xBF]{3}|'  # 4byte
    rb'\xF4[\x80-\x8F][\x80-\xBF]{2}'  # 4byte
    rb')+'
)

def find_jad_offset(dump_data, appname_off):
    min_search = max(appname_off-0x4000, 0)
    max_search = min(appname_off+0x4000, len(dump_data))
    utf8_matches = utf8_pattern.finditer(dump_data[min_search:max_search])

    for m in utf8_matches:
        min_off = m.start() + min_search
        max_off = m.end() + min_search
        if min_off <= appname_off < max_off:
            return (min_off, max_off)
    
    raise Exception()


def extract_jar_name(url_str):
    m = re.findall(r'([^&=\\/:*?"<>|]+)\.jar', url_str)
    if m:
        ret = m[-1]
    else:
        print(f"WARN: Failed to extract the jar name ({url_str})")
        ret = "empty"
    return ret

class JarValidationError(Exception):
    """JAR file validation error"""
    pass


def verify_jar(jar_data):
    """
    Verify the validity of a JAR file
    
    Args:
        jar_data (bytes): JAR file binary data
        
    Raises:
        JarValidationError: If the JAR file is invalid
    """
    if jar_data[0:4] != b"PK\x03\x04":
        raise JarValidationError("Invalid ZIP file format (incorrect magic number)")
    
    try:
        with io.BytesIO(jar_data) as jar_stream, zipfile.ZipFile(jar_stream, "r") as f:
            corrupted_file = f.testzip()
            if corrupted_file is not None:
                raise JarValidationError(f"Corrupted file detected: {corrupted_file}")
                
    except zipfile.BadZipFile as e:
        raise JarValidationError(f"ZIP format reading error: {str(e)}") from e
    except Exception as e:
        raise JarValidationError(f"Unexpected error occurred: {str(e)}") from e


def carve_jad_and_jar(dump_data, output_dir):
    appname_off = -1
    while True:
        # JAD part
        appname_off = dump_data.find(b"MIDlet-Name:", appname_off+1)
        
        if appname_off == -1:
            break

        (jad_start, jad_end) = find_jad_offset(dump_data, appname_off)
        
        # Avoid the case where the JAR signature "PK" is included at the end.
        pkoff = dump_data.find(b"PK\x03\x04", jad_start, jad_end+2)
        if pkoff != -1 and pkoff < jad_end:
            jad_end = pkoff
        
        jad_content = dump_data[jad_start:jad_end]
        if jad_content.find(b"MIDlet-Jar-URL:") == -1 or jad_content.find(b"MIDlet-Jar-Size:") == -1:
            continue

        #print(hex(jad_start), hex(jad_end), jad_content)
        jad_str = jad_content.decode("utf-8")

        try:
            app_name = re.search(r"MIDlet-Name:\x20?([^\r\n]+)" ,jad_str)[1]
            file_name = extract_jar_name(re.search(r"MIDlet-Jar-URL:\x20?([^\r\n]+)" ,jad_str)[1])
            jar_size = int(re.search(r"MIDlet-Jar-Size:\x20?([^\r\n]+)" ,jad_str)[1])
            print(f"\nfound {file_name}.jad (appname: '{app_name}', start: {hex(jad_start)})")
        except Exception as e:
            print(f"\nJAD Parsing Error: (appname: '{app_name}', start: {hex(jad_start)}) ({e})")
            continue
        
        # JAR part
        jar_start = dump_data.find(b"PK\x03\x04", jad_end, jad_end+0x2000)
        if jar_start == -1:
            print("WAMN: jar not found.")
            continue
        jar_end = jar_start + jar_size
        print(f"found {file_name}.jar (start: {hex(jar_start)}, size: {jar_size} bytes)")
        
        jar_content = dump_data[jar_start:jar_end]

        try:
            verify_jar(jar_content)
        except JarValidationError as e:
            print(f"WARN: {str(e)}")
            file_name = file_name + " (corrupted)"

        out_name = file_name
        
        i = 1
        if os.path.exists(os.path.join(output_dir, out_name + ".jad")):
            while True:
                out_name = f"{file_name} ({i})"
                if not os.path.exists(os.path.join(output_dir,  out_name + ".jad")): break
                i += 1
        
        with open(os.path.join(output_dir, out_name + ".jad"), "wb") as jad_f:
            jad_f.write(jad_content)

        with open(os.path.join(output_dir, out_name + ".jar"), "wb") as jar_f:
            jar_f.write(dump_data[jar_start:jar_end])

        print(f"Output name: {out_name}")
        

def main():
    parser = argparse.ArgumentParser("JAD/JAR Carver", description="Extract the JAD/JAR from the firmware. It is required that the file is not chunked, the JAR follows the JAD, and the JAR size specified in the JAD is correct.")
    parser.add_argument("input")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args()
    output_dir = args.output or os.path.dirname(args.input)
    os.makedirs(output_dir, exist_ok=True)
    
    
    print("Start")
    with open(args.input, "rb") as file: 
        carve_jad_and_jar(file.read(), output_dir)
    print("\nEnd")


if __name__ == "__main__":
    main()
