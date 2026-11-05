from rangesum import range_sum


def test_small():
    assert range_sum(4) == 10


def test_zero():
    assert range_sum(0) == 0
