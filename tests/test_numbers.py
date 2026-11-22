from speakerphone.numbers import normalise


def d(raw):
    n = normalise(raw)
    return n.dial if n.ok else None


def test_israeli_local():
    assert d("02 434 5678") == "+97224345678"
    assert d("054-123-4567") == "+972541234567"
    assert d("077 123 4567") == "+972771234567"
    assert d("072 1234567") == "+972721234567"
    assert d("03 1234567") == "+97231234567"


def test_already_e164_or_972():
    assert d("+972 54 123 4567") == "+972541234567"
    assert d("972541234567") == "+972541234567"
    assert d("+972 054 123 4567") == "+972541234567"


def test_international():
    assert d("+1 212 555 0100") == "+12125550100"
    assert d("0044 20 7946 0000") == "+442079460000"


def test_short_codes_and_star():
    assert d("101") == "101"
    assert d("*2700") == "*2700"
    assert normalise("101").kind == "short_code"


def test_extension():
    assert d("6009") == "6009"


def test_wrong_length_rejected():
    assert d("054 123 456") is None  # 9 digits for a mobile
    assert d("02 434 56789") is None  # 10 digits for a landline
    assert d("54 123 4567") is None  # no leading 0


def test_readback():
    assert normalise("054 123 4567").readback.startswith("plus nine seven two, five four, one two three")


def test_speak_number_local_groups():
    from speakerphone.phone import speak_number
    assert speak_number("+972541234567") == "zero five four, one two three, four five six seven"
    assert speak_number("+97224345678") == "zero two, four three four, five six seven eight"
    assert speak_number("6009") == "six zero zero nine"
