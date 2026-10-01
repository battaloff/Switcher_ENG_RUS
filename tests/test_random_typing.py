"""Random typing with every feature on must never raise: an exception there means a key goes unanswered."""

import random

from switcher.config import Config
from switcher.layouts import EN, RU
from test_controller import FakeAI

KEYS = list("qwertyuiop[]asdfghjkl;'zxcvbnm,./`1234567890-=") + ["SPACE"] * 8 + ["BS"] * 3 + ["ENTER", "SHIFT2",
       "QUOTE", "AT", "DOUBLE", "SEL", "SWITCH", "UPPER"]


def test_fuzz(make_screen):
    rng = random.Random(1)
    for uz in (False, True):
        for round_ in range(150):
            config = Config()
            config.writes_uzbek = uz
            s = make_screen(config, layout=rng.choice([EN, RU]), ai=FakeAI("x") if round_ % 3 == 0 else None)
            for _ in range(rng.randint(5, 80)):
                k = rng.choice(KEYS)
                if k == "SPACE":
                    s.keys(" ")
                elif k == "BS":
                    s.keys("\b")
                elif k == "ENTER":
                    s.keys("\n")
                elif k == "QUOTE":
                    s.keys('"')
                elif k == "AT":
                    s.keys("@")
                elif k == "DOUBLE":
                    s.double_shift()
                elif k == "SEL":
                    s.selection = s.text[-rng.randint(1, 20):] if s.text else ""
                    s.controller.convert_selection()
                elif k == "SWITCH":
                    s.switch_layout(RU if s.layout == EN else EN)
                elif k == "UPPER":
                    s.keys(rng.choice("QWERTYASDFG"))
                elif k == "SHIFT2":
                    s.keys(rng.choice("!@#$%^&*()"))
                else:
                    s.keys(k)
