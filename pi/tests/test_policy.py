from desk_presence.config import PresenceSettings, SecondPersonSettings
from desk_presence.policy import LockPolicy
from desk_presence.presence import Observation, State

SEATED, STOOD, AWAY, DARK = State.SEATED, State.STOOD_UP, State.AWAY, State.TOO_DARK


def run(states, policy=None, pc_locked=None, paused=False, step=0.5, second=False):
    """Feed one observation every `step` seconds; return [(time, action)]."""
    policy = policy or LockPolicy(PresenceSettings(), SecondPersonSettings())
    out = []
    for i, s in enumerate(states):
        t = i * step
        for d in policy.step(Observation(t, s, second and s is not AWAY), pc_locked, paused):
            out.append((t, d.action))
    return out


def test_locks_after_five_seconds_away():
    actions = run([SEATED] * 4 + [AWAY] * 12)
    assert actions == [(7.0, "lock")]  # away from 2.0, locks at 7.0


def test_locks_only_once_per_absence():
    assert len(run([SEATED] * 4 + [AWAY] * 60)) == 1


def test_locks_again_after_coming_back_and_leaving():
    actions = run([SEATED] * 4 + [AWAY] * 12 + [SEATED] * 4 + [AWAY] * 12)
    assert [a for _, a in actions] == ["lock", "lock"]


def test_standing_and_stretching_never_lock():
    assert run([SEATED] * 4 + [STOOD] * 100) == []


def test_short_absence_does_not_lock():
    assert run([SEATED] * 4 + [AWAY] * 8 + [SEATED] * 4 + [AWAY] * 8) == []


def test_one_frame_flicker_while_away_does_not_restart_countdown():
    actions = run([SEATED] * 4 + [AWAY] * 5 + [SEATED] + [AWAY] * 10)
    assert actions == [(7.0, "lock")]


def test_never_locks_before_seeing_you_seated():
    assert run([AWAY] * 40) == []
    assert run([STOOD] * 4 + [AWAY] * 40) == []


def test_darkness_restarts_the_countdown():
    actions = run([SEATED] * 4 + [AWAY] * 6 + [DARK] * 20 + [AWAY] * 12)
    assert actions == [(20.0, "lock")]  # light back at 15.0, locks 5 seconds later


def test_no_lock_while_paused():
    assert run([SEATED] * 4 + [AWAY] * 20, paused=True) == []


def test_no_key_press_when_pc_already_locked():
    assert run([SEATED] * 4 + [AWAY] * 20, pc_locked=True) == []


def test_locks_without_the_pc_helper():
    assert run([SEATED] * 4 + [AWAY] * 12, pc_locked=None) == [(7.0, "lock")]


def test_second_person_warns_once_by_default():
    actions = run([SEATED] * 20, second=True)
    assert actions == [(2.0, "warn_second_person")]


def test_second_person_can_lock_if_turned_on():
    policy = LockPolicy(PresenceSettings(), SecondPersonSettings(action="lock"))
    actions = run([SEATED] * 20, policy=policy, second=True)
    assert actions == [(2.0, "warn_second_person"), (2.0, "lock")]


def test_second_person_off():
    policy = LockPolicy(PresenceSettings(), SecondPersonSettings(action="off"))
    assert run([SEATED] * 20, policy=policy, second=True) == []
