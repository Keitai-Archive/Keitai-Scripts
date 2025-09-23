import sys
import os

layouts = [[512, 16], [1024, 32], [2048, 64], [4096, 128], [8192, 256]]

def main():
    nand_path = sys.argv[1]
    filename = os.path.splitext(os.path.basename(nand_path))[0]
    nandsize = os.path.getsize(nand_path)

    print(f"Input: {nand_path}\n")

    for i, (data_size, oob_size) in enumerate(layouts):
        print(f"{i}: Data = {data_size} Bytes, OOB = {oob_size} bytes, divisible = {nandsize % (data_size+oob_size) == 0}")
    inpn = int(input('Enter the layout number: '))

    data_size = layouts[inpn][0]
    oob_size = layouts[inpn][1]

    out_nand = os.path.join(os.path.dirname(nand_path), f"{filename}_separated_{data_size}.bin")
    out_oob = os.path.join(os.path.dirname(nand_path), f"{filename}_separated_{data_size}.oob")
    print(f"\nStarted separating {data_size}/{oob_size}")
    
    with open(nand_path, "rb") as in_nandf, open(out_nand, "wb") as out_nandf, open(out_oob, "wb") as out_oobf:
        while True:
            nand_temp = in_nandf.read(data_size)
            if not nand_temp: break

            oob_temp = in_nandf.read(oob_size)
            if not oob_temp: break

            out_nandf.write(nand_temp)
            out_oobf.write(oob_temp)

    print(f"Done")


if __name__ == "__main__":
    main()
