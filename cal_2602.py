#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# **** usage:
# - create/edit .conf file for connection settings and cal resistor values
# - customize dmm_* functions here as required
# - dry-run to go through entire cal without saving to eeprom

# **** code structure
# - cal steps described in ref manual section 16, are implemented in functions 'step2' to 'step4';
# - part of RM's step 3 (current ranges) is split to a step3b func since it requires different wiring and code
# - main() near the end initializes stuff and includes step1
# - dmm functions need to be customized to provide V and I readings, see dmm_read_v() and dmm_read_i()
# - config is held in external cal.conf to avoid having to edit this script too much

# TODO :
# -move dmm_* funcs to external file ?
# -check errors with *STB?
# -can probably replace 'smu{chan}' with 'smux' and then assign (in lua) smux=smua or smub 

#import pyvisa
import ast
import argparse
import sys
from time import sleep
import datetime as dt
from dataclasses import dataclass
import configparser

# some config class magic, https://alexandra-zaharia.github.io/posts/python-configuration-and-dataclasses/
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

## dummy pyvisa resources for offline testing
class pyvisa_dummy():
    def __init__(self, name):
        self.name = name
        self.val = 0    # dummy val for readings etc; increment on every query
    def write(self, ws):
        logf.write(f'{self.name}.write("{ws}")')
    def query(self, qs):
        logf.write(f'{self.name}.query("{qs}") => {self.val}')
        self.val = self.val + 1
        return f'{self.val}'
    def query_ascii_values(self, qs):
        logf.write(f'{self.name}.query_ascii_values("{qs}") => {self.val}')
        self.val = self.val + 1
        return [self.val]

def open_k26(resman):
    if testmode:
        return pyvisa_dummy('k26_dummy')
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = cfg.dut.flow
    idstring = k26_res.query('*idn?')
    if not idstring.contains('2602'):
        print("ID query mismatch")
        quit()
    return k26_res

def open_dmm(resman):
    if testmode:
        return pyvisa_dummy('dmm_dummy')
    dmm = resman.open_resource(cfg.dmm.res)
    ids = dmm.query('*idn?')
    if not ids:
        print("no DMM ?")
        quit()
    print(f"connected to DMM:\n{ids}")
    return dmm

# set whatever necessary to measure volts
def dmm_config_v(dmm):
    return

# set whatever necessary to measure up to 1A
def dmm_config_i(dmm):
    return

def dmm_read_v(dmm):
    return dmm.query_ascii_values(':whatsyourvoltage?')

def dmm_read_i(dmm):
    return dmm.query_ascii_values(':whatsyouramps?')

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
    calcmd = f'smu{chan}.source.calibratev({vrange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})'
    k26.write(calcmd)
    logf.write('V cal step : ', calcmd)
    if not sourceonly:
        k26.write(f'smu{chan}.measure.calibratev({vrange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})')
    return

def step2(k26, dmm, chan):
    print('******** STEP 2 (voltage) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    f'smu{chan}.cal.unlock("KI0026XX")'
    f'smu{chan}.reset()'
    f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS'
    dmm_config_v(dmm)
    for calstep in vcalsteps:
        print(f'V cal range {calstep.range}, setpoint {calstep.setpoint}')
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
    calcmd = f'smu{chan}.source.calibratei({irange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})'
    k26.write(calcmd)
    logf.write('I cal step :', calcmd)
    if not sourceonly:
        k26.write(f'smu{chan}.measure.calibratei({irange}, z_rdg, {dmm_z}, fs_rdg, {dmm_fs})')
    return

def step3(k26, dmm, chan):
    print('******** STEP 3 (current <= 1A) . Verify connections:')
    print('*** DMM_LO -> L')
    print('*** DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS'
    dmm_config_i(dmm)
    for calstep in icalsteps:
        print(f'I cal range {calstep.range}, setpoint {calstep.setpoint}')
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
def step3b_do_one(k26, dmm, chan, calstep, sign):
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
    logf.write(f'I cal step (dmm raw zero={dmm_z_raw}, fs={dmm_fs_raw}):', calcmd)
    k26.write(calcmd)
    return

def step3b(k26, dmm, chan):
    print('******** STEP 3B (current > 1A) . Verify connections (fig 16-3):')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    dmm_config_v(dmm)
    for calstep in icalsteps_hi:
        print(f'I cal range {calstep.range}, setpoint {calstep.setpoint}')
        k26.write(f'smu{chan}.source.rangei = {calstep.range}')
        k26.write(f'smu{chan}.sense = {calstep.sensemode}')
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_POSITIVE')
        step3_do_one(k26, dmm, chan, calstep, 1)
        k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_NEGATIVE')
        step3_do_one(k26, dmm, chan, calstep, -1)
    print('***** step 3 (hi current ranges) done ****')
    k26.write(f'smu{chan}.cal.polarity = smu{chan}.CAL_AUTO')

def step4(k26, chan):
    print('******** STEP 4 (contact 0) . Verify connections (fig 16-4):')
    print('*** no DMM; short L -> SL, and H -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r0_hi, r0_lo = smu{chan}.contact.r()')
    print('******** STEP 4 (contact 50R) . Verify connections (fig 16-5):')
    print('*** no DMM; L -> 50R_l -> SL, and H -> 50R_h -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r50_hi, r50_lo = smu{chan}.contact.r()')
    k26.write(f'smu{chan}.contact.calibratelo(r0_lo, {cfg.cal.r0_actual}, {r50_lo}, {cfg.cal.r50_l})')
    k26.write(f'smu{chan}.contact.calibratehi(r0_hi, {cfg.cal.r0_actual}, {r50_hi}, {cfg.cal.r50_h})')

def step5(k26, chan):
    if dryrun: return
    today = dt.date.today()
    k26.write(f'smu{chan}.cal.date = os.time(year={today.year}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.due = os.time(year={today.year+1}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.save()')
    k26.write(f'smu{chan}.cal.lock()')

def main():
    parser = argparse.ArgumentParser(description="K 2600 calibration script")
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-s', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-n', action='store_true', help='dry run, will not save cal')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', type=argparse.FileType('wa'), help='output log file')
    args = parser.parse_args(sys.argv[1:])

    parser = configparser.ConfigParser()
    parser.read_file(args.cfg)
    global cfg
    cfg = DynamicConfigIni(parser)

    if (args.chan != 'a') and (args.chan != 'b'):
        print("bad channel, must be a or b")
        exit()

    global logf
    logf = args.log
    if logf is None:
        logf=open('cal_tmp.log', 'w')
    global testmode
    testmode = args.t
    global dryrun
    dryrun = args.n

    if testmode:
        rm = None
    else:
        rm = pyvisa.ResourceManager()
    logf.write(f'start cal on {dt.datetime.now().isoformat()}, SMU chan {args.chan}')
    logf.write(f'Using following parameters for cal:\n{cfg}')

    if dryrun:
        logf.write(' ***************** dry run ! will not save cal ! ****************** ')
    print('******** STEP 1 (prep)')
    k26 = open_k26(rm)
    dmm = open_dmm(rm)
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    uptime = k26.query_ascii_values('print(os.clock())')[0]
    logf.write(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime}')
    if uptime < (2 * 3600):
        print('******* WARNING **********')
        print(f'******* uptime ({uptime/60} minutes) below minimum recommended 2h **********')
    step2(k26, dmm, args.chan)
    step3(k26, dmm, args.chan)
    step3b(k26, dmm, args.chan)
    step4(k26, args.chan)
    step5(k26, args.chan)

if __name__ == '__main__':
    main()
