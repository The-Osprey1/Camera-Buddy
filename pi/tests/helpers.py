from desk_presence.pose import Frame, Person


def person(shoulder_y=0.6, x=0.5, scale=1.0, score=0.9):
    """A body whose head is above its shoulders, centred on x."""
    pts = [(0.0, 0.0, 0.0)] * 17
    head_y = shoulder_y - 0.2 * scale
    pts[0] = (x, head_y, score)  # nose
    pts[1] = (x - 0.03 * scale, head_y - 0.02, score)
    pts[2] = (x + 0.03 * scale, head_y - 0.02, score)
    pts[3] = (x - 0.06 * scale, head_y, score)
    pts[4] = (x + 0.06 * scale, head_y, score)
    pts[5] = (x - 0.15 * scale, shoulder_y, score)
    pts[6] = (x + 0.15 * scale, shoulder_y, score)
    return Person(pts)


def frame(t, people=(), lux=100.0):
    return Frame(t, list(people), lux)
