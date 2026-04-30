#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# - common funcs for performance verif and cal
# - eventually could wrap around pyvisa resource to better handle logging some/all commands passthru

from statistics import median, stdev

# check errors in STB and print
def k26_get_errors(res):
    n = res.query_ascii_values('print(errorqueue.count)')[0]
    if n == 0: return
    while n > 0:
        res.write('ec, em = errorqueue.next()')
        ec = res.query_ascii_values('print(ec)')[0]
        em = res.query('print(em)').strip()
        print(f'**** K26 ERROR {ec}: {em}')
        n = n -1
    stb = int(res.query_ascii_values('*STB?')[0])
    # bit 2 (0x04) is 'EAV - Error Available'; should now be clear unless some other errors appeared
    # while retrieving the first set. Bad news
    if (stb & 4):
        print(f'**** errors still present !')
        quit()

#shorthand
def k26_read_v(pyvisa_res, chan):
    return pyvisa_res.query_ascii_values(f'print(smu{chan}.measure.v())')[0]

def k26_read_i(pyvisa_res, chan):
    return pyvisa_res.query_ascii_values(f'print(smu{chan}.measure.i())')[0]

# given an arbitrary func that returns one val, take readings and compute stats
class read_multi():
    def __init__(self, readfunc, discard, keep, logfunc, prefix=''):
        raw = []
        for i in range(0, discard + keep):
            raw.append(readfunc())
        filtered = raw[-keep:]
        self.median=median(filtered)
        self.stdev=stdev(filtered)
        self.raw_rdg = raw
        self.filtered = filtered
        logfunc(f'[{prefix}] discarded {discard}, kept {keep} readings; median={self.median:.7g} stdev={self.stdev:.7g} raw={raw}')

