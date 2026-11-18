# Audio r1 build and verification

## Board dimensions

100 × 72 mm, R4 corners, 2 layers, FR-4 1.6 mm and 1 oz copper. The socket
courtyard gap between U1 and U2/U3 is approximately 4.1 mm. Placement
coordinates are in `tools/audio_mechanics.py`. The 6502 and m68k cards have
different bus connector offsets and jack footprints.

## Assembly

Use the 6502 BOM. Fit low-profile DIP sockets for U1 (40 pins, 15.24 mm),
U2 (20 pins, 7.62 mm) and U3 (24 pins, 7.62 mm). X1 can be soldered
directly. Check the jack pinout and component height against the adjacent
slot. Check capacitor polarity, IC notches and oscillator pin 1 before power.

Program U3 with the 6502 JEDEC file. Its SHA-256 is recorded in
`release.json`. J3 is optional and wired in parallel with J2 as L, GND, R.
Connect either output to an amplifier line input or powered speakers.

## Board verification

1. Inspect soldering and check for a short between 5 V and ground before applying power.
2. Use a current-limited 5 V supply. Check the supply voltage at each IC and confirm the PSG clock is 1.8432 MHz.
3. Verify that the host starts, then exercise repeated register writes and resets.
4. Test channels A, B and C individually, then test noise, envelope and silence. A goes left, B goes to both outputs and C goes right. If J3 is fitted, test it with J2 unplugged.
5. Scope CAPTURE, data and BDIR at the intended host clock. See `docs/TIMING.md` for the calculated hold margin and test conditions.

## Rebuild

KiCad 10 Python with pcbnew, Java 25+, KICAD_CLI and KICAD_SHARE set for the machine.
Run the platform generator, its verify_design.py (and 6502 sim_card.py), route_card.py
with ROUTE_MODE=best, then run tools/make_outputs.py with KiCad Python from the repository root. The shared router
applies tools/brand identity. Do not rerun the obsolete m68k brand_project.py afterwards.
The generator clears all tracks; re-generation requires re-routing. .kicad_pro is preserved.

## Fabrication files

`manufacturing/solderdemon_6502-audio-r1-2026-11-12-gerbers.zip` contains Gerbers and Excellon drills.
`manufacturing/solderdemon_6502-audio-r1-2026-11-12-release.zip` includes those files plus the schematic,
BOM, positions, IPC netlist, PLD sources and JEDEC image, and this guide.
`release.json` records the source-file checksums.
