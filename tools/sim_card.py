"""Timed simulation of the r1 card from design.json, with U3 running its real JEDEC fuse map.

    python tools/sim_card.py

Every part is wired by the netlist the generator wrote, not by assumption: U3 is tools/gal.py
fed with code/firmware/pld/audio_sequencer.jed, U2 is a 74HCT273, U1 a YM2149 register file that checks
its own bus timing. The CPU is a W65C02S bus model: address bits change one by one somewhere
between tAH and tADS after PHI2 falls (so the decoder sees the glitches), write data appears
up to tMDS after PHI2 rises and is driven until tDHW after it falls, read cycles put random bytes
on the data bus, RDY is sampled tPCS before PHI2 falls and a held cycle repeats.

The program mixes PSG writes with everything that must not disturb the card: reads of the PSG
page, writes next door ($C000, the DUART; $C200), a read-modify-write and back-to-back stores.
It runs at 14, 10, 4, 1 and 0.25 MHz, with the GAL at its fastest and slowest delay and the PSG
clock at several phases, and checks:
  - the PSG register file ends up exactly as the program wrote it, one BDIR pulse per write;
  - BDIR 300 ns - 10 us, DA and BC2 stable 300 ns before it rises and 80 ns after it falls
    (Yamaha p. 10), the register number's upper nibble 0 (chip address A9 = 0, A8 = 1);
  - U2 only ever latches the byte the CPU is writing;
  - RDY is low tPCS before the first PHI2 fall of every card write, never low in another cycle,
    driven only during the card's own writes, and driven high before it is let go;
  - no BDIR and a cleared latch while /RESET is low.
It proves the logic and the timing of the model, not of the silicon.
"""
from pathlib import Path
import json, random, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
import gal

PAGE = 0xC100
T_PSG = 1e3 / 1.8432                     # ns
TADS, TAH, TMDS, TDHW, TPCS = 30.0, 10.0, 30.0, 10.0, 10.0     # W65C02S, 5 V, 14 MHz column
TH_273 = 3.0                             # 74HCT273 data hold after CP
YM_SETUP, YM_HOLD, YM_MIN, YM_MAX = 300.0, 80.0, 300.0, 10000.0
DT = 0.5
RDY_RISE = 80.0                          # 3k3 and the bus load: a released low RDY reads high this much later

parts = {p['ref']: p for p in json.loads((ROOT / 'design/design.json').read_text())}
src = (ROOT / 'code' / 'firmware' / 'pld' / 'audio_sequencer.pld').read_text()
gpin = gal.pins(src)
FUSES = gal.jedec((ROOT / 'code' / 'firmware' / 'pld' / 'audio_sequencer.jed').read_text())


def net(ref, pin):
    return parts[ref]['nets'][str(pin)]


# U3: pin -> net; unconnected outputs get a net of their own so combinational feedback (NREQ)
# reads the pin, as the silicon does (AGENTS.md).
U3 = {p: net('U3', p) or f'U3.{gpin[p]}' for p in gpin if gpin[p] not in ('VCC', 'GND', 'NC')}
U3_OUT = {p for p in range(14, 24) if gpin[p] != 'NC'}
U2_D = [net('U2', p) for p in (3, 4, 7, 8, 13, 14, 17, 18)]
U2_Q = [net('U2', p) for p in (2, 5, 6, 9, 12, 15, 16, 19)]
U2_CP, U2_MR = net('U2', 11), net('U2', 1)
U1_DA = [net('U1', 37 - i) for i in range(8)]
U1_BDIR, U1_BC2, U1_BC1, U1_RESET, U1_CLK = (net('U1', p) for p in (27, 28, 29, 23, 22))
BUS = {n: int(p) for p, n in parts['J1']['nets'].items() if n}
A = {i: f'A{i}' for i in range(16) if f'A{i}' in BUS}
D = [f'D{i}' for i in range(8)]
assert U2_D == D and U2_Q == U1_DA, 'U2 between the data bus and the PSG'
assert U1_BC2 == 'A0' and U1_BC1 == 'GND' and U1_BDIR == U3[21] and U2_CP == U3[22] and U3[23] == 'RDY'
assert net('R1', 1) == net('R2', 1) == net('X1', 5) and U1_CLK == net('R1', 2) and U3[1] == net('R2', 2)
assert parts['U1']['nets']['24'] == 'GND' and parts['U1']['nets']['25'] == '+5V' and parts['U1']['nets']['26'] == '+5V'


class Card:
    def __init__(self, tc, tpd, phase, seed):
        self.rng = random.Random(seed)
        self.tc, self.tpd = tc, tpd
        self.v = {n: 0 for n in set(U3.values()) | set(D) | set(A.values()) | set(U2_Q) |
                  {'PHI2', 'RWB', '~{RESET}', 'RDY', 'GND'}}
        self.v.update({'RWB': 1, '+5V': 1, 'RDY': 1})
        self.drv = {n: None for n in U3.values()}          # what U3 drives (None = off)
        self.g = gal.Gal22V10(FUSES)
        self.t = 0.0
        self.next_clk = phase
        self.pending = []                                  # (time, net, value or None)
        self.log = {n: [(0.0, self.v[n])] for n in self.v}
        self.rdy_up = None                                 # time the pull-up brings a released RDY high
        self.captures, self.pulses, self.errors = [], [], []
        self.regs, self.addr = {}, 0
        self.data_valid = False                            # the CPU drives D0-D7 with the cycle's byte
        self.cycle = None                                  # (addr, rw, data) on the bus
        self.dirty = True
        self.want, self.events = {}, []
        self.cur, self.mine = (0x1000, 1), False

    # ---- nets ----
    def set(self, n, val):
        if self.v.get(n) != val:
            old = self.v.get(n)
            self.v[n] = val
            self.log.setdefault(n, [(0.0, old)]).append((self.t, val))
            self.on_change(n, old, val)
            self.dirty = True

    def on_change(self, n, old, val):
        if n == U2_CP and old == 0 and val == 1:          # 74HCT273: rising CP
            if self.v[U2_MR]:
                byte = sum(self.v[d] << i for i, d in enumerate(D))
                self.captures.append((self.t, byte, self.cycle, self.data_valid))
                for i, q in enumerate(U2_Q): self.set(q, self.v[D[i]])
        if n == U2_MR and val == 0:
            for q in U2_Q: self.set(q, 0)
        if n == U1_BDIR:
            if val: self.bdir_rise = self.t
            else: self.ym_pulse(self.bdir_rise, self.t)

    def ym_pulse(self, t0, t1):
        """BDIR was high from t0 to t1: what the YM2149 does with it, and its timing."""
        da = sum(self.v[q] << i for i, q in enumerate(U1_DA))
        bc2 = self.v[U1_BC2]
        self.pulses.append((t0, t1, bc2, da))
        if not YM_MIN <= t1 - t0 <= YM_MAX: self.errors.append(f'BDIR {t1 - t0:.0f} ns at {t0:.0f}')
        if not self.v[U1_RESET]: self.errors.append(f'BDIR during reset at {t0:.0f}')
        for n in U1_DA + [U1_BC2]:
            last = max(t for t, _ in self.log[n] if t <= t1)
            if last > t0 - YM_SETUP: self.errors.append(f'{n} changed {t0 - last:.0f} ns before BDIR at {t0:.0f}')
        if bc2 == 0:                                       # BC2 BC1 = 0 0, BDIR 1: latch address
            if da >> 4: self.errors.append(f'address byte {da:02X}: upper nibble must be 0')
            self.addr = da & 15
        else:                                              # 1 0: write
            self.regs[self.addr] = da

    # ---- U3 ----
    def gal_eval(self):
        level = {p: self.v[n] for p, n in U3.items() if self.v.get(n) is not None}
        out = self.g.outputs(level)
        for p in U3_OUT:
            n, val = U3[p], out[p]
            if self.want.get(n, self.drv[n]) != val:
                self.want[n] = val
                self.pending.append((self.t + self.tpd, n, val))

    def clock(self):
        level = {p: self.v[n] for p, n in U3.items() if self.v.get(n) is not None}
        self.g.clock(level)
        self.dirty = True

    def drive(self, n, val):
        self.drv[n] = val
        if n == 'RDY' and val == 0 and not self.mine:
            self.errors.append(f'U3 pulls RDY low at {self.t:.0f} during a cycle to {self.cur[0]:04X} rw={self.cur[1]}')
        if n == 'RDY':
            if val is None:                                # released: the host's 3k3 pulls it up
                if self.v['RDY'] == 0: self.rdy_up = self.t + RDY_RISE
            else:
                self.rdy_up = None; self.set('RDY', val)
        else:
            self.set(n, 0 if val is None else val)        # R3/R4 pull BDIR and CAPTURE low

    # ---- time ----
    def run_until(self, t_end):
        while self.t < t_end:
            if self.next_clk <= self.t:                    # rising edge of X1
                self.clock(); self.next_clk += T_PSG
            due = [e for e in self.pending if e[0] <= self.t]
            if due:
                self.pending = [e for e in self.pending if e[0] > self.t]
                for _, n, val in sorted(due, key=lambda e: e[0]): self.drive(n, val)
            if self.rdy_up is not None and self.t >= self.rdy_up:
                self.set('RDY', 1); self.rdy_up = None
            if self.dirty:
                self.dirty = False; self.gal_eval()
            for ev in [e for e in self.events if e[0] <= self.t]:
                self.events.remove(ev); ev[1]()
            self.t = round(self.t + DT, 3)

    def bus_cycle(self, addr, rw, data):
        """One CPU cycle starting at a PHI2 fall (now); repeats while RDY is low. Returns cycles used."""
        n = 0
        first = True
        self.cur, self.mine = (addr, rw), rw == 0 and addr >> 8 == PAGE >> 8
        while True:
            t0, tc = self.t, self.tc
            old = sum(self.v[A[i]] << i for i in A)
            if first:
                for i in A:                               # each bit settles at its own time
                    if (old ^ addr) >> i & 1:
                        self.events.append((t0 + self.rng.uniform(TAH, TADS), lambda i=i: self.set(A[i], addr >> i & 1)))
                if self.v['RWB'] != rw:
                    self.events.append((t0 + self.rng.uniform(TAH, TADS), lambda: self.set('RWB', rw)))
                self.events.append((t0 + TADS, lambda: setattr(self, 'cycle', (addr, rw, data))))
            self.events.append((t0 + tc / 2, lambda: self.set('PHI2', 1)))
            if rw:                                         # someone else's byte, from somewhere in PHI2 high
                junk = self.rng.randrange(256)
                self.events.append((t0 + tc / 2 + self.rng.uniform(0, TMDS),
                                    lambda: [self.set(D[i], junk >> i & 1) for i in range(8)]))
            else:
                when = t0 + tc / 2 + self.rng.uniform(5, TMDS)
                self.events.append((when, lambda: [self.set(D[i], data >> i & 1) for i in range(8)]))
                self.events.append((t0 + tc / 2 + TMDS, lambda: setattr(self, 'data_valid', True)))
            self.run_until(t0 + tc - TPCS)
            rdy, driven = self.v['RDY'], self.drv['RDY']
            rdy_since = max(t for t, _ in self.log['RDY'])
            self.run_until(t0 + tc)
            self.set('PHI2', 0)
            if not rw:                                     # tDHW later the CPU stops driving
                self.events.append((self.t + TDHW, lambda: setattr(self, 'data_valid', False)))
            n += 1
            mine = rw == 0 and addr >> 8 == PAGE >> 8
            if first and mine and (rdy or rdy_since > t0 + tc - TPCS):
                self.errors.append(f'RDY not low {TPCS:.0f} ns before PHI2 fell, write to {addr:04X}')
            if not mine and not rdy:
                self.errors.append(f'RDY low in a cycle to {addr:04X} rw={rw}')
            if not mine and driven is not None:
                self.errors.append(f'U3 drives RDY in a cycle to {addr:04X} rw={rw}')
            if mine and driven is None:
                self.errors.append(f'RDY floating when sampled, write to {addr:04X}')
            first = False
            if rdy: return n
            if n > 400: self.errors.append('CPU held for 400 cycles'); return n


def program(rng):
    """Bus cycles of a 65C02 program, and the PSG registers it leaves behind."""
    cyc, regs, sel = [], {}, 0
    pc = 0x11F0                                            # A8 = 1 in the fetches: their way to $C0xx/$C2xx
                                                           # can pass through $C1xx

    def fetch(k):
        nonlocal pc
        for _ in range(k): cyc.append((pc, 1, None)); pc += 1

    def store(addr, val):                                  # STA abs: 3 fetches, 1 write
        nonlocal sel
        fetch(3); cyc.append((addr, 0, val))
        if addr >> 8 == PAGE >> 8:
            if addr & 1: regs[sel] = val
            else: sel = val & 15
    for _ in range(14):
        reg, val = rng.randrange(14), rng.randrange(256)
        store(PAGE | rng.choice((0x00, 0x10, 0xFE)), reg)            # register number, mirrors too
        if rng.random() < .3: fetch(3); cyc.append((PAGE + 1, 1, None))   # LDA $C101: ignored
        if rng.random() < .3: store(0xC000 + rng.randrange(16), rng.randrange(256))   # the DUART
        if rng.random() < .3: store(rng.choice((0xC200, 0xC300, 0xC0FF, 0xE100, 0x4100)), rng.randrange(256))
        store(PAGE | rng.choice((0x01, 0x11, 0xFF)), val)
    # Neighbours of the card's decode one bus cycle apart, in both directions: their address bits
    # change one by one, so on the way the decoder briefly sees a write to $C1xx.
    for _ in range(40):
        near = [(0x1100 | rng.randrange(256), 1), (PAGE | rng.randrange(256), 1), (0xC000 | rng.randrange(256), 0),
                (0xC200 | rng.randrange(256), 0), (0xC300 | rng.randrange(256), 0), (0x4100 | rng.randrange(256), 0),
                (0xE100 | rng.randrange(256), 0), (0xC000 | rng.randrange(256), 1)]
        for addr, rw in rng.sample(near, 2):
            cyc.append((addr, rw, None if rw else rng.randrange(256)))
    # INC $C101 on a 65C02: opcode, two operand bytes, read, read again, write. One PSG write.
    fetch(3); cyc += [(PAGE + 1, 1, None), (PAGE + 1, 1, None), (PAGE + 1, 0, 0x42)]
    regs[sel] = 0x42
    return cyc, regs


def run(mhz, tpd, seed):
    rng = random.Random(seed)
    c = Card(1e3 / mhz, tpd, rng.uniform(0, T_PSG), seed)
    c.set('~{RESET}', 0)
    c.run_until(2000)
    if any(c.v[q] for q in U2_Q) or c.v[U1_BDIR]: c.errors.append('latch or BDIR not cleared by reset')
    c.set('~{RESET}', 1)
    c.run_until(2500)
    cyc, want = program(rng)
    held, writes = [], 0
    for addr, rw, data in cyc:
        n = c.bus_cycle(addr, rw, data)
        if rw == 0 and addr >> 8 == PAGE >> 8:
            writes += 1; held.append(n - 1)
        elif n != 1:
            c.errors.append(f'cycle to {addr:04X} held')
    c.run_until(c.t + 3 * T_PSG)
    if c.regs != want: c.errors.append(f'registers {c.regs} != {want}')
    if len(c.pulses) != writes: c.errors.append(f'{len(c.pulses)} BDIR pulses for {writes} writes')
    for t, byte, cycle, valid in c.captures:
        if cycle is None or cycle[1] != 0 or cycle[0] >> 8 != PAGE >> 8 or byte != cycle[2]:
            c.errors.append(f'U2 latched {byte:02X} at {t:.0f} in cycle {cycle}')
    # the YM2149 hold after each BDIR fall
    for t0, t1, bc2, da in c.pulses:
        for n in U1_DA + [U1_BC2]:
            after = [t for t, _ in c.log[n] if t1 < t]
            if after and after[0] < t1 + YM_HOLD: c.errors.append(f'{n} changed {after[0] - t1:.0f} ns after BDIR fell')
    widths = [t1 - t0 for t0, t1, _, _ in c.pulses]
    return dict(mhz=mhz, tpd=tpd, seed=seed, writes=writes, errors=c.errors,
                held=(min(held), max(held)), bdir=(min(widths), max(widths)),
                us_per_write=max(held) * c.tc / 1e3 if held else 0)


if __name__ == '__main__':
    results, bad = [], []
    for mhz in (14.0, 10.0, 4.0, 1.0, 0.25):
        for tpd in (3.0, 7.5):
            for seed in range(3):
                r = run(mhz, tpd, seed * 101 + int(mhz * 10))
                results.append(r)
                if r['errors']: bad.append(r)
                print(f"{mhz:5.2f} MHz tpd {tpd:3.1f} seed {seed}: {r['writes']} writes, held {r['held'][0]}-{r['held'][1]} "
                      f"cycles (<= {r['us_per_write']:.2f} us), BDIR {r['bdir'][0]:.0f}-{r['bdir'][1]:.0f} ns, "
                      f"{len(r['errors'])} errors", flush=True)
    for r in bad:
        print(r['mhz'], r['tpd'], r['seed'], r['errors'][:5])
    hold = TDHW - 7.5 - TH_273
    text = (f"{'FAIL' if bad else 'PASS'}: {len(results)} runs (14/10/4/1/0.25 MHz, GAL 3/7.5 ns, 3 PSG clock phases), "
            f"{sum(r['writes'] for r in results)} PSG writes through the JEDEC fuse map; registers, one BDIR per write, "
            f"BDIR {min(r['bdir'][0] for r in results):.0f}-{max(r['bdir'][1] for r in results):.0f} ns, YM setup/hold, "
            f"latch contents, RDY timing and scope.\n"
            f"Paper hold at the PHI2-fall capture: tDHW {TDHW:.0f} - GAL {7.5} - 273 th {TH_273:.0f} = {hold:+.1f} ns "
            f"(see docs/TIMING.md).\n")
    (ROOT / 'sim-verification.txt').write_text(text)
    print(text)
    sys.exit(1 if bad else 0)
