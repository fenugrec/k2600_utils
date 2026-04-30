#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# **** usage:
# - copy/edit .conf file for connection settings and cal resistor values
# - customize dmm.py as required for DMM to be used
# - dry-run to go through entire cal without saving to eeprom

# **** code structure
# - cal steps described in ref manual section 16, are implemented in functions 'step2' to 'step7';
# - part of step 3 (current ranges) is split since it requires different wiring and code,
#   and the numbering of steps no longer matches the refman.
# - main() near the end initializes stuff and includes step1
# - general config is held in external cal.conf to ideally avoid having to edit this script at all

# TODO :
# -tweak logger to output to file as well as print
# -check errors with *STB?
# -can probably replace 'smu{chan}' with 'smux' and then assign (in lua) smux=smua or smub 
# -separate dwell times for 100mA + 1A too
# -ability to run just one step / one range

import pyvisa
import argparse
import ast
import configparser
from dataclasses import dataclass
import datetime as dt
import logging
import sys
from time import sleep
from dmm import *

# some config class magic, https://alexandra-zaharia.github.io/posts/python-configuration-and-dataclasses/
# modified to use ast.literal_eval() to ~safely convert strings to numeric types when applicable
# idea is to digest a ini-style .conf file into a class whose members can be used like 'cfg.dut.baud'
class DynamicConfig:
    def __init__(self, conf):
        if not isinstance(conf, dict):
            raise TypeError(f'dict expected, found {type(conf).__name__}')

        self._raw = conf
        for key, value in self._raw.items():
            setattr(self, key, ast.literal_eval(value))

class DynamicConfigIni:
    def __init__(self, conf):
        if not isinstance(conf, configparser.ConfigParser):
            raise TypeError(f'ConfigParser expected, found {type(conf).__name__}')

        self._raw = conf
        for key, value in self._raw.items():
            setattr(self, key, DynamicConfig(dict(value.items())))

# helper func to pretty print config tree
def log_configtree(logger, parser: configparser):
    for sec in parser.sections():
        for key in parser[sec]:
            rawval = parser[sec][key]
            logger.info(f'\t{sec}.{key}={rawval}')

## dummy pyvisa resources for offline debugging
class pyvisa_dummy():
    def __init__(self, name):
        self.name = name
        self.val = 0    # dummy val for readings etc; increment on every query
    def write(self, ws):
        logf.debug(f"{self.name}.write('{ws}')")
    def query(self, qs):
        logf.debug(f"{self.name}.query('{qs}') => {self.val}")
        self.val = self.val + 1
        return f'{self.val}'
    def read_ascii_values(self):
        logf.debug(f"{self.name}.read_ascii_values() => {self.val}")
        self.val = self.val + 1
        return [self.val]
    def query_ascii_values(self, qs):
        logf.debug(f"{self.name}.query_ascii_values('{qs}') => {self.val}")
        self.val = self.val + 1
        return [self.val]

def open_k26(resman):
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = pyvisa.constants.ControlFlow[cfg.dut.flow]
    idstring = k26_res.query('*idn?')
    if '2602' not in idstring:
        print("ID query mismatch")
        quit()
    return k26_res

######## cal points
#tweak according to 2601/02/11/12, 35/36 needs more work. Tables 16-2 etc

@dataclass
class calstep():
    range: float
    zval: float
    setpoint: float
    sensemode: string
    sourceonly:bool = False #by default, cal both Source and Measure modes.

vcalsteps = [
        calstep(100e-3, 1e-12, 90e-3, 'SENSE_LOCAL'),
        calstep(100e-3, 1e-10, 90e-3, 'SENSE_REMOTE'),
        calstep(1, 1e-10, 0.9, 'SENSE_LOCAL'),
        calstep(1, 1e-10, 0.9, 'SENSE_CALA', sourceonly=True),
        calstep(6, 1e-10, 5.4, 'SENSE_LOCAL'),
        calstep(40, 1e-10, 36, 'SENSE_LOCAL'),
        ]
icalsteps = [
        calstep(100e-9, 1e-10, 90e-9, 'SENSE_LOCAL'),
        calstep(1e-6, 1e-10, 900e-9, 'SENSE_LOCAL'),
        calstep(10e-6, 1e-10, 9e-6, 'SENSE_LOCAL'),
        calstep(100e-6, 1e-10, 90e-6, 'SENSE_LOCAL'),
        calstep(1e-3, 1e-10, 900e-6, 'SENSE_LOCAL'),
        calstep(1e-3, 1e-10, 900e-6, 'SENSE_CALA', sourceonly=True),
        calstep(10e-3, 1e-10, 9e-3, 'SENSE_LOCAL'),
        calstep(100e-3, 1e-10, 90e-3, 'SENSE_LOCAL'),
        calstep(1, 1e-10, 900e-3, 'SENSE_LOCAL'),
        ]
icalsteps_hi = [
        calstep(3, 1e-10, 2.4, 'SENSE_LOCAL'),
        calstep(10, 1e-10, 2.4, 'SENSE_LOCAL'),
        ]

#step2, do one voltage cal step; items 3b to 14 or 15b to 26 (once for each polarity)
# sign : 1 or -1
def step2_do_one(k26, dmm, chan, calstep, sign):
    if sign >= 0:
        vrange = calstep.range
        zval = calstep.zval
        setpoint = calstep.setpoint
    else:
        vrange = -calstep.range
        zval = -calstep.zval
        setpoint = -calstep.setpoint
    k26.write(f'smu{chan}.source.levelv = {zval}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
# TODO : not clear what can / needs to be skipped on CALA steps, docs unclear
    k26.write(f'z_rdg = smu{chan}.measure.v()')
    dmm_z = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.levelv = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
    k26.write(f'fs_rdg = smu{chan}.measure.v()')
    dmm_fs = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    smu_z = k26.query_ascii_values(f'print(z_rdg)')[0]
    smu_fs = k26.query_ascii_values(f'print(fs_rdg)')[0]
    calcmd = f'smu{chan}.source.calibratev({vrange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})'
    k26.write(calcmd)
    logf.info(f'V cal step : range={vrange} smu_z={smu_z} dmm_z={dmm_z} smu_fs={smu_fs} dmm_fs={dmm_fs}')
    if not calstep.sourceonly:
        k26.write(f'smu{chan}.measure.calibratev({vrange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})')
    return

def step2(k26, dmm, chan):
    print('\n******** STEP 2 (voltage) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    f'smu{chan}.cal.unlock("KI0026XX")'
    f'smu{chan}.reset()'
    f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS'
    dmm_config_v(dmm)
    for calstep in vcalsteps:
        print(f'V cal step: {calstep}')
        k26.write(f'smu{chan}.source.rangev = {calstep.range}')
        k26.write(f'smu{chan}.sense = {calstep.sensemode}')
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_POSITIVE')
        step2_do_one(k26, dmm, chan, calstep, 1)
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_NEGATIVE')
        step2_do_one(k26, dmm, chan, calstep, -1)
    print('***** step 2 (voltage ranges) done ****')
    k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_AUTO')
    return

def step3_do_one(k26, dmm, chan, calstep, sign):
    if sign > 0:
        irange = calstep.range
        zval = calstep.zval
        setpoint = calstep.setpoint
    else:
        irange = -calstep.range
        zval = -calstep.zval
        setpoint = -calstep.setpoint
    k26.write(f'smu{chan}.source.leveli = {zval}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
# TODO : not clear what can / needs to be skipped on CALA steps, docs unclear
    k26.write(f'z_rdg = smu{chan}.measure.i()')
    dmm_z = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.leveli = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
    k26.write(f'fs_rdg = smu{chan}.measure.i()')
    dmm_fs = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    smu_z = k26.query_ascii_values(f'print(z_rdg)')[0]
    smu_fs = k26.query_ascii_values(f'print(fs_rdg)')[0]
    calcmd = f'smu{chan}.source.calibratei({irange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})'
    k26.write(calcmd)
    logf.info(f'I cal step : range={irange} smu_z={smu_z} dmm_z={dmm_z} smu_fs={smu_fs} dmm_fs={dmm_fs}')
    if not calstep.sourceonly:
        k26.write(f'smu{chan}.measure.calibratei({irange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})')
    return

def step3(k26, dmm, chan):
    print('\n******** STEP 3 (current <= 1A) . Verify connections:')
    print('*** DMM_LO -> L')
    print('*** DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS'
    dmm_config_i(dmm)
    for calstep in icalsteps:
        print(f'I cal step: {calstep}')
        k26.write(f'smu{chan}.source.rangei = {calstep.range}')
        k26.write(f'smu{chan}.sense = {calstep.sensemode}')
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_POSITIVE')
        step3_do_one(k26, dmm, chan, calstep, 1)
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_NEGATIVE')
        step3_do_one(k26, dmm, chan, calstep, -1)
    print('***** step 3 (low current ranges) done ****')
    k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_AUTO')

# almost identical to step3. SMU pulse mode could be difficult to use while
# synchronizing to external DMM...
def step4_do_one(k26, dmm, chan, calstep, sign):
    if sign > 0:
        irange = calstep.range
        zval = calstep.zval
        setpoint = calstep.setpoint
    else:
        irange = -calstep.range
        zval = -calstep.zval
        setpoint = -calstep.setpoint
    k26.write(f'smu{chan}.source.leveli = {zval}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.ipulse_ton)
    k26.write(f'z_rdg = smu{chan}.measure.i()')
    dmm_z_raw = dmm_read_v(dmm) 
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.leveli = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.cal.ipulse_ton)
    k26.write(f'fs_rdg = smu{chan}.measure.i()')
    dmm_fs_raw = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.cal.ipulse_toff)
    dmm_z = dmm_z_raw / cfg.cal.r5_actual
    dmm_fs = dmm_fs_raw / cfg.cal.r5_actual
    calcmd = f'smu{chan}.source.calibratei({irange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})'
    logf.info(f'I cal step (dmm raw zero={dmm_z_raw}, fs={dmm_fs_raw}): ' + calcmd)
    k26.write(calcmd)
    return

def step4(k26, dmm, chan):
    print('\n******** STEP 3B (current > 1A) . Verify connections (fig 16-3):')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    dmm_config_v(dmm)
    for calstep in icalsteps_hi:
        print(f'I cal step: {calstep}')
        k26.write(f'smu{chan}.source.rangei = {calstep.range}')
        k26.write(f'smu{chan}.sense = {calstep.sensemode}')
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_POSITIVE')
        step4_do_one(k26, dmm, chan, calstep, 1)
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_NEGATIVE')
        step4_do_one(k26, dmm, chan, calstep, -1)
    print('***** step 3 (hi current ranges) done ****')
    k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_AUTO')

def step5(k26, dmm, chan):
    print('\n******** STEP 4 (contact 0) . Verify connections (fig 16-4):')
    print('*** no DMM; short L -> SL, and H -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r0_hi, r0_lo = smu{chan}.contact.r()')
    print('\n******** STEP 4 (contact 50R) . Verify connections (fig 16-5):')
    print('*** no DMM; L -> 50R_l -> SL, and H -> 50R_h -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r50_hi, r50_lo = smu{chan}.contact.r()')
    k26.write(f'smu{chan}.contact.calibratelo(r0_lo, {cfg.cal.r0_actual}, r50_lo, {cfg.cal.r50_l})')
    k26.write(f'smu{chan}.contact.calibratehi(r0_hi, {cfg.cal.r0_actual}, r50_hi, {cfg.cal.r50_h})')

def step6(k26, chan):
    today = dt.date.today()
    k26.write(f'smu{chan}.cal.date = os.time(year={today.year}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.due = os.time(year={today.year+1}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.save()')
    k26.write(f'smu{chan}.cal.lock()')

# gather calsteps together
calsteps = [None, None, step2, step3, step4, step5]

def main():
    parser = argparse.ArgumentParser(description="K 2600 calibration script")
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-s', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-p', '--step', type=int, help='run only step # [2..5]')
    parser.add_argument('-n', action='store_true', help='dry run, will not save cal')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', default='cal_tmp.log', help='output log file')
    args = parser.parse_args(sys.argv[1:])

    parser = configparser.ConfigParser()
    parser.read_file(args.cfg)
    global cfg
    cfg = DynamicConfigIni(parser)

    if (args.chan != 'a') and (args.chan != 'b'):
        print("bad channel, must be a or b")
        exit()

    ## setup logging, test/debug options
    global logf
    logf = logging.getLogger()
        
    logging.basicConfig(filename=args.log, filemode='w')
    global testmode
    testmode = args.t
    dryrun = args.n

    if testmode:
        dmm = pyvisa_dummy('dmm_dummy')
        k26 = pyvisa_dummy('k26_dummy')
        logf.setLevel(logging.DEBUG)
    else:
        rm = pyvisa.ResourceManager()
        k26 = open_k26(rm)
        dmm = dmm_open(rm, cfg.dmm.res)

    ## start cal process
    logf.info(f'start cal on {dt.datetime.now().isoformat()}, SMU chan {args.chan}')
    logf.info(f'Using following parameters for cal:')
    log_configtree(logf, parser)

    if dryrun:
        logf.info(' ***************** dry run ! will not save cal ! ****************** ')
    print('\n******** STEP 1 (prep)')
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    uptime = round(k26.query_ascii_values('print(os.clock())')[0]/60)
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime} min.')
    if uptime < (2 * 60):
        print('******* WARNING **********')
        print(f'******* uptime ({uptime} minutes) below minimum recommended 2h **********')

    if args.step in range(2, 6):
        steps = [args.step]
        print(f'Running only step {steps}')
    else:
        steps = range(2,6)

    for s in steps:
        calsteps[s](k26, dmm, args.chan)

    if not dryrun:
        print('\n******** STEP 6')
        ans = input("-------- Save to EEPROM? y/Y to confirm, anything else cancels: ")
        if ans == 'y' or ans == 'Y':
            step6(k26, args.chan)

if __name__ == '__main__':
    main()
