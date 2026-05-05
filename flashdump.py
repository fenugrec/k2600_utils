#!/usr/bin/env python

# (c) fenugrec 2026, gplv3
# for keithley 2600 SMU, should work on 2600A and possibly 2600B
# Requires pyvisa etc.

############## READ THIS ########
# - configure connection settings below
# - run with -h for syntax help
# - if required, specify start / end addresses
#
# - in my very rushed tests with a 2602A, it seemed to take offense
#  when I requested a read for the entire 32MB flash; it may be necessary to run
# this multiple times, specifying 'start' and 'len' manually
#
# - my 2602 gives me an innocuous error 'query interrupted' but still shoots all data correctly.
#  Not sure if that is due to the progress reporting method I use and pyvisa
#
# K2600 mem size : 4MB (0x400000)
# K2600A mem size : 32MB (0x200 0000)
# K2600B mem size : ???

####################################
# ###### configure here ########

res = 'GPIB0::16::INSTR'
#res = 'TCPIP::192.168.1.12::gpib0,16::INSTR'
#res = 'ASRL/dev/ttyUSB7::INSTR'
#baud and flow only for ASRL resource
baud = 115200
# flow can be none, rts_cts, xon_xoff see pyvisa.constants.ControlFlow
flow = 'none'
####################################

import pyvisa
import argparse
import sys
from time import time, sleep

# progress updater from teemu, 'based' on tqdm
class minup():
    def __init__(self, total):
        self.total = int(total)
        self.n = 0
        self.last_print_n = 0
        self.last_t = time()
    def update(self, n):
        self.n += n
        if self.n - self.last_print_n < (self.total / 100):
            return
        cur_t = time()
        dt = cur_t - self.last_t
        if dt < 1:
            return
        self.last_print_n = self.n
        self.last_t = time()
        pct = self.n/self.total * 100
        print(f'0x{self.n:X}\t/ 0x{self.total:X} ({pct:.1f}%)')
        return

def dump_section(outf, k26, start, len):
    mon = minup(len)
    k26.write(f'print(ki.memread("{start:X}", "{len:X}"))')
    rd = k26.read_bytes(len, monitoring_interface=mon)
    outf.write(rd)
    return

# sanitest, useless
def test_minup():
    mon = minup(32e3)
    a = 0
    incr=200
    while a < 32e3:
        sleep(0.1)
        mon.update(incr)
        a += incr
    return


def main():
    parser = argparse.ArgumentParser(description='*** K 2600 mem dumper, (c) fenugrec 2026 ***')
    parser.add_argument('-s', '--start', type=lambda x:int(x, 0), default=0, help='start addr (dec or hex)')
    parser.add_argument('-l', '--len', type=lambda x:int(x, 0), default=2048, help='length (dec or hex)')
    parser.add_argument('ofile', type=argparse.FileType('wb'), help='output file')
    args = parser.parse_args(sys.argv[1:])

    start = args.start
    len = args.len
    if start < 0 or len < 0:
        print('start and len must be >0')
        quit()

    rm = pyvisa.ResourceManager()
    k26 = rm.open_resource(res)
    k26.baud_rate = baud
    k26.flow_control = pyvisa.constants.ControlFlow[flow]
    idstring = k26.query('*idn?')
    print(f'connected to {idstring}\nStart dumping 0x{start:X}-0x{start + len -1:X} ({len} bytes)')
    k26.write('errorqueue.clear()')

    dump_section(args.ofile, k26, start, len)
    return

if __name__ == '__main__':
    main()

