#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# **** usage:
# - copy/edit .conf file for connection settings and cal resistor values
# - customize dmm.py as required for DMM to be used
# - dry-run to go through entire cal without saving to eeprom (easily reverted with reset())
# - can run individual 'steps' and test points

# **** code structure
# - cal steps described in ref manual section 16, are implemented in functions 'step2' to 'step7';
# - part of step 3 (current ranges) is split since it requires different wiring and code,
#   and the numbering of steps no longer matches the refman.
# - A bit of confusion between 'steps' i.e. ref manual steps, and individual test points per range
# - main() near the end initializes stuff and includes step1
# - general config is held in external adj.conf to ideally avoid having to edit this script at all

# TODO :
# -debug mode with alt settings for dwell, etc in .conf
# -sanity check wiring at start of each step (i.e. set 1V read 1V +- 10% etc)
# -unify config naming of ipulse_ton etc vs config_dwell

import pyvisa
import argparse
from dataclasses import dataclass
import datetime as dt
import logging
import sys
from time import sleep
from k26_common import *
from magiconfig import magiconfig

#XXX print wrapper, todo move to common code
#tee to both stdout and logfile for everything
def logprint(*args, **kwargs):
    logf.info(*args, **kwargs)

def open_k26(resman):
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = pyvisa.constants.ControlFlow[cfg.dut.flow]
    idstring = k26_res.query('*idn?')
    if '2602' not in idstring:
        logprint("ID query mismatch")
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
    config_dwell: str = None   #if set, will query .conf for given string and use its value. Only for I stuff

class k2602_points():
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
            calstep(100e-3, 1e-10, 90e-3, 'SENSE_LOCAL', config_dwell='dly_100mA'),
            calstep(1, 1e-10, 900e-3, 'SENSE_LOCAL', config_dwell='dly_1A'),
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
    logf.info(f'\n\t step2 {setpoint}')
    dmm.range_v(setpoint / 100) # range down two decades to capture zero point
    k26.write(f'smux.source.levelv = {zval}')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
# docs not super clear, but in SENSE_CALA we should: - not smu.measure.v() , and not call smux.measure.calibratev()
    k26r = lambda: k26_read_v(k26, chan)
    if calstep.sourceonly:
        smu_z = 0
    else:
        smu_z = read_multi(k26r, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'smu').median
    dmm_z = read_multi(dmm.read_v, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    dmm.range_v(setpoint)
    k26.write(f'smux.source.levelv = {setpoint}')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(cfg.cal.step_dwell)
    if calstep.sourceonly:
        smu_fs = setpoint
    else:
        smu_fs = read_multi(k26r, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'smu').median
    dmm_fs = read_multi(dmm.read_v, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    logf.info(f'V cal step : range={vrange} smu_z={smu_z} dmm_z={dmm_z} smu_fs={smu_fs} dmm_fs={dmm_fs}')
    k26.write(f'smux.source.calibratev({vrange}, {zval}, {dmm_z}, {setpoint}, {dmm_fs})')
    if not calstep.sourceonly:
        k26.write(f'smux.measure.calibratev({vrange}, {smu_z}, {dmm_z}, {smu_fs}, {dmm_fs})')
    k26_get_errors(k26)
    return

def step2(k26, dmm, chan, point=None):
    logprint('\n******** STEP 2 (voltage) . Verify connections:')
    logprint('*** DMM_LO -> SL, and DMM_LO -> L')
    logprint('*** DMM_HI -> SH, and DMM_HI -> H')
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    input("-------- press Enter when ready ---------")
    k26.write(f'smux.source.func = smux.OUTPUT_DCVOLTS')
    dmm.config_v()
    points = k2602_points.vcalsteps
    if point in range(0, len(points)):
        points = [points[point]]
    for calpoint in points:
        logprint(f'V cal point: {calpoint}')
        k26.write(f'smux.source.rangev = {calpoint.range}')
        k26.write(f'smux.sense = smux.{calpoint.sensemode}')
        k26.write(f'smux.cal.polarity = smux.CAL_POSITIVE')
        step2_do_one(k26, dmm, chan, calpoint, 1)
        k26.write(f'smux.cal.polarity = smux.CAL_NEGATIVE')
        step2_do_one(k26, dmm, chan, calpoint, -1)
    logprint('***** step 2 (voltage ranges) done ****')
    k26.write(f'smux.cal.polarity = smux.CAL_AUTO')
    return

# step3 : current <= 1A
def step3_do_one(k26, dmm, chan, calstep, sign):
    if sign > 0:
        irange = calstep.range
        zval = calstep.zval
        setpoint = calstep.setpoint
    else:
        irange = -calstep.range
        zval = -calstep.zval
        setpoint = -calstep.setpoint
    logf.info(f'\n\t step3 {setpoint}')
    dmm.range_i(setpoint / 100) # range down two decades to capture zero point
    k26.write(f'smux.source.leveli = {zval}')
    if calstep.config_dwell:
        dwell = getattr(cfg.cal, calstep.config_dwell)
    else:
        dwell = cfg.cal.step_dwell
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(dwell)
# TODO : not clear what can / needs to be skipped on CALA steps, docs unclear
    k26r = lambda: k26_read_i(k26, chan)
    smu_z = read_multi(k26r, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'smu').median
    dmm_z = read_multi(dmm.read_i, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    dmm.range_i(setpoint)
    k26.write(f'smux.source.leveli = {setpoint}')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(dwell)
    smu_fs = read_multi(k26r, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'smu').median
    dmm_fs = read_multi(dmm.read_i, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    logf.info(f'I cal step : range={irange} smu_z={smu_z} dmm_z={dmm_z} smu_fs={smu_fs} dmm_fs={dmm_fs}')
    k26.write(f'smux.source.calibratei({irange}, {zval}, {dmm_z}, {setpoint}, {dmm_fs})')
    if not calstep.sourceonly:
        k26.write(f'smux.measure.calibratei({irange}, {smu_z}, {dmm_z}, {smu_fs}, {dmm_fs})')
    k26_get_errors(k26)
    return

def step3(k26, dmm, chan, point=None):
    logprint('\n******** STEP 3 (current <= 1A) . Verify connections:')
    logprint('*** DMM_LO -> L')
    logprint('*** DMM_AMPS -> H')
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    input("-------- press Enter when ready ---------")
    k26.write(f'smux.source.func = smux.OUTPUT_DCAMPS')
    dmm.config_i()
    points = k2602_points.icalsteps
    if point in range(0, len(points)):
        points = [points[point]]
    for calpoint in points:
        logprint(f'I cal point: {calpoint}')
        k26.write(f'smux.source.rangei = {calpoint.range}')
        k26.write(f'smux.sense = smux.{calpoint.sensemode}')
        k26.write(f'smux.cal.polarity = smux.CAL_POSITIVE')
        step3_do_one(k26, dmm, chan, calpoint, 1)
        k26.write(f'smux.cal.polarity = smux.CAL_NEGATIVE')
        step3_do_one(k26, dmm, chan, calpoint, -1)
    logprint('***** step 3 (low current ranges) done ****')
    k26.write(f'smux.cal.polarity = smux.CAL_AUTO')
    return

# similar to step3 but uses external shunt ! SMU pulse mode could be difficult to use while
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
    logf.info(f'\n\t step4 {setpoint}')
    dmm.range_v(setpoint * cfg.cal.r5_actual / 100) # range down two decades to capture zero point
    k26.write(f'smux.source.leveli = {zval}')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(cfg.cal.ipulse_ton)
    k26r = lambda: k26_read_i(k26, chan)
    smu_z = read_multi(k26r, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'smu').median
    dmm_z_raw = read_multi(dmm.read_v, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    dmm.range_v(setpoint * cfg.cal.r5_actual)
    k26.write(f'smux.source.leveli = {setpoint}')
    k26.write(f'smux.source.output = smux.OUTPUT_ON')
    sleep(cfg.cal.ipulse_ton)
    smu_fs = read_multi(k26r, cfg.cal.discard_i, cfg.cal.keep_i, logf.debug, 'smu').median
    dmm_fs_raw = read_multi(dmm.read_v, cfg.cal.discard_v, cfg.cal.keep_v, logf.debug, 'dmm').median
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    logprint("post pulse cooldown...")
    sleep(cfg.cal.ipulse_toff)
    dmm_z = dmm_z_raw / cfg.cal.r5_actual
    dmm_fs = dmm_fs_raw / cfg.cal.r5_actual
    logf.info(f'I cal step (dmm raw zero={dmm_z_raw}, fs={dmm_fs_raw}): ' + calcmd)
    k26.write(f'smux.source.calibratei({irange}, {zval}, {dmm_z}, {setpoint}, {dmm_fs})')
    k26.write(f'smux.measure.calibratei({irange}, {smu_z}, {dmm_z}, {smu_fs}, {dmm_fs})')
    k26_get_errors(k26)
    return

def step4(k26, dmm, chan, point=None):
    logprint('\n******** STEP 4 (current > 1A) . Verify connections (fig 16-3):')
    logprint('*** DMM_LO -> 0R5 sense_L')
    logprint('*** DMM_HI -> 0R5 sense_H')
    logprint('*** SMU_L -> 0R5 L')
    logprint('*** SMU_H -> 0R5 H')
    k26.write(f'smux.source.output = smux.OUTPUT_OFF')
    input("-------- press Enter when ready ---------")
    dmm.config_v()
    k26.write(f'smux.source.func = smux.OUTPUT_DCAMPS')
    k26_write(f'smux.source.limitv = 6')
    points = k2602_points.icalsteps_hi
    if point in range(0, len(points)):
        points = [points[point]]
    for calpoint in points:
        logprint(f'I cal point: {calpoint}')
        k26.write(f'smux.source.rangei = {calpoint.range}')
        k26.write(f'smux.sense = smux.{calpoint.sensemode}')
        k26.write(f'smux.cal.polarity = smux.CAL_POSITIVE')
        step4_do_one(k26, dmm, chan, calpoint, 1)
        k26.write(f'smux.cal.polarity = smux.CAL_NEGATIVE')
        step4_do_one(k26, dmm, chan, calpoint, -1)
    logprint('***** step 3 (hi current ranges) done ****')
    k26.write(f'smux.cal.polarity = smux.CAL_AUTO')
    return

def step5(k26, dmm, chan, point=None):
    logprint('\n******** STEP 5 (contact 0R) . Verify connections (fig 16-4):')
    logprint('*** no DMM; short L -> SL, and H -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r0_hi, r0_lo = smux.contact.r()')
    logprint('\n******** STEP 5B (contact 50R) . Verify connections (fig 16-5):')
    logprint('*** L -> 50R_l -> SL')
    logprint('*** H -> 50R_h -> SH')
    input("-------- press Enter when ready ---------")
    sleep(cfg.cal.step_dwell)
    k26.write('r50_hi, r50_lo = smux.contact.r()')
    k26.write(f'smux.contact.calibratelo(r0_lo, {cfg.cal.r0_actual}, r50_lo, {cfg.cal.r50_l})')
    k26.write(f'smux.contact.calibratehi(r0_hi, {cfg.cal.r0_actual}, r50_hi, {cfg.cal.r50_h})')

def step6(k26, dmm, chan, point=None):
    today = dt.date.today()
    k26.write(f'smux.cal.date = os.time(year={today.year}, month={today.month}, day={today.day})')
    k26.write(f'smux.cal.due = os.time(year={today.year+1}, month={today.month}, day={today.day})')
    k26.write(f'smux.cal.save()')
    k26.write(f'smux.cal.lock()')

# gather calsteps together, except step6 that is meaningless on its own
calsteps = [None, None, step2, step3, step4, step5]
calsteps_descr = [None,
#                  '1: init/prep (mandatory)', #not sure how to convey that it can neither be skipped or run on its own
                  None,
                  '2: Voltage',
                  '3: I(low)',
                  '4: I(high)',
                  '5: Contact resistance',
#                  '6: save cal', #same, can't run just this one
                  ]
calsteps_helpstring = 'List of steps:'
for i in calsteps_descr:
    if i:
        calsteps_helpstring += (f'\n\t{i}')


def main():
    parser = argparse.ArgumentParser(description="####### K 2600 adjustment (calibration) #######",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog= calsteps_helpstring)
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-x', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-s', '--step', type=int, help='run specific step (see list below)')
    parser.add_argument('-p', '--point', type=int, help='run only one cal point (use with -s)')
    parser.add_argument('-n', action='store_true', help='dry run, will not save cal')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-r', action='store_true', help='restore SMU cal state to default')
    parser.add_argument('-l', '--log', default='cal_tmp.log', help='output log file')
    args = parser.parse_args(sys.argv[1:])

    global cfg
    cfg = magiconfig(args.cfg)

    chan = args.chan
    if (chan != 'a') and (chan != 'b'):
        logprint("bad channel, must be a or b")
        exit()

    point = args.point
    if (point and not args.step):
        logprint('cannot specify single point without step !')
        exit()

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
    dryrun = args.n

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

    ## start cal process
    logf.info(f'start cal on {dt.datetime.now().isoformat()}, SMU chan {chan}')
    logf.info(f'Using following parameters for cal:')
    cfg.print_configtree(logf)

    if dryrun:
        logf.info('***************** dry run ! will not save cal ! ****************** ')
    logprint('\n******** STEP 1 (prep)')
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    logprint('\nClearing any pending errors :')
    k26_get_errors(k26)
    uptime = round(k26.query_ascii_values('print(os.clock())')[0]/60)
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime} min.')
    if uptime < (2 * 60):
        logprint('******* WARNING **********')
        logprint(f'******* uptime ({uptime} minutes) below minimum recommended 2h **********')

    k26.write(f'smux = smu{chan}')  #convenient alias
    k26.write(f'smux.cal.unlock("KI0026XX")')

    if args.r:
        k26.write(f'smu{chan}.cal.restore()')
        # " with no args : restores smuX.CALSET_DEFAULT The normal calibration set.
        k26.write('smux.reset()')
        k26.write('abort') #GTL
        return

    k26.write(f'smux.reset()')
    k26.write(f'display.screen = display.SMU{chan.upper()}')

    if args.step in range(2, 6):
        steps = [args.step]
        logprint(f'Running only step {steps}')
    else:
        steps = range(2,6)

    for s in steps:
        calsteps[s](k26, dmm, chan, point)
        k26_get_errors(k26)

    if dryrun:
        logprint('\n******** STEP 6 - skipped (dry run); not saving cal ********')
    else:
        logprint('\n******** STEP 6')
        ans = input("-------- Save to EEPROM? y/Y to confirm, anything else cancels: ")
        if ans == 'y' or ans == 'Y':
            step6(k26, chan)

    k26.write('abort') #return to local

if __name__ == '__main__':
    main()
