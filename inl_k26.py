#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# **** usage:
# - copy/edit .conf file for connection settings
# - customize dmm.py as required for DMM to be used

# **** code structure
# - main() near the end initializes stuff
# - general config is held in external .conf to ideally avoid having to edit this script at all

# TODO :
# - interlace reads for multiple dmm ?
# - better output format
# - log + print

import pyvisa
import argparse
from dataclasses import dataclass
import datetime as dt
import logging
import random
import sys
from time import sleep
from k26_common import *
from magiconfig import magiconfig

def logprint(*args, **kwargs):
    logf.info(*args, **kwargs)

def open_k26(resman):
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = pyvisa.constants.ControlFlow[cfg.dut.flow]
    idstring = k26_res.query('*idn?')
    if '2602' not in idstring:
        print("ID query mismatch")
        quit()
    return k26_res

######## points, ranges. not useful right now
#tweak according to 2601/02/11/12, 35/36

@dataclass
class specs():
    range: float
    resol: float

# for k2602, best resolution+accuracy at FS would be on 6V range; lowest relative noise on 40V
class k2602_points():
    v = [
            specs(0.1, 5e-6),
            specs(1, 50e-6),
            specs(6, 50e-6),
            specs(40, 500e-6),
            ]

######## target generator
# different strategies; pick random points, or n equally spaced, or specify interval

def gen_random(min, max, n):
    return [random.uniform(min, max) for _ in range(n)]

def gen_linear_n(min, max, n):
    i = 0
    while i < n:
        yield min + i * (max - min)/(n-1)
        i += 1

def gen_linear_step(min, max, step):
    i = 0
    while min + i * step < max:
        yield min + i * step
        i += 1

gentypes = {'random':gen_random, 'linear_n':gen_linear_n, 'linear_step':gen_linear_step}

######## produce edible csv
def print_header():
    print('setpoint\tmedian\te_ppm\tstdev')

def print_reading(setpoint, rdg):
    s = setpoint
    if s == 0: s = 1e-9
    e_ppm = (rdg.median / s - 1) * 1e6
    e_ppm = sorted((-999, e_ppm, 999))[1] #magic clamp to within +- 999ppm
    logprint(f'{setpoint:.7g}\t{rdg.median:.7g}\t{e_ppm:.5g}\t{rdg.stdev:.7g}')

######## sweep smu V and measure with DMM
def sweep(k26, dmm, vrange, points):
    k26.write(f'smux.source.func = smux.OUTPUT_DCVOLTS')
    dmm.config_v()
    dmm.range_v(vrange)
    print_header()
    k26.write(f'smux.source.rangev = {vrange}')
    k26.write(f'smux.source.levelv = 0')
    k26.write(f'smux.source.limiti = 1e-3')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    for pt in points:
        k26.write(f'smux.source.levelv = {pt:.4f}')
        sleep(cfg.inl.sweep_dwell)
        dmm_fs = read_multi(dmm.read_v, cfg.inl.discard, cfg.inl.keep, lambda *x:None)
        print_reading(pt, dmm_fs)
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    print('*********** done. Output off **************')
    return

def main():
    parser = argparse.ArgumentParser(description="K 2600 INL sweep")
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-x', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', default='inltmp.log', help='output log file')
    args = parser.parse_args(sys.argv[1:])

    global cfg
    cfg = magiconfig(args.cfg)

    chan = args.chan
    if (chan != 'a') and (chan != 'b'):
        print("bad channel, must be a or b")
        exit()

    smin = cfg.inl.sweep_min 
    smax = cfg.inl.sweep_max 
    if smin >= smax:
        print('Error : sweep min > max')
        quit()

    # magic : create the generator with given parameters
    sweep_gen = gentypes[cfg.inl.spread](smin, smax, cfg.inl.step)

    ## setup logging, test/debug options
    global logf
    logf = logging.getLogger()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    file_handler = logging.FileHandler(filename=args.log, mode='w')
    #for stdout : don't print 'info:root' prefix
    stdout_handler.setFormatter(logging.Formatter('%(message)s'))
    logging.basicConfig(handlers=[file_handler, stdout_handler])

    if '3478' in cfg.dmm.driver:
        from dmm_3478 import dmm_3478
    elif 'xdevs' in cfg.dmm.driver:
        from dmm_xdevs import dmm_xdevs

    global testmode
    testmode = args.t

    if testmode:
        dmm = dmm_3478(pyvisa_dummy('dmm_dummy'))
        k26 = pyvisa_dummy('k26_dummy')
        logf.setLevel(logging.DEBUG)
    else:
        rm = pyvisa.ResourceManager()
        k26 = open_k26(rm)
        dmm_res = rm.open_resource(cfg.dmm.res)
        dmm = dmm_3478(dmm_res)
        logf.setLevel(logging.INFO)

    k26.write(f'smux = smu{chan}')  #cleverness
    logf.info(f'start INL on {dt.datetime.now().isoformat()}, SMU chan {chan}')
    logf.info(f'Using following parameters:')
    cfg.print_configtree(logf)

    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    uptime = round(k26.query_ascii_values('print(os.clock())')[0]/60)
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime} min.')

    k26.write(f'smux.reset()')
#    print(list(sweep_gen))
    sweep(k26, dmm, max(abs(smin), abs(smax)), sweep_gen)

if __name__ == '__main__':
    main()
