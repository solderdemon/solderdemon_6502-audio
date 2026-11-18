"""Shared r1 audio-card mechanics, viewed with bus edge down (millimetres).

Bus pad offsets remain platform-specific. Audio connectors retain their proven footprints.
"""
WIDTH, HEIGHT, CORNER = 100.0, 72.0, 4.0
REV, DATE = 'r1', '2026-11-12'
# Courtyard top-left anchors; U1 to U2/U3 clearance >= 4.0 mm with DIP sockets.
LAYOUT = {
    'U1': (37, 2, 90), 'U3': (25, 24.5, 90), 'U2': (62, 24.5, 90),
    'X1': (59, 38, 0), 'C1': (32, 6, 270), 'C2': (57.4, 25.5, 270),
    'C3': (20.5, 25.5, 270), 'BULK': (90, 25.5, 0),
    'LEFT_CAP': (3, 27, 0), 'RIGHT_CAP': (3, 38, 0),
    'J3': (17, 33, 0), 'R12': (12, 27, 90), 'R13': (12, 38, 90),
    'R1': (76, 38, 90), 'R2': (80.5, 38, 90), 'R3': (85, 38, 90),
    'R4': (89.5, 38, 90),
    'R5': (21, 56, 0), 'R6': (33, 56, 0), 'R7': (45, 56, 0),
    'R8': (21, 51, 0), 'R9': (33, 51, 0), 'R10': (45, 51, 0),
    'R11': (57, 52, 0),
}

def layout(platform):
    out = dict(LAYOUT)
    if platform == 'm68k':
        aliases = {'BULK':'C6', 'LEFT_CAP':'C7', 'RIGHT_CAP':'C8'}
        out.update(U4=(25,38,90), C4=(20,43,270), C5=(53,39,270))
    else:
        aliases = {'BULK':'C5', 'LEFT_CAP':'C6', 'RIGHT_CAP':'C7'}
        out.update(C4=(53,39,270))
    return {aliases.get(k,k):v for k,v in out.items()}

def finish_labels(board, pcb):
    """Silkscreen only: readable IC references in the new gaps, outside socket bodies."""
    removed=[]
    for ref in ('U1','U2','U3','U4','X1'):
        f=board.FindFootprintByReference(ref)
        if f is None:continue
        for g in list(f.GraphicalItems()):
            if isinstance(g,pcb.PCB_TEXT) and g.GetText()=='${REFERENCE}':
                f.Remove(g);removed.append(g)
        bb=f.GetCourtyard(pcb.F_CrtYd).BBox()
        x=pcb.ToMM(bb.GetCenter().x)
        y=pcb.ToMM(bb.GetBottom())+1.3 if ref=='U1' else pcb.ToMM(bb.GetTop())-1.2
        t=f.Reference();t.SetPosition(pcb.VECTOR2I(pcb.FromMM(x),pcb.FromMM(y)))
        t.SetTextAngleDegrees(0);t.SetTextSize(pcb.VECTOR2I(pcb.FromMM(1),pcb.FromMM(1)));t.SetTextThickness(pcb.FromMM(.15))
    # Discard an obsolete header label left by the older rotated placement.
    old_labels=[d for d in board.GetDrawings() if isinstance(d,pcb.PCB_TEXT) and d.GetText()=='GND' and pcb.ToMM(d.GetPosition().x)<20]
    for d in old_labels:board.Remove(d)
    removed += old_labels
    return removed  # Keep removed SWIG objects alive through SaveBoard.
