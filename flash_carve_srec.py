#!/usr/bin/env python

# (c) fenugrec 2026 GPLv3
# take firmware update file and copy idata sections, invoking srec_cat

import argparse
import subprocess
import sys

# will be as much work to auto-parse the .X as it is to just enter a few blockdefs...

#blockdefs for 2600A v2.2.4; located at 0x2000C ?
blocks = [
	(0xa0316200, 0x2043c, 0xfc),
	(0xa0200728, 0x20538, 0x04),
	(0xa0000800, 0x2053c, 0xe4dba),
	(0xa020072c, 0x1052f6, 0x262fa),
	]


def main():
    parser = argparse.ArgumentParser(description="stuff")
    parser.add_argument('-i', required=True, help='source .X file')
    parser.add_argument('-o', '--out', required=True, help='output file')
    args = parser.parse_args(sys.argv[1:])

    print('************************ orig file structure:')
    print(args.i)
    print(args.out)
    subprocess.run([f'srec_cat {args.i} -out {args.out}'], shell=1)
    subprocess.run(['srec_info', args.out])
    for b in blocks:
        start = b[1]
        len = b[2]
        dest = b[0]
        print(f'\n** processing block; start={start:X} len={len:X} dest={dest:X}')
        subprocess.run([f'srec_cat {args.i} -exclud 0 0x{start:X} -offs - 0x{start:X} '
                        f'-crop 0 0x{len:X} -offs 0x{dest:X} -out chunk_tmp'], shell=1)
        subprocess.run([f'srec_cat {args.out} -exclud 0x{start:X} 0x{start+len:X} '
                f'chunk_tmp -out {args.out}'], shell=1)

    print('************************ new file structure:')
    subprocess.run(['srec_info', args.out])
    return

if __name__ == '__main__':
    main()

