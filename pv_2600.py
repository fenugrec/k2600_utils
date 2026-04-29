#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series
# This script implements the Performance Verification steps described in ref manual section 15

# **** usage:
# - copy/edit .conf file for connection settings and cal resistor values
# - customize dmm_* functions here as required

# **** code structure
# - main() near the end initializes stuff
# - dmm functions need to be customized to provide V and I readings, see dmm_read_v(), dmm_config_v() etc
# - general config is held in external pv.conf to ideally avoid having to edit this script at all

# TODO :
# see cal_2600.py

#import pyvisa
import argparse
import ast
import configparser
from dataclasses import dataclass
import datetime as dt
import logging
import sys
from time import sleep

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
    def query_ascii_values(self, qs):
        logf.debug(f"{self.name}.query_ascii_values('{qs}') => {self.val}")
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
    return dmm.query_ascii_values(':whatsyourvoltage?')[0]

def dmm_read_i(dmm):
    return dmm.query_ascii_values(':whatsyouramps?')[0]

######## verification points
#tweak according to 2601/02/11/12, 35/36 needs more work. Tables 16-2 etc

@dataclass
class pvstep():
    range: float
    target: float
    tol: float # such that 'acceptable range = [(target - tol) ... (target + tol)]

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
            pvstep(100e-3, 90e-3, 47e-6),
            pvstep(1, 900e-3, 1.35e-6),
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
            pvstep(100e-3, 90e-3, 38e-6),
            pvstep(1, 900e-3, 1.77e-3),
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
    k26.write(f'smu{chan}.source.levelv = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    report=f'output V step: range={vrange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step2 : output volt accu test; p. 15-6
def step2(k26, dmm, chan):
    print('\n******** STEP 2 (output voltage) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 2')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS')
    k26.write(f'smu{chan}.sense = smu{chan}.SENSE_REMOTE')
    dmm_config_v(dmm)
    for pvstep in k2602_limits.vsource_points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step2_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
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
    k26.write(f'smu{chan}.source.levelv = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = dmm_read_v(dmm)
    delta = dmm_rdg - target
    logf.info(f'V meas initial: range={vrange} tgt={target} rdg={dmm_rdg} delta={delta}')
    # adjust SMU once, should be 'close enough'
    k26.write(f'smu{chan}.source.levelv = {target - delta}')
    sleep(cfg.pv.step_dwell)
    smu_rdg = k26.query_ascii_values(f'print(smu{chan}.measure.v())')[0]
    dmm_rdg = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - smu_rdg
    logf.info(f'V meas final: range={vrange} dmm={dmm_rdg} smu={smu_rdg} delta={delta}')
    report=f'V meas step: range={vrange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step3 : volt meas accu test; p. 15-8
def step3(k26, dmm, chan):
    print('\n******** STEP 3 (voltage meas. accu) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 3')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCVOLTS')
    k26.write(f'smu{chan}.sense = smu{chan}.SENSE_REMOTE')
    dmm_config_v(dmm)
    for pvstep in k2602_limits.vmeas_points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step3_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
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
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    report=f'I source step: range={irange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step4: low I source test; p. 15-9
def step4(k26, dmm, chan):
    print('\n******** STEP 4 (I source <= 1A). Verify connections:')
    print('*** DMM_LO -> L')
    print('*** DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 4')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm_config_i(dmm)
    for pvstep in k2602_limits.isource_points:
        k26.write(f'smu{chan}.source.rangei = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step4_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
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
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.step_dwell)
    dmm_rdg = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - target
    logf.info(f'I meas initial: range={irange} tgt={target} rdg={dmm_rdg} delta={delta}')
    # adjust SMU once, should be 'close enough'
    k26.write(f'smu{chan}.source.leveli = {target - delta}')
    sleep(cfg.pv.step_dwell)
    smu_rdg = k26.query_ascii_values(f'print(smu{chan}.measure.i())')[0]
    dmm_rdg = dmm_read_i(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    delta = dmm_rdg - smu_rdg
    logf.info(f'I meas final: range={irange} dmm={dmm_rdg} smu={smu_rdg} delta={delta}')
    report=f'I meas step: range={irange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step5 : low I meas accu test; p. 15-14
def step5(k26, dmm, chan):
    print('\n******** STEP 5 (I meas <= 1A) . Verify connections:')
    print('*** DMM_LO -> SL, and DMM_LO -> L')
    print('*** DMM_HI -> SH, and DMM_HI -> H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 5')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm_config_v(dmm)
    for pvstep in k2602_limits.imeas_points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step5_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
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
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.ipulse_ton)
    dmm_raw = dmm_read_v(dmm)
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.pv.ipulse_toff)
    dmm_rdg = dmm_raw / cfg.pv.r5_actual
    delta = dmm_rdg - target
    report=f'I source step: range={irange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step6: high I source test; p. 15-9
def step6(k26, dmm, chan):
    print('\n******** STEP 6 (I source > 1A). Verify connections:')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 6')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm_config_v(dmm)
    for pvstep in k2602_limits.isource_hi_points:
        k26.write(f'smu{chan}.source.rangei = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step6_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
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
    k26.write(f'smu{chan}.source.leveli = {target}')
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_ON')
    sleep(cfg.pv.ipulse_ton)
    dmm_raw = dmm_read_v(dmm)
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
    dmm_raw = dmm_read_v(dmm)
    smu_rdg = k26.query_ascii_values(f'print(smu{chan}.measure.i())')[0]
    k26.write(f'smu{chan}.source.output = smu{chan}.OUTPUT_OFF')
    print("post pulse cooldown...")
    sleep(cfg.pv.ipulse_toff)
    dmm_rdg = dmm_raw / cfg.pv.r5_actual
    delta = dmm_rdg - smu_rdg
    logf.info(f'I meas final: range={irange} vsense={dmm_raw} i_calc={dmm_rdg} smu={smu_rdg} delta={delta}')
    report=f'I meas step: range={irange} tgt={target} rdg={dmm_rdg} delta={delta} tol={pvstep.tol}'
    if abs(delta) > pvstep.tol:
        report = report + '\t*** OUT OF SPEC ***'
    print(report)
    return

#step7 : high I meas accu test; p. 15-14
def step7(k26, dmm, chan):
    print('\n******** STEP 7 (I meas > 1A) . Verify connections (fig 15-3):')
    print('*** DMM_LO -> 0R5 sense_L')
    print('*** DMM_HI -> 0R5 sense_H')
    print('*** SMU_L -> 0R5 L')
    print('*** SMU_H -> 0R5 H')
    input("-------- press Enter when ready ---------")
    logf.info('\n STEP 7')
    k26.write(f'smu{chan}.source.func = smu{chan}.OUTPUT_DCAMPS')
    dmm_config_v(dmm)
    for pvstep in k2602_limits.imeas_hi_points:
        k26.write(f'smu{chan}.source.rangev = {pvstep.range}')
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_POSITIVE')
        step7_do_one(k26, dmm, chan, pvstep, 1)
        k26.write(f'smu{chan}.pv.polarity = smu{chan}.pv_NEGATIVE')
        step7_do_one(k26, dmm, chan, pvstep, -1)
    return

def main():
    parser = argparse.ArgumentParser(description="K 2600 performance verif")
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-s', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', default='pv_tmp.log', help='output log file')
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

    if testmode:
        rm = None
        logf.setLevel(logging.DEBUG)
    else:
        rm = pyvisa.ResourceManager()

    ## start cal process
    logf.info(f'start PV on {dt.datetime.now().isoformat()}, SMU chan {args.chan}')
    logf.info(f'Using following parameters for PV:')
    log_configtree(logf, parser)

    print('\n******** STEP 1 (prep)')
    k26 = open_k26(rm)
    dmm = open_dmm(rm)
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    uptime = k26.query_ascii_values('print(os.clock())')[0]
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime}')
    if uptime < (2 * 3600):
        print('******* WARNING **********')
        print(f'******* uptime ({uptime/60} minutes) below minimum recommended 2h **********')
    # should be ~ equivalent to Menu->Save Setup->Recall->Factory
    k26.write('reset()')
    k26.write(f'smu{args.chan}.reset()')
    step2(k26, dmm, args.chan)
    step3(k26, dmm, args.chan)
    step4(k26, dmm, args.chan)
    step5(k26, dmm, args.chan)
    step6(k26, dmm, args.chan)
    step7(k26, dmm, args.chan)
    logf.info(f'\n*********** DONE *********')
    k26.write('abort') #return to local


if __name__ == '__main__':
    main()
