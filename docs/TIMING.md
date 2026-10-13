# Timing and electrical limits — r1

Design target: 5 V ±5 %, WDC W65C02S at any clock up to 14 MHz (the host's X1), ATF22V10C-7PX,
74HCT273, YM2149F on a 1.8432 MHz can. This is a paper analysis plus a timed model
(`tools/sim_card.py`), not a measured board.

r1 works like the SolderDemon m68k Audio card: the CPU stores two bytes and the card times the PSG
itself. The 6502 has no `/DTACK`, so the card stretches the write with `RDY` instead, which the
W65C02S honours in write cycles too (an NMOS 6502 or a Rockwell R65C02 does not: they need the r1
VIA card).

## Sequence of one store

`T` = one PSG clock = 542.5 ns. U3 counts `T` in Gray code (`000 001 011 010 110 111 101 100`),
held in `000` by `AR` whenever there is no request.

| Step | When | What |
|---|---|---|
| Request | address and `RWB` settle in PHI2 low | `REQ` = write to `$C100-$C1FF` with `/RESET` high |
| `RDY` low | PHI2 rise + 2 × tPD (`EN`, then the output enable) | the W65C02S holds and repeats the write cycle |
| Capture | every PHI2 fall in states 0–2, plus the moment state 3 is reached if PHI2 is high | `CAPTURE` rises, U2 takes D0–D7 |
| Count starts | first X1 edge that sees PHI2 high | so the first write cycle has driven its byte before the window closes, at any CPU clock |
| `BDIR` high | states 4–5 | 2 T = **1085 ns** (Yamaha: 300 ns – 10 µs) |
| `DONE` | state 7, 1 T after `BDIR` fell | `RDY` driven **high** |
| Release | CPU leaves the address after the next PHI2 fall | `RDY` tri-stated, `AR` resets the counter |

One store holds the CPU for 6–8 T, **≤ 4.9 µs** in the model (68 cycles at 14 MHz, 46 at 10 MHz,
4 at 1 MHz); a register + value pair costs about 10 µs. IRQ and NMI wait for the store to finish.

## CPU → card

At 14 MHz (71.4 ns, 35.7 ns per half), WDC 5 V column: tADS 30 ns, tMDS 30 ns, tDHW 10 ns, RDY
set-up tPCS 10 ns. ATF22V10C-7: tPD 7.5 ns.

| Path | Budget | Margin |
|---|---|---|
| `RDY` low before the first PHI2 fall | 35.7 + 2 × 7.5 = 50.7 ns, needed by 71.4 − 10 = 61.4 ns | **10.7 ns** (10 MHz: 25 ns) |
| `RDY` high before the CPU samples it | driven by U3, not left to the 3k3 pull-up | — |
| Data into U2 at the PHI2-fall capture | tDHW 10 − tPD 7.5 − 74HCT273 th 3 | **−0.5 ns on paper** |

A `-10` or `-15` GAL misses the `RDY` set-up at 14 MHz (65.7 ns with -15) and is fine at 10 MHz.
Fit the **-7**.

The capture hold is the same bet the host makes for its own SRAM (`/WR` ends one CPLD delay after
PHI2 falls) and its DUART. Here it is weaker still as a risk: while `RDY` holds the CPU, the bus
cycle is the same write over and over, nothing else decodes `$C1xx`, and nothing else drives
D0–D7, so the bus keeps the byte between the CPU's own drives, and U2 takes it again at every
PHI2 fall until state 3. The model lets the bus keep its last value once the CPU stops driving;
measure CAPTURE (U3 pin 22) against D0–D7 on the first board.

`RDY` is shared (the host's CPLD pulls it for the DUART, the W65C02S pulls it during `WAI`). U3
enables it only through `EN`: during PHI2 high of a card write, and once its counter runs. While
PHI2 is low the address bits change one at a time; with an ungated enable the decoder would pull
`RDY` low for a few ns on the way from `$11xx` to `$C0xx`, and the 3k3 pull-up needs ~80–150 ns to
bring it back. `tools/sim_card.py` shows exactly that failure for the ungated equation (DUART
cycles stretched) and none for the real one.

## Card → YM2149

Yamaha pp. 10–11: address/data set-up 300 ns, hold 80 ns, write pulse 300 ns – 10 µs.

| Requirement | r1 |
|---|---|
| DA0–DA7 and BC2 stable 300 ns before `BDIR` | last capture by state 3, `BDIR` at state 4: ≥ 1 T = 543 ns; BC2 = A0 from the start of the cycle |
| `BDIR` 300 ns – 10 µs | 1085 ns, from X1 only: no CPU clock, NMI, single-step or stopped PHI2 can stretch it |
| hold 80 ns | U2 does not capture after state 2; A0 holds until the CPU moves, ≥ 1 T after `BDIR` falls |
| chip address | `/A9` = GND, `A8` = +5 V; the driver masks the register number to 0–15 (DA4–DA7 = 0) |

BC1 is grounded and nothing reads the PSG: `BDIR BC2 BC1` is only ever `000`/`010` (inactive),
`100` (latch address) or `110` (write). The PSG's data pins never drive, so U2's outputs never
fight them.

## Clock

X1 is a 5 V 1.8432 MHz can into the YM2149 with `SEL` high (no divider), inside Yamaha's 2 MHz limit
for that mode — the VIA port's 2.000 MHz can sat on the limit. R1 and R2 (33 Ω) split it to U1 and U3.
Tone: f = 1843200 / (16 × period) = 115200 / period. Pin 1 of the can must be an active-high enable
or NC; it is tied to +5 V.

## Reset and power

`/RESET` clears U2 and the YM2149 and takes U3 out of any request; the ATF22V10C clears its
registers at power-up. R3/R4 hold `BDIR` and `CAPTURE` low while U3 is blank or out of its socket.

Budget **250 mA** at 5 V: the ATF22V10C-7 alone is up to ~130 mA (power-down disabled), the YM2149
and 74HCT273 the rest. Not measured.

## Audio

Unchanged from rosco_6502-audio: 1 kΩ loads, A → L and C → R through 10 kΩ, B → both through 22 kΩ (about
−6.9 dB), 10 µF coupling, 100 kΩ bleeders. Passive line out, ≥ 10 kΩ load; not for headphones.
Ground pour on both layers, not split.
