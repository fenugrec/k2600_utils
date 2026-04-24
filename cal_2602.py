#!/usr/bin/env python

# fenugrec 2026
# for keithley 2602 SMU, easily tweaked for other 2600 series

# TODO :
# -check warmup time by reading 'uptime' 
# -check errors with *STB?
# -can probably replace 'smu{chan}' with 'smux' and then assign (in lua) smux=smua or smub 

import pyvisa
import argparse
import sys
import shutil
import datetime as dt
from dataclasses import dataclass

######## configure stuff here
step_dwell = 3  #for all V/I ranges
ipulse_ton = 100e-3  #for 3A / 10A ranges
ipulse_toff = 10 #cooldown ?
r5_actual = 0.50087 # as characterized for 3A/10A ranges
zero_actual = 0 # for contact check; not sure if needs measurement (SM just gives 0)
r50_l = 50    #50r resistor to tie between L and SL (step 4)
r50_h = 50    #50r resistor to tie between H and SH (step 4)

def open_k26(resman):
    k26_res = resman.open_resource('ASRL/dev/ttyUSB0::INSTR')
    k26_res.baud_rate = 115200
    k26_res.flow_control = VI_ASRL_FLOW_RTS_CTS
    idstring = k26_res.query('*idn?')
    if not idstring.contains('2602'):
        print("ID query mismatch")
        quit()
    return k26_res

def open_dmm(resman):
    dmm = resman.open_resource('GPIB0::3:INSTR')
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

#step2, do one cal step; items 3b to 14 or 15b to 26 (once for each polarity)
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
    sleep(step_dwell)
# TODO : not clear what can / needs to be skipped on CALA steps, docs unclear
    k26.write(f'z_rdg = smu{chan}.measure.v()')
    dmm_z = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.levelv = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(step_dwell)
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
    sleep(step_dwell)
# TODO : not clear what can / needs to be skipped on CALA steps, docs unclear
    k26.write(f'z_rdg = smu{chan}.measure.i()')
    dmm_z = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.leveli = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(step_dwell)
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
    sleep(ipulse_ton)
    k26.write(f'z_rdg = smu{chan}.measure.i()')
    dmm_z_raw = dmm_read_v(dmm) 
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    k26.write(f'smu{chan}.source.leveli = {setpoint}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(ipulse_ton)
    k26.write(f'fs_rdg = smu{chan}.measure.i()')
    dmm_fs_raw = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(ipulse_toff)
    dmm_z = dmm_z_raw / r5_actual
    dmm_fs = dmm_fs_raw / r5_actual
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
    sleep(step_dwell)
    k26.write('r0_hi, r0_lo = smu{chan}.contact.r()')
    print('******** STEP 4 (contact 50R) . Verify connections (fig 16-5):')
    print('*** no DMM; L -> 50R_l -> SL, and H -> 50R_h -> SH')
    input("-------- press Enter when ready ---------")
    sleep(step_dwell)
    k26.write('r50_hi, r50_lo = smu{chan}.contact.r()')
    k26.write(f'smu{chan}.contact.calibratelo(r0_lo, {z_actual}, {r50_lo}, {r50_l})')
    k26.write(f'smu{chan}.contact.calibratehi(r0_hi, {z_actual}, {r50_hi}, {r50_h})')

def step5(k26, chan):
    if testmode: return
    today = dt.date.today()
    k26.write(f'smu{chan}.cal.date = os.time(year={today.year}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.due = os.time(year={today.year+1}, month={today.month}, day={today.day})')
    k26.write(f'smu{chan}.cal.save()')
    k26.write(f'smu{chan}.cal.lock()')

def main():
    parser = argparse.ArgumentParser(description="K 2600 calibration script")
    parser.add_argument('-c', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-t', action='store_true', help='run in test mode, will not save cal')
    parser.add_argument('-l', '--log', type=argparse.FileType('w'), help='output log file')
    args = parser.parse_args(sys.argv[1:])

    if (args.chan != 'a') or (args.chan != 'b'):
        print("bad channel, must be a or b")
        exit()

    global logf
    logf = args.log
    global testmode
    testmode = args.t

    rm = pyvisa.ResourceManager()
    logf.write(f'start cal on {dt.datetime.now().isoformat()}, chan {args.chan}')
    if testmode:
        logf.write(' ***************** test mode ! will not save cal ! ****************** ')
    # step 1 : open stuff
    k26 = open_k26(rm)
    dmm = open_dmm(rm)

