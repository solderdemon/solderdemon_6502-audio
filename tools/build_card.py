"""SolderDemon 6502 Audio r1: schematic + PCB placement generator. Run with KiCad 10 python.

YM2149F sound card for the SolderDemon 6502, built the way the SolderDemon m68k Audio card is:
the PSG sits behind a data latch and a GAL sequencer that times its strobes in hardware, and the
CPU just stores two bytes. r1 (the rosco_6502-audio port) drove the PSG from a W65C22S VIA with
software-timed strobes and an ATF16V8 decoder; r1 drops the VIA and the 16V8:

  U3 ATF22V10C-7PX (code/firmware/pld/audio_sequencer.pld): decodes a write to $C100-$C1FF, pulls RDY low so
     the W65C02S holds the write cycle, latches the data into U2, raises BDIR for two PSG clocks,
     then drives RDY high and lets go when the CPU moves on. The 6502 RDY plays the m68k /DTACK.
  U2 74HCT273: holds the byte for the PSG while BDIR is high.
  U1 YM2149F: BC1 grounded, BC2 = A0, so $C100 latches the register number and $C101 writes it.
  X1 1.8432 MHz can: PSG clock (SEL high, no divider) and the sequencer clock.

Passive ABC stereo line out as on both earlier cards: A left, C right, B in both.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/build_card.py
"""
from pathlib import Path
import math, os, re, subprocess, sys, xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import audio_mechanics as mech
import videolib as g
import gal
import pcbnew as pcb

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / 'design'
CLI = os.environ.get('KICAD_CLI', 'C:/Program Files/KiCad/10.0/bin/kicad-cli.exe')
g.LIB = Path(os.environ.get('KICAD_SHARE', 'C:/Program Files/KiCad/10.0/share/kicad'))
NAME = 'solderdemon_6502-audio'
LIBNAME = 'SolderDemonAudio'
PAGE = 0xC100           # decoded in code/firmware/pld/audio_sequencer.pld
REV = mech.REV
DATE = mech.DATE
W, H = mech.HEIGHT, mech.WIDTH                      # H: the busboard's depth, so both side edges are flush with it
R_BUS = R_FAR = 4.0

d = g.Design(NAME, 'SolderDemon 6502 Audio / YM2149F, hardware-timed (ATF22V10C + 74HCT273)', REV, DATE,
             paper='A3', libname=LIBNAME, ns='solderdemon-6502-audio-r1/')
DIP = lambda n, w=7.62: f'Package_DIP:DIP-{n}_W{w:.2f}mm_Socket_LongPads'
FP_C = 'SolderDemonCaps:C_Radial_5.08mm'
FP_CP = 'Capacitor_THT:CP_Radial_D6.3mm_P2.50mm'
FP_R = 'Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal'
FP_BUS = f'{LIBNAME}:PinSocket_2x18_P2.54mm_Horizontal_EdgeMount'      # the host's J5
FP_JACK = f'{LIBNAME}:Jack_3.5mm_HOOYA_PJ-307_Horizontal_EdgeMount'
FP_LOGO = f'{LIBNAME}:SolderDemon_Logo'
FP_OSC = 'Oscillator:Oscillator_DIP-8'
FP_HDR = 'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'
LOCAL_FP = DESIGN / f'{LIBNAME}.pretty'


def named(key, m):
    """Map pins by name; every name in the symbol must be listed (None = no-connect)."""
    pins = {}
    for p in d._pins(key):
        pins[g.one(p, 'name')[1]] = (g.one(p, 'number')[1], p[1])
    assert set(m) == set(pins), (key, 'unknown', set(m) - set(pins), 'missing', set(pins) - set(m))
    for name, (num, typ) in pins.items():
        assert m[name] or typ not in ('input', 'power_in'), f'{key}: input {name} left open'
    return {num: m[name] for name, (num, typ) in pins.items()}


# ---- J1: SolderDemon 6502 expansion bus (the host's J5 pinout, 2x18) ------------------------
bus = {1: 'A0', 3: 'A1', 5: 'A2', 7: 'A3', 9: 'A4', 11: 'A5', 13: 'A6', 15: 'A7', 17: 'A8',
       19: 'A9', 21: 'A10', 23: 'A11', 25: 'A12', 27: 'A13', 29: 'A14', 31: 'A15', 33: '~{VP}', 35: '~{ML}',
       2: '+5V', 4: 'PHI2', 6: 'GND', 8: 'RWB', 10: '~{RESET}', 12: 'RDY', 14: '~{NMI}', 16: '~{IRQ}',
       18: 'SYNC', 20: 'BE', 22: 'D7', 24: 'D6', 26: 'D5', 28: 'D4', 30: 'D3', 32: 'D2', 34: 'D1', 36: 'D0'}
used = {'+5V', 'GND', 'PHI2', 'RWB', '~{RESET}', 'RDY', 'A0'} | {f'A{i}' for i in range(8, 16)} | {f'D{i}' for i in range(8)}
k = d.custom('BUS_2x18', [(i, bus[i], 'passive') for i in range(1, 37, 2)],
             [(i, bus[i], 'passive') for i in range(2, 37, 2)], FP_BUS, ref='J')
d.add('J1', k, 'SYSTEM BUS', {i: n if n in used else None for i, n in bus.items()}, (40, 90))

# ---- U3: ATF22V10C sequencer. Pin names and directions come from the GALasm source -----------
src = (ROOT / 'code' / 'firmware' / 'pld' / 'audio_sequencer.pld').read_text(encoding='utf-8')
gpins, kinds = gal.pins(src), gal.signals(src)
GNET = {'CLK': 'LOGICCLK', 'RW': 'RWB', 'RESET': '~{RESET}'}
LINKED = {'RDY', 'CAPTURE', 'BDIR'}      # outputs that leave the GAL; the rest only feed back


def gal_pin(p):
    name = gpins[p]
    if name == 'VCC' or name == 'GND': return (p, name, 'power_in')
    if name == 'NC': return (p, f'NC{p}', 'no_connect')
    if name in kinds: return (p, name, 'tri_state' if kinds[name] == 'tri' else 'output')
    return (p, name, 'input')


key = d.custom('ATF22V10C_AUDIO65', [gal_pin(p) for p in range(1, 13)], [gal_pin(p) for p in range(24, 12, -1)],
               DIP(24), width=20.32)
gnets = {}
for p, name in gpins.items():
    if name == 'VCC': gnets[p] = '+5V'
    elif name == 'GND': gnets[p] = 'GND'
    elif name == 'NC' or (name in kinds and name not in LINKED): gnets[p] = None
    else: gnets[p] = GNET.get(name, name)
d.add('U3', key, 'ATF22V10C-7PX', gnets, (130, 90))

# ---- U2: data latch, cleared by reset; CAPTURE rises at PHI2 fall during a write -------------
k = d.standard('74xx', '74HCT273', DIP(20))
m = {'~{Mr}': '~{RESET}', 'Cp': 'CAPTURE', 'VCC': '+5V', 'GND': 'GND'}
m.update({f'D{i}': f'D{i}' for i in range(8)}); m.update({f'Q{i}': f'PSGD{i}' for i in range(8)})
d.add('U2', k, '74HCT273', named(k, m), (200, 90))

# ---- U1: YM2149F. SEL high (no clock divider), chip address A9 = 0 / A8 = 1, BC1 grounded -----
k = d.standard('Audio', 'YM2149', DIP(40, 15.24))
m = {'VSS': 'GND', 'VCC': '+5V', 'A': 'CH_A', 'B': 'CH_B', 'C': 'CH_C', 'CLOCK': 'PSGCLK',
     '~{RESET}': '~{RESET}', '~{A9}': 'GND', 'A8': '+5V', '~{SEL}': '+5V', 'BDIR': 'BDIR', 'BC2': 'A0',
     'BC1': 'GND', 'TEST1': None, 'NC': None}
m.update({f'DA{i}': f'PSGD{i}' for i in range(8)}); m.update({f'IO{p}{i}': None for p in 'AB' for i in range(8)})
d.add('U1', k, 'YM2149F', named(k, m), (280, 90))

# ---- X1: one 1.8432 MHz can, split through two 33R series resistors -----------------------------
k = d.standard('Oscillator', 'CXO_DIP8', FP_OSC)
d.add('X1', k, '1.8432MHz', {1: '+5V', 4: 'GND', 5: 'CLK_RAW', 8: '+5V'}, (60, 170))
R = d.standard('Device', 'R', FP_R)
d.add('R1', R, '33', {1: 'CLK_RAW', 2: 'PSGCLK'}, (100, 170))
d.add('R2', R, '33', {1: 'CLK_RAW', 2: 'LOGICCLK'}, (115, 170))
# idle levels while the GAL is blank or out of its socket
d.add('R3', R, '10k', {1: 'BDIR', 2: 'GND'}, (140, 170))
d.add('R4', R, '10k', {1: 'CAPTURE', 2: 'GND'}, (155, 170))

# ---- ABC mixer: 1k loads, A + B/2.2 left, C + B/2.2 right, AC coupled, 100k bleeders ----------
CP = d.standard('Device', 'C_Polarized', FP_CP)
for i, (ref, ch) in enumerate((('R5', 'CH_A'), ('R6', 'CH_B'), ('R7', 'CH_C'))):
    d.add(ref, R, '1k', {1: ch, 2: 'GND'}, (190 + 15 * i, 170))
for i, (ref, val, a, z) in enumerate((('R8', '10k', 'CH_A', 'MIX_L'), ('R9', '22k', 'CH_B', 'MIX_L'),
                                      ('R10', '22k', 'CH_B', 'MIX_R'), ('R11', '10k', 'CH_C', 'MIX_R'))):
    d.add(ref, R, val, {1: a, 2: z}, (250 + 15 * i, 170))
d.add('C6', CP, '10uF 16V', {1: 'MIX_L', 2: 'OUT_L'}, (320, 170))
d.add('C7', CP, '10uF 16V', {1: 'MIX_R', 2: 'OUT_R'}, (335, 170))
d.add('R12', R, '100k', {1: 'OUT_L', 2: 'GND'}, (350, 170))
d.add('R13', R, '100k', {1: 'OUT_R', 2: 'GND'}, (365, 170))
k = d.standard('Connector_Audio', 'AudioJack3_SwitchTR', FP_JACK)
d.add('J2', k, 'PJ-307 LINE OUT', {'T': 'OUT_L', 'R': 'OUT_R', 'S': 'GND', 'TN': None, 'RN': None}, (330, 210))
k = d.standard('Connector_Generic', 'Conn_01x03', FP_HDR)
d.add('J3', k, 'L / GND / R', {1: 'OUT_L', 2: 'GND', 3: 'OUT_R'}, (370, 210))

# ---- power ---------------------------------------------------------------------------------------
C = d.standard('Device', 'C', FP_C)
for i, ic in enumerate(('U1', 'U2', 'U3', 'X1'), 1):           # C1..C4 bypass U1, U2, U3, X1
    d.add(f'C{i}', C, '100nF', {1: '+5V', 2: 'GND'}, (40 + 15.24 * i, 230))
d.add('C5', CP, '47uF 16V', {1: '+5V', 2: 'GND'}, (120, 230))
FLG = d.standard('power', 'PWR_FLAG', '')
d.add('#FLG01', FLG, 'PWR_FLAG', {1: '+5V'}, (150, 230))
d.add('#FLG02', FLG, 'PWR_FLAG', {1: 'GND'}, (170, 230))

d.note(f'${PAGE:04X} write: PSG register number (BC2 = A0 = 0).  ${PAGE + 1:04X} write: value for that register '
       f'(BC2 = 1).  Both repeat over ${PAGE:04X}-${PAGE + 0xFF:04X} (A1-A7 not decoded). Reads do nothing.', 20, 255, 2)
d.note('U3 ATF22V10C-7PX: code/firmware/pld/audio_sequencer.pld (GALasm). Each write holds the CPU with RDY for about '
       '7 PSG clocks (~4 us); BDIR is high for 2 clocks (1.09 us). U3 drives RDY only during its own write.', 20, 261, 2)
d.note('X1 1.8432 MHz: tone f = 1843200 / (16 * period).', 20, 267, 2)

d.write(DESIGN, [(LIBNAME, f'${{KIPRJMOD}}/{LIBNAME}.pretty')])


def edge_jack():
    """PJ-307 for a jack whose front sits on the board edge (x = -3.2 in the footprint): the stock
    silk runs along that edge and round the barrel beyond it, so it is cut 0.3 mm inside."""
    stock = (LOCAL_FP / 'Jack_3.5mm_HOOYA_PJ-307_Horizontal.kicad_mod').read_text(encoding='utf-8')
    cut, out = -2.9, []
    for line in stock.splitlines():
        m = re.match(r'(\s*\(fp_line \(start )([-\d.]+) ([-\d.]+)\) \(end ([-\d.]+) ([-\d.]+)(\).*F\.SilkS.*)', line)
        if m:
            x0, y0, x1, y1 = map(float, m.group(2, 3, 4, 5))
            if max(x0, x1) <= cut: continue
            x0, x1 = max(x0, cut), max(x1, cut)
            line = f'{m.group(1)}{x0:g} {y0:g}) (end {x1:g} {y1:g}{m.group(6)}'
        out.append(line)
    text = '\n'.join(out).replace('(footprint "Jack_3.5mm_HOOYA_PJ-307_Horizontal"',
                                  '(footprint "Jack_3.5mm_HOOYA_PJ-307_Horizontal_EdgeMount"', 1)
    assert 'EdgeMount' in text
    (LOCAL_FP / 'Jack_3.5mm_HOOYA_PJ-307_Horizontal_EdgeMount.kicad_mod').write_text(text + '\n', encoding='utf-8')


edge_jack()

# ---- PCB -----------------------------------------------------------------------------------------
b = pcb.BOARD(); b.SetCopperLayerCount(2)
v = lambda x, y: pcb.VECTOR2I(pcb.FromMM(x), pcb.FromMM(y))
mm = lambda i: round(pcb.ToMM(i), 3)
nets = {}
for i, n in enumerate(sorted(d.netlist()), 1):
    ni = pcb.NETINFO_ITEM(b, '/' + n, i); b.Add(ni); nets[n] = ni
fps = {}
for p in d.parts + [dict(ref='LOGO1', fp=FP_LOGO, value='SolderDemon', nets={}, uuid=None)]:
    if p['ref'].startswith('#'): continue
    lib, name = p['fp'].split(':')
    f = pcb.FootprintLoad(str(DESIGN / 'SolderDemonCaps.pretty' if lib == 'SolderDemonCaps' else LOCAL_FP if lib == LIBNAME else g.LIB / 'footprints' / (lib + '.pretty')), name)
    assert f, p['fp']
    f.SetFPID(pcb.LIB_ID(lib, name))
    f.SetReference(p['ref']); f.SetValue(p['value'])
    if p['uuid']: f.SetPath(pcb.KIID_PATH('/' + d.uid('root') + '/' + p['uuid']))
    for pad in f.Pads():
        net = p['nets'].get(pad.GetNumber())
        if net: pad.SetNet(nets[net])
    f.Value().SetVisible(False)
    f.Reference().SetTextSize(v(1, 1)); f.Reference().SetTextThickness(pcb.FromMM(.15))
    b.Add(f); fps[p['ref']] = f
fps['LOGO1'].Reference().SetVisible(False)


def box(ref):
    """Courtyard (or body) of a footprint: left, top, right, bottom in mm."""
    f = fps[ref]
    c = f.GetCourtyard(pcb.F_CrtYd)
    if not c.OutlineCount(): c = f.GetCourtyard(pcb.B_CrtYd)
    bb = c.BBox() if c.OutlineCount() else f.GetBoundingBox(False)
    return mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())


def place(ref, x, y, rot=0, anchor='tl'):
    """Move a footprint so that a courtyard corner/edge ('tl', 'tr', 'bl', 'br', 'cl', 'cr', 'tc', 'bc')
    or its centre ('cc') lands on (x, y)."""
    f = fps[ref]; f.SetOrientationDegrees(rot); f.SetPosition(v(0, 0))
    l, t, r, bm = box(ref)
    dx = x - l if anchor[1] == 'l' else x - r if anchor[1] == 'r' else x - (l + r) / 2
    dy = y - t if anchor[0] == 't' else y - bm if anchor[0] == 'b' else y - (t + bm) / 2
    f.SetPosition(v(dx, dy))
    return box(ref)


def ref_at(ref, x, y, angle=0, size=1.0):
    t = fps[ref].Reference(); t.SetPosition(v(x, y)); t.SetTextAngle(pcb.EDA_ANGLE(angle, pcb.DEGREES_T))
    t.SetTextSize(v(size, size)); t.SetTextThickness(pcb.FromMM(.15 * size))


def pad(ref, num):
    p = next(p for p in fps[ref].Pads() if p.GetNumber() == str(num))
    return mm(p.GetPosition().x), mm(p.GetPosition().y)


# J1 is the SolderDemon 6502 host's J5 (busboard BACKPLANE.md): the stock right-angle socket on
# the component side at the left edge, pin 1 at (5.775, 14.425), pin 2 nearer the edge. Every
# board on the busboard then faces the same way.
J1_PIN1 = (5.775, 14.425)
j1 = fps['J1']
j1.SetOrientationDegrees(0)
j1.SetPosition(v(*J1_PIN1))
assert pad('J1', 1) == J1_PIN1 and pad('J1', 2) == (3.235, 14.425) and pad('J1', 35) == (5.775, 57.605)
assert j1.GetLayer() == pcb.F_Cu
# Final board coordinates: J1 follows PIN 1 down the same centred column.
J1_REF_FINAL = (10.225, 57.75)
ref_at('J1', W - J1_REF_FINAL[1], J1_REF_FINAL[0])

# J1 against the host's J5, signal by signal, from the host's own schematic.
HOST_SCH = ROOT.parent / 'solderdemon_6502' / 'design' / 'solderdemon_6502.kicad_sch'
host_xml = DESIGN / 'bus-reference.xml'
subprocess.run([CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(host_xml), str(HOST_SCH)],
               check=True, capture_output=True)
netlist_tree = ET.parse(host_xml)
netlist_tree.find("./design/source").text = HOST_SCH.name
netlist_tree.write(host_xml, encoding="UTF-8", xml_declaration=True)
host = {int(x.get('pin')): n.get('name') for n in ET.parse(host_xml).findall('./nets/net')
        for x in n.findall('node') if x.get('ref') == 'J5'}
SAME = {'CLK': 'PHI2', 'R/~{W}': 'RWB', 'VCC': '+5V', 'NMI': '~{NMI}', 'IRQ': '~{IRQ}'}
assert sorted(host) == list(range(1, 37)), host
assert all(SAME.get(host[i], host[i]) == bus[i] for i in host), \
    [(i, host[i], bus[i]) for i in host if SAME.get(host[i], host[i]) != bus[i]]

# Preliminary placement in the host frame; the final m68k-style layout is applied after rotation.
# The card stands in the busboard on its left edge, so the right edge is the top and the top and
# bottom edges are its sides. It is as deep as the busboard (100 mm), so its sides are flush with
# the busboard's, and the line out leaves through the top side (the pin 1 end, y = 0), the way a
# PC card's connector leaves through its bracket. Columns from the bus edge: U3 over U2 beside J1
# (U3 by the control and address rows, U2 by the data rows); the PSG, its outputs at the top next
# to the jack and the mixer; the mixer resistors. The clock, pull-downs and logo fill the bottom.
place('U3', 9.0, 14.5, 0)                                   # pin 1 top left, inputs face J1
place('U2', 9.0, 49.5, 0)
place('U1', 25.0, 14.5, 0)                                  # pin 1 top left: A, B and C at the top
assert pad('U1', 1)[0] < pad('U1', 40)[0] and pad('U1', 1)[1] < pad('U1', 20)[1], 'U1 pin 1 top left'
place('X1', 24.0, 76.0, 0)
for ref in ('U1', 'U2', 'U3'):
    l, t, r, bm = box(ref)
    ref_at(ref, (l + r) / 2, (t + bm) / 2, 90, 1.2)
l, t, r, bm = box('X1'); ref_at('X1', (l + r) / 2, (t + bm) / 2)


def part(ref, x, y, rot=0, anchor='tl', label=None, size=.9):
    """Place a small part; its reference goes beside or inside the courtyard."""
    l, t, r, bm = place(ref, x, y, rot, anchor)
    side = label or ('below' if rot in (0, 180) else 'right')
    if side == 'above': ref_at(ref, (l + r) / 2, t - .7, 0, size)
    elif side == 'below': ref_at(ref, (l + r) / 2, bm + .7, 0, size)
    elif side == 'left': ref_at(ref, l - .7, (t + bm) / 2, 90, size)
    elif side == 'east': ref_at(ref, r + 1.3, (t + bm) / 2, 0, size)
    elif side == 'inside': ref_at(ref, (l + r) / 2, (t + bm) / 2, 0, size)
    elif side == 'none': ref_at(ref, (l + r) / 2, (t + bm) / 2, 0, size)
    else: ref_at(ref, r + .7, (t + bm) / 2, 90, size)


# PJ-307 on the top side, plug outwards: the body's front on the edge, the barrel over it.
JACK_X = 15.3
fps['J2'].SetOrientationDegrees(270)
fps['J2'].SetPosition(v(JACK_X, 3.2))                       # pad S 3.2 mm inside: the body edge on the board edge
ref_at('J2', JACK_X, 5.6, 0, .8)                           # inside its outline
part('C6', 22.2, 1.6, 0, label='below', size=.8)
part('C7', 29.4, 1.6, 0, label='below', size=.8)
part('J3', 37.6, 1.2, 0, label='none', size=.01)            # L G R, pin 1 at the top
fps['J3'].Reference().SetVisible(False)
part('C5', 44.8, 1.0, 0, label='left', size=.8)             # bulk

part('C3', 20.7, 13.0, 90, label='inside', size=.8)          # U3, in the gap to U1
part('C2', 20.7, 48.0, 90, label='inside', size=.8)          # U2
part('C1', 45.4, 9.0, 0, label='inside', size=.8)            # U1, by pin 40
for i, ref in enumerate(('R5', 'R6', 'R7', 'R8', 'R9', 'R10', 'R11', 'R12', 'R13')):
    part(ref, 45.4, 13.4 + 3.3 * i, 0, label='east', size=.8)

part('C4', 38.4, 76.0, 90, label='inside', size=.8)         # X1
for i, ref in enumerate(('R2', 'R1', 'R3', 'R4')):          # clock to U3 and U1, BDIR and CAPTURE pull-downs
    part(ref, 42.8 + 3.57 * i, 76.0, 90, label='below', size=.8)

# The logo under U2.
LOGO_TL = (10.0, 77.0)
lb = fps['LOGO1'].GetBoundingBox(False)
fps['LOGO1'].Move(v(LOGO_TL[0] - mm(lb.GetLeft()), LOGO_TL[1] - mm(lb.GetTop())))


# ---- outline: R4 on the bus edge, R10 on the edge that stands up -----------------------------
def inside(x, y, margin):
    """Is (x, y) at least `margin` inside the rounded outline?"""
    for cx, cy, r in ((R_BUS, R_BUS, R_BUS), (R_BUS, H - R_BUS, R_BUS), (W - R_FAR, R_FAR, R_FAR), (W - R_FAR, H - R_FAR, R_FAR)):
        if (x - cx) * (1 if cx > W / 2 else -1) > 0 and (y - cy) * (1 if cy > H / 2 else -1) > 0:
            return math.hypot(x - cx, y - cy) <= r - margin
    return margin <= x <= W - margin and margin <= y <= H - margin


# Nothing may overlap or leave the board (J1 and J2 hang over the edge on purpose).
bad = []
items = [r for r in fps if r != 'LOGO1']
for i, a in enumerate(items):
    al, at, ar, ab = box(a)
    if a not in ('J1', 'J2') and not all(inside(x, y, .5) for x in (al, ar) for y in (at, ab)):
        bad.append(f'{a} off the board')
    for c in items[i + 1:]:
        cl, ct, cr, cb = box(c)
        if al < cr and cl < ar and at < cb and ct < ab: bad.append(f'{a} overlaps {c}')
assert not bad, bad


def seg(a, z, layer=pcb.F_SilkS, width=.15):
    e = pcb.PCB_SHAPE(b); e.SetShape(pcb.SHAPE_T_SEGMENT); e.SetStart(v(*a)); e.SetEnd(v(*z))
    e.SetLayer(layer); e.SetWidth(pcb.FromMM(width)); b.Add(e)


def arc(start, mid, end):
    e = pcb.PCB_SHAPE(b); e.SetShape(pcb.SHAPE_T_ARC); e.SetArcGeometry(v(*start), v(*mid), v(*end))
    e.SetLayer(pcb.Edge_Cuts); e.SetWidth(pcb.FromMM(.05)); b.Add(e)


def text(s, x, y, size=1.0, layer=pcb.F_SilkS, left=True, angle=0, bold=False):
    t = pcb.PCB_TEXT(b); t.SetText(s); t.SetPosition(v(x, y)); t.SetTextSize(v(size, size))
    t.SetTextThickness(pcb.FromMM(size * (.2 if bold else .15))); t.SetLayer(layer)
    if left: t.SetHorizJustify(pcb.GR_TEXT_H_ALIGN_LEFT)
    if angle: t.SetTextAngle(pcb.EDA_ANGLE(angle, pcb.DEGREES_T))
    if layer == pcb.B_SilkS: t.SetMirrored(True)
    b.Add(t)


a, k = R_BUS, R_BUS * (1 - math.sqrt(.5))
c, kc = R_FAR, R_FAR * (1 - math.sqrt(.5))
E = pcb.Edge_Cuts
seg((a, 0), (W - c, 0), E, .05); seg((W, c), (W, H - c), E, .05)
seg((W - c, H), (a, H), E, .05); seg((0, H - a), (0, a), E, .05)
arc((W - c, 0), (W - kc, kc), (W, c)); arc((W, H - c), (W - kc, H - kc), (W - c, H))
arc((a, H), (k, H - k), (0, H - a)); arc((0, a), (k, k), (a, 0))

text('LINE OUT', JACK_X, 12.0, .8, left=False)
for k, ch in enumerate(('L', 'GND', 'R')):                             # beside each pin of J3
    x_, y_ = pad('J3', k + 1); text(ch, 22.3, y_, .8, left=False)
# as on the host's J5: the socket hides the square pad, so pin 1 is marked above it
text('PIN 1', J1_PIN1[0], J1_PIN1[1] - 4.2, .8, left=False)
tri = pcb.PCB_SHAPE(b, pcb.SHAPE_T_POLY)
tri.SetPolyPoints(pcb.VECTOR_VECTOR2I([v(J1_PIN1[0], J1_PIN1[1] - 1.7), v(J1_PIN1[0] - .7, J1_PIN1[1] - 2.9),
                                       v(J1_PIN1[0] + .7, J1_PIN1[1] - 2.9)]))
tri.SetFilled(True); tri.SetWidth(0); tri.SetLayer(pcb.F_SilkS); b.Add(tri)
# ---- the whole board a quarter turn anticlockwise: the bus at the bottom, as on the m68k card ----
# Everything above is laid out and checked with the bus on the left, the frame of BACKPLANE.md and
# of the host's J5. The files show the card the way it is drawn on the m68k side, bus edge down;
# the card itself does not change. (x, y) -> (y, W - x); the jack ends up on the left edge.
O = pcb.VECTOR2I(0, 0)
for item in list(b.GetFootprints()) + list(b.GetDrawings()):
    item.Rotate(O, pcb.EDA_ANGLE(90, pcb.DEGREES_T))
    item.Move(v(0, W))
W, H = H, W
assert pad('J1', 1) == (14.425, round(H - 5.775, 3)) and pad('J1', 35) == (57.605, round(H - 5.775, 3)), (pad('J1', 1), pad('J1', 35))
lb = fps['LOGO1'].GetBoundingBox(False).GetCenter()          # the logo stays upright
fps['LOGO1'].Rotate(lb, pcb.EDA_ANGLE(-90, pcb.DEGREES_T))
texts = [d for d in b.GetDrawings() if isinstance(d, pcb.PCB_TEXT)] + [f.Reference() for f in b.GetFootprints()]
for t in texts:                                             # a turned label reads from the right edge or
    a_ = t.GetTextAngleDegrees() % 360                       # upside down: put it back level about its centre
    if a_ == 180 or a_ in (90.0, 270.0) and len(t.GetText()) <= 4:
        c_ = t.GetBoundingBox().GetCenter()
        t.SetTextAngle(pcb.EDA_ANGLE(0, pcb.DEGREES_T))
        t.Move(c_ - t.GetBoundingBox().GetCenter())
# Keep J1 vertical like PIN 1; the two labels read as one sequence with a small gap.
fps['J1'].Reference().SetTextAngle(pcb.EDA_ANGLE(90, pcb.DEGREES_T))

# Arrange the card like the m68k audio card, keeping the 6502 bus connector fixed.
# Positions are courtyard top-left coordinates in the final, bus-down view.
layout = mech.layout('6502')
layout.update(J2=(-3.7, 11, 0), LOGO1=(17, 3, 0))
for ref, (x, y, rotation) in layout.items():
    l, t, r, bm = place(ref, x, y, rotation)
    if ref.startswith('U') or ref == 'X1':
        ref_at(ref, (l+r)/2, (t+bm)/2, 0, 1.0)
    elif ref.startswith('R'):
        ref_at(ref, (l+r)/2, (t+bm)/2, 90 if rotation == 90 else 0, .8)
    elif ref in ('C1', 'C2', 'C3'):
        ref_at(ref, (l+r)/2, t - .8, 0, .8)
    elif ref.startswith('C'):
        ref_at(ref, r + .9, (t+bm)/2, 90, .8)
    elif ref == 'J2':
        ref_at(ref, 10.5, 24.5, 0, .8)
# Discard labels tied to the old locations; replace them next to the connectors.
removed_labels = [item for item in b.GetDrawings() if isinstance(item, pcb.PCB_TEXT) and item.GetText() in ('LINE OUT', 'L', 'G', 'GND', 'R')]
for item in removed_labels:
    b.Remove(item)
text('LINE OUT', 4, 24.5, .8, left=False)
for k, ch in enumerate(('L', 'GND', 'R')):
    x_, y_ = pad('J3', k + 1)
    text(ch, 22.3, y_, .8, left=False)

# Check final courtyards as well as the initial placement.
bad = []
items = [r for r in fps if r != 'LOGO1']
for i, a in enumerate(items):
    al, at, ar, ab = box(a)
    if a not in ('J1', 'J2') and (al < .5 or at < .5 or ar > W-.5 or ab > H-.5):
        bad.append(f'{a} off the board')
    for c in items[i+1:]:
        cl, ct, cr, cb = box(c)
        if al < cr and cl < ar and at < cb and ct < ab:
            bad.append(f'{a} overlaps {c}')
assert not bad, bad

# Title and notes in the new frame: the front title under the logo, the notes behind U1.
lb = fps['LOGO1'].GetBoundingBox(False)
l, r, bm = mm(lb.GetLeft()), mm(lb.GetRight()), mm(lb.GetBottom())
text('SolderDemon 6502', (l + r) / 2, bm + 2.2, 1.0, left=False, bold=True)
text(f'AUDIO {REV}  ${PAGE:04X}/${PAGE + 1:04X}', (l + r) / 2, bm + 4.4, .8, left=False)
l, t, r, bm = box('U1')                                     # behind U1, between its pad rows
for i, line in enumerate(['SOLDERDEMON 6502', f'AUDIO {REV}', f'${PAGE:04X} W: register', f'${PAGE + 1:04X} W: value',
                          'reads ignored', 'U3 sequencer', 'RDY ~4 us/write', 'X1 1.8432 MHz']):
    text(line, l + (r - l) * (.7 if i < 4 else .3), (t + bm) / 2 - 2.4 + (i % 4) * 1.6, .8, pcb.B_SilkS, left=False)
l, t, r, bm = box('U3')                                      # between U3's pad rows
text(f'Copyright ©{DATE[:4]} SolderDemon', (l + r) / 2, (t + bm) / 2, .9, pcb.B_SilkS, left=False)

# No-connect pins get 'unconnected-(...)' nets in the schematic; copy nets and pin metadata onto the pads.
netlist = DESIGN / 'schematic.xml'
subprocess.run([CLI, 'sch', 'export', 'netlist', '--format', 'kicadxml', '-o', str(netlist),
                str(DESIGN / f'{NAME}.kicad_sch')], check=True, capture_output=True)
netlist_tree = ET.parse(netlist)
netlist_tree.find("./design/source").text = f'{NAME}.kicad_sch'
netlist_tree.write(netlist, encoding="UTF-8", xml_declaration=True)
for n in ET.parse(netlist).findall('./nets/net'):
    for node in n.findall('node'):
        f = fps.get(node.get('ref'))
        pd_ = next((pd for pd in f.Pads() if pd.GetNumber() == node.get('pin')), None) if f else None
        if pd_ is None: continue
        ni = b.FindNet(n.get('name'))
        if ni is None:
            ni = pcb.NETINFO_ITEM(b, n.get('name')); b.Add(ni)
        pd_.SetNet(ni)
        if node.get('pinfunction'): pd_.SetPinFunction(node.get('pinfunction'))
        if node.get('pintype'): pd_.SetPinType(node.get('pintype'))

removed_labels += mech.finish_labels(b, pcb)
pcb.SaveBoard(str(DESIGN / (NAME + '.kicad_pcb')), b)
pcb.ExportSpecctraDSN(b, str(DESIGN / 'routing.dsn'))
print('Generated', len(d.parts), 'symbols;', len(nets), 'nets;', f'{W:g} x {H:g} mm')
