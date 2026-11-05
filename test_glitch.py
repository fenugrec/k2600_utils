#!/usr/bin/env python

# XXX incomplete, just copypaste + partially butchered
# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# this is to troubleshoot glitch issues wher the smu is setup for :
# - force I, measure V
# - Vmeas set to Remote (4-wire)
# - Sense inputs disconnected (this is not "correct" but should still behave
# - Force outputs to dummy 4R load (rated for a few 100us of abuse at a few amps)
# - toggle channel on/off 

# **** usage:
# - copy/edit .conf file for connection settings

# **** code structure
# - main() near the end initializes stuff
# - general config is held in external pv.conf to ideally avoid having to edit this script at all

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

# XXX k26 command wrapper just for debug; combine as class wrapper ?
# ah but pyvisa also does that for us if loglevel=DEBUG
def k26_writelog(s):
#    logf.debug(f'k26 write "{s}"')
    k26.write(s)


#func to format each measurement result
def print_result_header():
    logprint(f'\n{'range':10}\t{'target':10}\t{'reading':10}\t{'delta':20}\t{'tol':10}\t{'pass':10}')

def print_result(range, tgt, rdg, delta, tol):
    delta_ppm = (delta / tgt) * 1e6
    if abs(delta) > tol:
        result = '* FAIL *'
    else:
        result = 'OK'
    logprint(f'{range:8g}\t{tgt:10.7g}\t{rdg:10.7g}\t'
        + f'{delta:10.7g} ({delta_ppm:.4g} ppm)\t{tol:10.7g}\t{result}')

def open_k26(resman):
    k26_res = resman.open_resource(cfg.dut.res)
    k26_res.baud_rate = cfg.dut.baud
    k26_res.flow_control = pyvisa.constants.ControlFlow[cfg.dut.flow]
    idstring = k26_res.query('*idn?')
    logprint(f'k26 connected: {idstring}')
    if '2602' not in idstring:
        logprint("ID query mismatch")
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
    isource_points = [
            pvstep(100e-9, 90e-9, 154e-12),
            pvstep(1e-6, 900e-9, 870e-12),
            pvstep(10e-6, 9e-6, 4.7e-9),
            pvstep(100e-6, 90e-6, 57e-9),
            pvstep(1e-3, 900e-6, 470e-9),
            pvstep(10e-3, 9e-3, 5.7e-6),
            pvstep(100e-3, 90e-3, 47e-6, config_dwell='dly_100mA'),
            pvstep(1, 900e-3, 1.35e-3, config_dwell='dly_1A'),
            ]

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
    k26_writelog(f'smux.source.leveli = {target}')
    if pvstep.config_dwell:
        dwell = getattr(cfg.pv, pvstep.config_dwell)
    else:
        dwell = cfg.pv.step_dwell
    k26_writelog(f'smux.source.output = smux.OUTPUT_ON')
    sleep(dwell)
    dmm_rdg = read_multi(dmm.read_i, cfg.pv.discard_i, cfg.pv.keep_i, logf.debug, 'dmm').median
    k26_writelog(f'smux.source.output = smux.OUTPUT_OFF')
    delta = dmm_rdg - target
    print_result(irange, target, dmm_rdg, delta, pvstep.tol)
    k26_get_errors(k26)
    return

#step4: low I source test; p. 15-9
def step4(k26, dmm, chan, point=None):
    logprint('\n******** STEP 4 (I source <= 1A). Verify connections:')
    logprint('*** DMM_LO -> L')
    logprint('*** DMM_AMPS -> H')
    input("-------- press Enter when ready ---------")
    logf.debug('\n STEP 4')
    #XXX test to simulate running prev step
    k26_writelog(f'smux.source.func = smux.OUTPUT_DCVOLTS')
    k26_writelog(f'smux.sense = smux.SENSE_REMOTE')
    k26_writelog(f'smux.source.rangev = 36')
    k26_writelog(f'smux.source.output = smux.OUTPUT_OFF')
    k26_get_errors(k26)
    #k26_writelog(f'smux.sense = smux.SENSE_LOCAL')
    #XXX end of test
    k26_writelog(f'smux.source.func = smux.OUTPUT_DCAMPS')
    k26_writelog(f'smux.source.limitv = 10')
    k26_writelog(f'smux.source.leveli = 0')
    k26_writelog(f'smux.source.output = smux.OUTPUT_ON')
    dmm.config_i()
    print_result_header()
    points = k2602_limits.isource_points
    if point in range(0, len(points)):
        points = [points[point]]
    for pvstep in points:
        k26_writelog(f'smux.source.rangei = {pvstep.range}')
        step4_do_one(k26, dmm, chan, pvstep, 1)
        step4_do_one(k26, dmm, chan, pvstep, -1)
    return

def main():
    parser = argparse.ArgumentParser(description="####### K 2600 performance verif ####### ", 
                                    formatter_class=argparse.RawDescriptionHelpFormatter, epilog= calsteps_helpstring)
    parser.add_argument('-c', '--cfg', type=argparse.FileType('r'), required=True, help='config file')
    parser.add_argument('-x', '--chan', required=True, help='select channel [a|b]')
    parser.add_argument('-s', '--step', type=int, help='run specific step (see list below)', )
    parser.add_argument('-p', '--point', type=int, help='run only one cal point (use with -s)')
    parser.add_argument('-t', action='store_true', help='test mode (dev)')
    parser.add_argument('-l', '--log', default='pv_tmp.log', help='output log file')
    args = parser.parse_args(sys.argv[1:])

    global cfg
    cfg = magiconfig(args.cfg)

    if (args.chan != 'a') and (args.chan != 'b'):
        logprint("bad channel, must be a or b")
        exit()
    chan = args.chan

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

    global k26
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
    logf.info(f'start PV on {dt.datetime.now().isoformat()}, SMU chan {chan}')
    logf.info(f'Using following parameters for PV:')
    cfg.print_configtree(logf)

    logprint('\n******** STEP 1 (prep)')
    k26_model = k26.query('print(localnode.model)')
    k26_sn = k26.query('print(localnode.serialno)')
    k26_rev = k26.query('print(localnode.revision)')
    print('\nClearing any pending errors :')
    k26_get_errors(k26)
    uptime = round(k26.query_ascii_values('print(os.clock())')[0]/60)
    logf.info(f'connected to model {k26_model}, sn # {k26_sn}, rev {k26_rev}; uptime {uptime} min.')
    k26.write(f'smux = smu{chan}')  #convenient alias

    if uptime < (2 * 60):
        logprint('******* WARNING **********')
        logprint(f'******* uptime ({uptime} minutes) below minimum recommended 2h **********')
    # should be ~ equivalent to Menu->Save Setup->Recall->Factory
    k26_writelog('reset()')
    k26_writelog(f'smux.reset()')
    chan_u=chan.capitalize()
    k26_writelog(f'display.screen = display.SMU{chan_u}')

    if args.step in range(2, 8):
        steps = [args.step]
        logprint(f'Running only step {steps}')
    else:
        steps = range(2,8)

    for s in steps:
        calsteps[s](k26, dmm, chan, point)

    logf.info(f'\n*********** DONE *********')
    k26_writelog('display.screen = display.SMUA_SMUB')
    k26_writelog('abort') #return to local


if __name__ == '__main__':
    main()
