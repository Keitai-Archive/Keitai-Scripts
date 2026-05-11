import os
import sys
import re

# header
#   magic (3byte, KJX)
#   header length (1byte)
#   kjx name length (1byte)
#   kjx name string 
#   jad length(2byte, big)
#   jad name length (1byte)
#   jad name string
# jad content
# jar content


def main(dump_content, output_dir):
    print("Start")
    
    offset = -1
    while True:
        offset = dump_content.find(b"KJX", offset+1)
        if offset == -1: break
        
        temp_kjx = dump_content[offset:offset+1000*1000*15]
        kjx_name_len = temp_kjx[4]
        
        try:
            kjx_name = temp_kjx[5:5+kjx_name_len].decode("cp932")
            kjx_name = re.sub(r"\.kjx$", "", kjx_name)
            print(f"\nFound: '{kjx_name}.kjx' (start: {hex(offset)}, ", end="")
        except Exception as e:
            #print(e)
            continue
        
        header_len = temp_kjx[3]
        header_content = temp_kjx[0:header_len]
        
        jad_len = int.from_bytes(temp_kjx[5+kjx_name_len:5+kjx_name_len+2], "big")
        jad_content = temp_kjx[header_len:header_len+jad_len]
        
        try:
            jad_str = jad_content.decode("utf-8")
        except:
            try:
                jad_str = jad_content.decode("cp932")
            except:
                print(f"Failed: jad decoding")
                continue
        
        print("AppName: " + re.search(r"MIDlet-Name: ([^\r\n]+)", jad_str)[1] + ")")
        
        try:
            jar_len = int(re.search(r"MIDlet-Jar-Size: (\d+)", jad_str)[1])
        except:
            print(f"Failed: no 'MIDlet-Jar-Size'")
            continue
        
        jar_content = temp_kjx[header_len+jad_len:header_len+jad_len+jar_len]
        if jar_content[0:2] != b"PK":
            print(f"Failed: no jar")
            continue
        
        os.makedirs(output_dir, exist_ok=True)
        
        with open(os.path.join(output_dir, f"{kjx_name}.kjx"), "wb") as kjx:
            kjx.write(header_content + jad_content + jar_content)
            
        with open(os.path.join(output_dir, f"{kjx_name}.jad"), "wb") as jad:
            jad.write(jad_content)
            
        with open(os.path.join(output_dir, f"{kjx_name}.jar"), "wb") as jar:
            jar.write(jar_content)
        
        print(f"Succeeded")
    
    if os.path.isdir(output_dir):
        print(f"\noutput -> {output_dir}")
    else:
        print("\nno kjx")




if __name__ == "__main__":
    output_dir = sys.argv[1] + "_kjxs"
    
    with open(sys.argv[1], "rb") as file:
        main(file.read(), output_dir)
