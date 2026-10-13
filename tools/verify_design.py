"""Check the board against its own schematic, against the SolderDemon 6502 host and against the PLD.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/verify_design.py

  - every pad carries the net design.json and the KiCad netlist (schematic.xml) give it;
  - J1 is the host's J5: same footprint and side, every pad at the same offset from the outline's
    corner (BACKPLANE.md of the busboard), the card drawn a quarter turn round, bus edge down, and on every pin the signal the
    host drives (bus-reference.xml, the host schematic's netlist);
  - U3's fuse map, for all 65536 addresses: a write selects exactly $C100-$C1FF (RDY enabled in
    PHI2 high, CAPTURE low), a read or reset selects nothing, and RDY is never driven while PHI2
    is low unless the sequencer has started;
  - the YM2149 strapping: BC1 grounded, BC2 = A0, chip address /A9 = 0, A8 = 1, SEL high, and
    nothing but U3 and the bus on RDY.
"""
from pathlib import Path
import json, sys, xml.etree.ElementTree as ET
import pcbnew as p

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
import gal

HOST = root.parent / 'solderdemon_6502' / 'design' / 'kicad' / 'solderdemon_6502.kicad_pcb'
SAME = {'VCC': '+5V', 'CLK': 'PHI2', 'R/~{W}': 'RWB', 'IRQ': '~{IRQ}', 'NMI': '~{NMI}'}   # host net -> card net
parts = json.loads((root / 'design.json').read_text())
b = p.LoadBoard(str(root / 'solderdemon_6502-audio.kicad_pcb'))


def netlist(path, ref=None):
    out = {}
    for net in ET.parse(path).findall('./nets/net'):
        for node in net.findall('node'):
            if ref is None or node.attrib['ref'] == ref:
                out[(node.attrib['ref'], node.attrib['pin'])] = net.attrib['name']
    return out


sch = netlist(root / 'schematic.xml')
errors = []
for part in parts:
    if part['ref'].startswith('#'): continue
    fp = b.FindFootprintByReference(part['ref'])
    for pin, net in part['nets'].items():
        pads = [a for a in fp.Pads() if a.GetNumber() == pin]
        assert pads, (part['ref'], pin)
        for a in pads:
            actual = a.GetNetname().removeprefix('/')
            if net and actual != net: errors.append(('PCB', part['ref'], pin, net, actual))
            if not net and actual and not actual.startswith('unconnected-'): errors.append(('NC', part['ref'], pin, actual))
        actual = sch.get((part['ref'], pin), '').removeprefix('/')
        if net and actual != net: errors.append(('SCH', part['ref'], pin, net, actual))


def corner(board):
    outline = p.SHAPE_POLY_SET()
    assert board.GetBoardPolygonOutlines(outline, False)
    box = outline.BBox()
    return box.GetX(), box.GetY()


# J1 against the host's J5, pad by pad, from each outline's top-left corner. The card's files show
# it a quarter turn anticlockwise from the host's (bus edge down), so a host offset (x, y) is the
# card's (y, h - x), h being the card's height in this view.
host = p.LoadBoard(str(HOST))
f, hf = b.FindFootprintByReference('J1'), host.FindFootprintByReference('J5')
assert f.GetFPID().GetLibItemName() == hf.GetFPID().GetLibItemName(), (f.GetFPIDAsString(), hf.GetFPIDAsString())
assert f.IsFlipped() == hf.IsFlipped() == False and (f.GetOrientationDegrees() - hf.GetOrientationDegrees()) % 360 == 90
outline = p.SHAPE_POLY_SET(); assert b.GetBoardPolygonOutlines(outline, False)
h = outline.BBox().GetHeight()
(cx, cy), (hx, hy) = corner(b), corner(host)
hpads = {a.GetNumber(): a for a in hf.Pads()}
for a in f.Pads():
    hp = hpads[a.GetNumber()]
    ox, oy = hp.GetPosition().x - hx, hp.GetPosition().y - hy
    assert (a.GetPosition().x - cx, a.GetPosition().y - cy) == (oy, h - ox), a.GetNumber()
pin1 = next(a for a in f.Pads() if a.GetNumber() == '1').GetPosition()
assert (p.ToMM(pin1.x - cx), p.ToMM(cy + h - pin1.y)) == (14.425, 5.775)
bus = netlist(root / 'bus-reference.xml', 'J5')
for a in f.Pads():
    want = SAME.get(bus[('J5', a.GetNumber())], bus[('J5', a.GetNumber())])
    got = a.GetNetname().removeprefix('/')
    if got.startswith('unconnected-'):
        got = got.split('-')[2] if got.count('-') >= 3 else got   # unconnected-(J1-<pin name>-PadN)
    if got != want: errors.append(('BUS', a.GetNumber(), want, a.GetNetname()))

# U3 from its fuses: every address, write and read, in and out of reset, PHI2 high and low.
src = (root / 'code/pld/audio_sequencer.pld').read_text()
name = gal.pins(src); pin = {n: k for k, n in name.items()}
g = gal.Gal22V10(gal.jedec((root / 'code/pld/audio_sequencer.jed').read_text()))
selected = []
for addr in range(0, 0x10000, 0x100):                     # A0-A7 do not reach U3
    for rw in (0, 1):
        for reset in (1, 0):
            for phi2 in (1, 0):
                lv = {pin[f'A{i}']: addr >> i & 1 for i in range(8, 16)}
                lv.update({pin['RW']: rw, pin['RESET']: reset, pin['PHI2']: phi2})
                g.q = {k: 0 for k in g.q}                    # sequencer idle (AR holds it so between writes)
                lv[pin['NREQ']] = 1
                for _ in range(3):                           # NREQ and EN feed back through their pins
                    out = g.outputs(lv)
                    lv.update({pin['NREQ']: out[pin['NREQ']], pin['EN']: out[pin['EN']]})
                req = out[pin['NREQ']] == 0
                rdy_on = out[pin['RDY']] is not None
                if req != (out[pin['CAPTURE']] == 0) and phi2: errors.append(('CAPTURE', hex(addr), rw, reset))
                if rdy_on != (req and phi2): errors.append(('RDY enable', hex(addr), rw, reset, phi2))
                if rdy_on and out[pin['RDY']] != 0: errors.append(('RDY idle level', hex(addr)))
                if out[pin['BDIR']]: errors.append(('BDIR idle', hex(addr)))
                if req and phi2: selected.append((addr, rw, reset))
assert [a for a, rw, reset in selected] == [0xC100] and selected[0][1:] == (0, 1), selected

# YM2149 strapping and the shared RDY line.
ym = next(x for x in parts if x['ref'] == 'U1')['nets']
assert (ym['29'], ym['28'], ym['24'], ym['25'], ym['26']) == ('GND', 'A0', 'GND', '+5V', '+5V'), ym
rdy = sorted(k for k, n in sch.items() if n.removeprefix('/') == 'RDY')
assert rdy == [('J1', '12'), ('U3', '23')], rdy
assert not errors, errors
result = ('PASS: schematic/PCB/manifest connectivity; J1 = host J5 (36 pads, offsets and signals); '
          'U3 fuse map over 65536 addresses x R/W x reset x PHI2 (selects $C100-$C1FF writes only, RDY only in '
          'PHI2 high until the sequencer runs); YM2149 strapping; RDY only on U3 and J1.\n')
(root / 'verification.txt').write_text(result)
print(result)
