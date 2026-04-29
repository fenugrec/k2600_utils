#!/usr/bin/env python

# fenugrec 2026, gplv3
#
# customize this for DMM to be used with cal / pv
#
# example code for hp3478a
import pyvisa

def dmm_open(resman):
    dmm = resman.open_resource(cfg.dmm.res)
#    ids = dmm.query('*idn?')
    if 0:
#    if not ids:
        print("no DMM ?")
        quit()
    else:
        print(f"connected to DMM:\n{ids}")
    return dmm

# set whatever necessary to measure volts
def dmm_config_v(dmm):
    # DCV, autorange, 5.5dig, internal trig, autozero
    dmm.write('F1RAN5T1Z1')
    return

# set whatever necessary to measure up to 1A
def dmm_config_i(dmm):
    # DCA, autorange, 5.5dig, internal trig, autozero
    dmm.write('F5RAN5T1Z1')
    return

def dmm_read_v(dmm):
    return dmm.read_ascii_values()[0]

def dmm_read_i(dmm):
    return dmm.read_ascii_values()[0]

