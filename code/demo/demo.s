.setcpu "65C02"
.import psg_test_chord
.export start
.segment "CODE"
start:
    jsr psg_test_chord
    rts
