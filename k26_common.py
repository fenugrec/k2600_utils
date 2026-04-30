#!/usr/bin/env python

# fenugrec 2026, gplv3
# for keithley 2602 SMU, should be easily tweakable for other 2600 series

# - common funcs for performance verif and cal
# - eventually could wrap around pyvisa resource to better handle logging some/all commands passthru

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
    if (stb & 4):
        print(f'**** errors still present !')
        quit()
