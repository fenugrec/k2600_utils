#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series
# This script implements the Performance Verification steps described in ref manual section 15

# **** usage:
# - copy/edit .conf file for connection settings and cal resistor values
# - customize dmm.py as required for DMM to be used

# **** code structure
# - main() near the end initializes stuff
# - general config is held in external pv.conf to ideally avoid having to edit this script at all

# TODO :
# see cal_2600.py

import pyvisa
import argparse
import ast
import configparser
from dataclasses import dataclass
import datetime as dt
import logging
import sys
from time import sleep
from k26_common import *

#func to format each measurement result
def print_result_header():
    print(f'\n{'range':10}\t{'target':10}\t{'reading':10}\t{'delta':20}\t{'tol':10}\t{'pass':10}')

def print_result(range, tgt, rdg, delta, tol):
    delta_ppm = (delta / tgt) * 1e6
    if abs(delta) > tol:
        result = '* FAIL *'
    else:
        result = 'OK'
    print(f'{range:8g}\t{tgt:10.7g}\t{rdg:10.7g}\t'
        + f'{delta:10.7g} ({delta_ppm:.4g} ppm)\t{tol:10.7g}\t{result}')

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

def open_k26(resman):
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = pyvisa.constants.ControlFlow[cfg.dut.flow]
    idstring = k26_res.query('*idn?')
    if '2602' not in idstring:
        print("ID query mismatch")
        quit()
    return k26_res

######## verification points
#tweak according to 2601/02/11/12, 35/36 needs more work. Tables 16-2 etc

@dataclass
class pvstep():
    range: float
    target: float
    tol: float # such that 'acceptable range = [(target - tol) ... (target + tol)]
    config_dwell: str = None   #if set, will query .conf for given string and use its value. Only for I stuff

class k2602_limits():
    vsource_points = [
            pvstep(100e-3, 90e-3, 268e-6),
            pvstep(1, 0.9, 580e-6),
            pvstep(6, 5.4, 2.88e-3),
            pvstep(40, 36, 19.2e-3),
            ]
    vmeas_points = [
            pvstep(100e-3, 90e-3, 163.5e-6),
            pvstep(1, 0.9, 335e-6),
            pvstep(6, 5.4, 1.81e-3),
            pvstep(40, 36, 13.4e-3),
            ]
    isource_points = [
            pvstep(100e-9, 90e-9, 154e-12),
            pvstep(1e-6, 900e-9, 870e-12),
            pvstep(10e-6, 9e-6, 4.7e-9),
            pvstep(100e-6, 90e-6, 57e-9),
            pvstep(1e-3, 900e-6, 470e-9),
            pvstep(10e-3, 9e-3, 5.7e-6),
            pvstep(100e-3, 90e-3, 47e-6, config_dwell='dly_100mA'),
            pvstep(1, 900e-3, 1.35e-6, config_dwell='dly_1A'),
            ]
    isource_hi_points = [
            pvstep(3, 2.4, 2.94e-3),
            ]
    imeas_points = [
            pvstep(100e-9, 90e-9, 145e-12),
            pvstep(1e-6, 900e-9, 525e-12),
            pvstep(10e-6, 9e-6, 3.75e-9),
            pvstep(100e-6, 90e-6, 43e-9),
            pvstep(1e-3, 900e-6, 380e-9),
            pvstep(10e-3, 9e-3, 4.3e-6),
            pvstep(100e-3, 90e-3, 38e-6, config_dwell='dly_100mA'),
            pvstep(1, 900e-3, 1.77e-3, config_dwell='dly_1A'),
            ]
    imeas_hi_points = [
            pvstep(3, 2.4, 4.7e-3),
            ]

#step2 : test one output volt accu point; sign = +1 or -1
def step2_do_one(k26, dmm, chan, pvstep, sign):
    if sign >= 0:
        vrange = pvstep.range
        target = pvstep.target
    else:
        vrange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step2 {target}')
    dmm.range_v(target)
    k26.write(f'smu{chan}.source.levelv = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    print_result(vrange, target, dmm_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step2 : output volt accu test; p. 15-6
def step2(k26, dmm, chan, point=None):
    print('\n******** STEP 2 (output voltage) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 2')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS')
    k26.write(f'smu{chan}.sense = smu{chan}.SENSE_REMOTE')
    dmm.config_v()
    print_result_header()
    points = k2602_limits.vsource_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        step2_do_one(k26, dmm, chan, pvstep, 1)
        step2_do_one(k26, dmm, chan, pvstep, -1)
    return

#step3 : test one volt meas point; sign = +1 or -1
#a bit trickier; need to adjust SMU setpoint so that DMM shows target value
def step3_do_one(k26, dmm, chan, pvstep, sign):
    if sign >= 0:
        vrange = pvstep.range
        target = pvstep.target
    else:
        vrange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step3 {target}')
    dmm.range_v(target)
    k26.write(f'smu{chan}.source.levelv = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    delta = dmm_rdg - target
    logf.info(f'V meas initial: range={vrange} tgt={target} rdg={dmm_rdg} delta={delta}')

    # adjust SMU once, should be 'close enough'
    k26.write(f'smu{chan}.source.levelv = {target - delta}')
    sleep(cfg.pv.step_dwell)
    k26r = lambda: k26_read_v(k26, chan)
    smu_rdg = read_multi(k26r, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'smu').median
    dmm_rdg = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - smu_rdg
    logf.info(f'V meas final: range={vrange} dmm={dmm_rdg} smu={smu_rdg} delta={delta}')
    print_result(vrange, target, smu_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step3 : volt meas accu test; p. 15-8
def step3(k26, dmm, chan, point=None):
    print('\n******** STEP 3 (voltage meas. accu) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 3')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS')
    k26.write(f'smu{chan}.sense = smu{chan}.SENSE_REMOTE')
    dmm.config_v()
    print_result_header()
    points = k2602_limits.vmeas_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        step3_do_one(k26, dmm, chan, pvstep, 1)
        step3_do_one(k26, dmm, chan, pvstep, -1)
    return

#step4 : low I source, test one point; sign = +1 or -1
def step4_do_one(k26, dmm, chan, pvstep, sign):
    if sign > 0:
        irange = pvstep.range
        target = pvstep.target
    else:
        irange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step4 {target}')
    dmm.range_i(target)
    k26.write(f'smu{chan}.source.leveli = {target}')
    if pvstep.config_dwell:
        dwell = getattr(cfg.pv, pvstep.config_dwell)
    else:
        dwell = cfg.pv.step_dwell
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(dwell)
    dmm_rdg = read_multi(dmm.read_i, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    print_result(irange, target, dmm_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step4: low I source test; p. 15-9
def step4(k26, dmm, chan, point=None):
    print('\n******** STEP 4 (I source <= 1A). Verify connections:')
    print('*** DMM_LO -> L')
    print('*** DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 4')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm.config_i()
    print_result_header()
    points = k2602_limits.isource_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangei = {pvstep.range}')
        step4_do_one(k26, dmm, chan, pvstep, 1)
        step4_do_one(k26, dmm, chan, pvstep, -1)
    return

#step5 : low I meas: test one point; sign = +1 or -1
#a bit trickier; need to adjust SMU setpoint so that DMM shows target value
def step5_do_one(k26, dmm, chan, pvstep, sign):
    if sign >= 0:
        irange = pvstep.range
        target = pvstep.target
    else:
        irange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step5 {target}')
    dmm.range_i(target)
    k26.write(f'smu{chan}.source.leveli = {target}')
    if pvstep.config_dwell:
        dwell = getattr(cfg.pv, pvstep.config_dwell)
    else:
        dwell = cfg.pv.step_dwell
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    # use configurable dwell only first time ?
    sleep(dwell)
    dmm_rdg = read_multi(dmm.read_i, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    logf.info(f'I meas initial: range={irange} tgt={target} rdg={dmm_rdg} delta={delta}')

    # adjust SMU once, should be 'close enough'
    k26.write(f'smu{chan}.source.leveli = {target - delta}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    k26r = lambda: k26_read_i(k26, chan)
    smu_rdg = read_multi(k26r, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'smu').median
    dmm_rdg = read_multi(dmm.read_i, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - smu_rdg
    logf.info(f'I meas final: range={irange} dmm={dmm_rdg} smu={smu_rdg} delta={delta}')
    print_result(irange, target, smu_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step5 : low I meas accu test; p. 15-14
def step5(k26, dmm, chan, point=None):
    print('\n******** STEP 5 (I meas <= 1A) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 5')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm.config_i()
    print_result_header()
    points = k2602_limits.imeas_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        step5_do_one(k26, dmm, chan, pvstep, 1)
        step5_do_one(k26, dmm, chan, pvstep, -1)
    return

#step6 : high I source, test one point; sign = +1 or -1
def step6_do_one(k26, dmm, chan, pvstep, sign):
    if sign > 0:
        irange = pvstep.range
        target = pvstep.target
    else:
        irange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step6 {target}')
    dmm.range_v(target * cfg.pv.r5_actual)
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.ipulse_ton)
    dmm_rdg = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.pv.ipulse_toff)
    dmm_rdg = dmm_raw / cfg.pv.r5_actual
    delta = dmm_rdg - target
    print_result(irange, target, dmm_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step6: high I source test; p. 15-9
def step6(k26, dmm, chan, point=None):
    print('\n******** STEP 6 (I source > 1A). Verify connections:')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 6')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm.config_v()
    print_result_header()
    points = k2602_limits.isource_hi_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangei = {pvstep.range}')
        step6_do_one(k26, dmm, chan, pvstep, 1)
        step6_do_one(k26, dmm, chan, pvstep, -1)
    return

#step7 : high I meas: test one point; sign = +1 or -1
#a bit trickier; need to adjust SMU setpoint so that DMM shows target value
def step7_do_one(k26, dmm, chan, pvstep, sign):
    if sign >= 0:
        irange = pvstep.range
        target = pvstep.target
    else:
        irange = -pvstep.range
        target = -pvstep.target
    logf.debug(f'\n\t step7 {target}')
    dmm.range_v(target * cfg.pv.r5_actual)
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.ipulse_ton)
    dmm_raw = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.pv.ipulse_toff)
    dmm_rdg = dmm_raw / cfg.pv.r5_actual
    delta = dmm_rdg - target
    logf.info(f'I meas initial: range={irange} tgt={target} vsense={dmm_raw} i_calc={dmm_rdg} delta={delta}')
    # adjust SMU once, should be 'close enough'
    k26.write(f'smu{chan}.source.leveli = {target - delta}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.ipulse_ton)
    dmm_raw = read_multi(dmm.read_v, cfg.pv.discard_v, cfg.pv.keep_v, logf.debug, 'dmm').median
    k26r = lambda: k26_read_i(k26, chan)
    smu_rdg = read_multi(k26r, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'smu').median
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.pv.ipulse_toff)
    dmm_rdg = dmm_raw / cfg.pv.r5_actual
    delta = dmm_rdg - smu_rdg
    logf.info(f'I meas final: range={irange} vsense={dmm_raw} i_calc={dmm_rdg} smu={smu_rdg} delta={delta}')
    print_result(irange, target, smu_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step7 : high I meas accu test; p. 15-14
def step7(k26, dmm, chan, point=None):
    print('\n******** STEP 7 (I meas > 1A) . Verify connections (fig 15-3):')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 7')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm.config_v()
    print_result_header()
    points = k2602_limits.imeas_hi_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        step7_do_one(k26, dmm, chan, pvstep, 1)
        step7_do_one(k26, dmm, chan, pvstep, -1)
    return

# gather calsteps together
calsteps = [None, None, step2, step3, step4, step5, step6, step7]

def main():
    parser = argparse.ArgumentParser(description="K 2600 performance verif")
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-x', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-s', '--step', type=int, help='run only step # [2..7]')
    parser.add_argument('-p', '--point', type=int, help='run only one cal point (use with -s)')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', default='pv_tmp.log', help='output log file')
    args = parser.parse_args(sys.argv[1:])

    parser = configparser.ConfigParser()
    parser.optionxform = lambda option: option  # hax to make config case-sensitive instead of force-lowercase
    parser.read_file(args.cfg)
    global cfg
    cfg = DynamicConfigIni(parser)

    if (args.chan != 'a') and (args.chan != 'b'):
        print("bad channel, must be a or b")
        exit()

    point = args.point
    if (point and not args.step):
        print('cannot specify single point without step !')
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

    # a bit confusing where 'dmm' is a class with stuff like dmm.read_v(), 
    # but k26 is a pyvisa resource that has .write(), .read_ascii_values() etc
    if testmode:
        dmm = dmm_3478(pyvisa_dummy('dmm_dummy'))
        k26 = pyvisa_dummy('k26_dummy')
        logf.setLevel(logging.DEBUG)
    else:
        rm = pyvisa.ResourceManager()
        dmm_res = rm.open_resource(cfg.dmm.res)
        k26 = open_k26(rm)
        dmm = dmm_3478(dmm_res)
        logf.setLevel(logging.INFO)

    ## start cal process
    logf.info(f'start PV on {dt.datetime.now().isoformat()}, SMU chan {args.chan}')
    logf.info(f'Using following parameters for PV:')
    log_configtree(logf, parser)

    print('\n******** STEP 1 (prep)')
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    uptime = round(k26.query_ascii_values('print(os.clock())')[0]/60)
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime} min.')
    if uptime < (2 * 60):
        print('******* WARNING **********')
        print(f'******* uptime ({uptime} minutes) below minimum recommended 2h **********')
    # should be ~ equivalent to Menu->Save Setup->Recall->Factory
    k26.write('reset()')
    k26.write(f'smu{args.chan}.reset()')

    if args.step in range(2, 8):
        steps = [args.step]
        print(f'Running only step {steps}')
    else:
        steps = range(2,8)

    for s in steps:
        calsteps[s](k26, dmm, args.chan, point)

    logf.info(f'\n*********** DONE *********')
    k26.write('abort') #return to local


if __name__ == '__main__':
    main()
