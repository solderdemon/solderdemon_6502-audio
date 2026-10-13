"""Run the ca65/ld65 build of code/demo on py65's 65C02 against the r1 card's register model.

    python tools/verify_driver.py

The card (tools/sim_card.py proves it from the fuse map): a store with A0 = 0 anywhere in
$C100-$C1FF latches a register number, one with A0 = 1 writes that register; reads do nothing.
Checks every register/value pair, register and flag preservation, IRQs masked between the two
stores of a pair, no reads of the page, init/silence/chord contents.
"""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tmp/python'))
from py65.devices.mpu65c02 import MPU

PAGE = 0xC100
labels = {line.split()[2].lstrip('.'): int(line.split()[1], 16)
          for line in (root / 'code/demo/demo.lbl').read_text().splitlines()}


class Card(list):
    def __init__(self):
        super().__init__([0] * 65536)
        self.cpu = None
        self.reset()

    def reset(self):
        self.sel, self.written, self.errors = None, [], []

    def __setitem__(self, a, v):
        if isinstance(a, int) and a >> 8 == PAGE >> 8:
            if not self.cpu.p & 0x04:
                self.errors.append(f'store to {a:04X} with IRQs enabled')
            if a & 1 == 0:
                if v >> 4: self.errors.append(f'register number {v:02X}')
                self.sel = v
            else:
                if self.sel is None: self.errors.append('value stored before any register number')
                self.written.append((self.sel, v))
                self.sel = None                    # every value has its own register number
        super().__setitem__(a, v)

    def __getitem__(self, a):
        if isinstance(a, int) and a >> 8 == PAGE >> 8:
            self.errors.append(f'read of {a:04X}')
        return super().__getitem__(a)


mem = Card(); cpu = MPU(memory=mem); mem.cpu = cpu
binary = (root / 'code/demo/demo.bin').read_bytes(); mem[0x1000:0x1000 + len(binary)] = binary


def call(name, a=0, x=0, y=0, p=0x30):
    cpu.a, cpu.x, cpu.y, cpu.p, cpu.sp, cpu.pc = a, x, y, p, 0xFD, labels[name]
    mem[0x1FE] = 0xFF; mem[0x1FF] = 0x1F                # RTS sentinel: returns to $2000
    mem.reset()
    for _ in range(10000):
        if cpu.pc == 0x2000: break
        cpu.step()
    else:
        raise AssertionError('routine did not return')
    assert cpu.sp == 0xFF and not mem.errors, mem.errors
    return mem.written


for reg in range(14):
    for value in range(256):
        flags = 0x30 | (reg & 1) * 0x08 | (value & 1) * 0x04 | (value & 2) >> 1 | (value & 0x80)
        assert call('psg_write', value, reg, 0xA5, flags) == [(reg, value)]
        assert (cpu.a, cpu.x, cpu.y, cpu.p) == (value, reg, 0xA5, flags)
assert call('psg_write', 0x11, 0x2B, 0, 0x30) == [(0x0B, 0x11)]     # register number masked to 0..15
assert call('psg_init', 0x5A, 0xA5, 0xC3, 0x39) == [(i, 0) for i in range(13, -1, -1)] + [(7, 0x3F)]
assert (cpu.a, cpu.x, cpu.y, cpu.p) == (0x5A, 0xA5, 0xC3, 0x39)
regs = dict(call('psg_test_chord'))
assert regs[7] == 0x38 and [regs[i] for i in (8, 9, 10)] == [10, 10, 10]
periods = [regs[i] + 256 * regs[i + 1] for i in (0, 2, 4)]
assert periods == [440, 349, 294], periods
notes = [1843200 / 16 / p for p in periods]
assert all(abs(f / ref - 1) < .002 for f, ref in zip(notes, (261.63, 329.63, 392.00))), notes
assert call('psg_silence', 1, 2, 3, 0x30) == [(8, 0), (9, 0), (10, 0)]
assert (cpu.a, cpu.x, cpu.y) == (1, 2, 3)
text = ('PASS: ca65/ld65 binary on py65 65C02 against the r1 register model; 3584 register/value pairs; '
        'A/X/Y/flag preservation; IRQs masked across each pair; register number masked; no reads of $C1xx; '
        'init, silence; chord ' + ' / '.join(f'{f:.1f} Hz' for f in notes) + ' at 1.8432 MHz.\n')
(root / 'driver-verification.txt').write_text(text)
print(text)
