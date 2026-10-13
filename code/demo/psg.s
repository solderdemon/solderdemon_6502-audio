.setcpu "65C02"
.export psg_init, psg_write, psg_silence, psg_test_chord

; SolderDemon 6502 Audio r1: the YM2149F behind a hardware sequencer (U3).
; A store to PSG_REG latches a register number, a store to PSG_DATA writes
; that register. The card holds the CPU with RDY for ~4 us per store and
; times the PSG strobe itself: no delays, any CPU clock. Both repeat over
; $C100-$C1FF (A0 picks one); reads of the page do nothing.
PSG_REG  = $C100
PSG_DATA = $C101

.segment "CODE"
; A = value, X = register 0..13 (14/15 are the PSG's I/O ports).
; Preserves A, X, Y and the flags. The register number is masked to 0..15:
; a number with a bit set in 4..7 would not address this chip at all.
; IRQs are off between the two stores, so an IRQ handler that uses the PSG
; cannot slip its own register number in between.
.proc psg_write
    php
    sei
    pha
    txa
    and #$0F
    sta PSG_REG
    pla
    sta PSG_DATA
    plp
    rts
.endproc

; All registers 13..0 to zero, then tones and noise off with the I/O ports
; as inputs (R7 = $3F). /RESET has already done the same in hardware.
.proc psg_init
    php
    pha
    phx
    ldx #13
    lda #0
clear:
    jsr psg_write
    dex
    bpl clear
    ldx #7
    lda #$3F
    jsr psg_write
    plx
    pla
    plp
    rts
.endproc

.proc psg_silence
    php
    pha
    phx
    ldx #8
    lda #0
    jsr psg_write
    inx
    jsr psg_write
    inx
    jsr psg_write
    plx
    pla
    plp
    rts
.endproc

; C4-E4-G4 chord. X1 is 1.8432 MHz: f = 1843200 / (16 * period) = 115200 / period.
; A left / B centre / C right. Returns with the chord still sounding.
.proc psg_test_chord
    jsr psg_init
    php
    pha
    phx
    ldx #0
next:
    lda chord,x
    jsr psg_write
    inx
    cpx #14
    bne next
    plx
    pla
    plp
    rts
chord:
    .byte $B8,$01, $5D,$01, $26,$01, $00,$38
    .byte $0A,$0A,$0A, $00,$00,$00
.endproc
