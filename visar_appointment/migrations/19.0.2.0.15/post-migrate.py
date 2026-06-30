# -*- coding: utf-8 -*-
"""Split del monolito visar_appointment en visar_base + visar_fsm."""


def migrate(cr, version):
    if not version:
        return
