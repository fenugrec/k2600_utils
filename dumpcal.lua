-- (c) fenugrec 2026, GPLv3
-- not sure if this covers everything ?
-- to load & run : send whole block from 'loadscript' to 'endscript', then 'dc.run()', then 'dumpcal(smua)'
-- once done, to return to LOCAL : send command 'abort' or press local/exit button
-- the 'contact' constants are suspicious; I don't recognize the values in the raw eeprom dumps.


loadscript dc
-- cal points. During cal, the 90% points are used, but to retrieve the raw constant, need to request 100% value.
--vcalpoints = {90e-3, 0.9, 5.4, 36}
vcalpoints = {0.1, 1, 6, 40}
--icalpoints = {90e-9, 900e-9, 9e-6, 90e-6, 900e-6, 9e-3, 90e-3, 900e-3, 2.4, 10}
icalpoints = {100e-9, 1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1, 3, 10}
--not sure about 10A; just to force range ?

function dumpcal(smux)
	smux.reset()
	smux.source.func = smux.OUTPUT_DCVOLTS
	caldate = smux.cal.date
	print('**********************')
	print('last cal date: ' .. os.date('%x', caldate))
	print('range\tsrc_x\tsrc_y\tmeas_x\tmeas_y')
	for i,cp in pairs(vcalpoints) do
		smux.source.rangev = cp
		u,v = smux.source.getcalv(cp)
		x,y = smux.measure.getcalv(cp)
		print(string.format('%g V\t%X\t%X\t%X\t%X', cp, u,v,x,y))
		u,v = smux.source.getcalv(-cp)
		x,y = smux.measure.getcalv(-cp)
		print(string.format('%g V\t%X\t%X\t%X\t%X', cp, u,v,x,y))
	end
	print('')

	smux.source.func = smux.OUTPUT_DCAMPS
	for i,cp in pairs(icalpoints) do
		smux.source.rangei = cp
		u,v = smux.source.getcali(cp)
		x,y = smux.measure.getcali(cp)
		print(string.format('%g A\t%X\t%X\t%X\t%X', cp, u,v,x,y))
		u,v = smux.source.getcali(-cp)
		x,y = smux.measure.getcali(-cp)
		print(string.format('%g A\t%X\t%X\t%X\t%X', -cp, u,v,x,y))
		smux.source.rangei = cp
	end
	beeper.beep(0.1, 800)

	print('\ncontact:')
	x,y = smux.contact.getcallo()
	print(string.format('LO\t%X\t%X', x,y))
	x,y = smux.contact.getcalhi()
	print(string.format('HI\t%X\t%X', x,y))
end

beeper.beep(0.1, 400)

endscript

