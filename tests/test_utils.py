import pytest

from lib.utils import whetherWin


@pytest.mark.parametrize("radiant_win,slot,expected", [
    (True, 0, True), (True, 127, True), (True, 128, False),
    (False, 0, False), (False, 127, False), (False, 255, True),
])
def test_win_follows_player_side(radiant_win, slot, expected) -> None:
    assert whetherWin(radiant_win, slot) is expected
