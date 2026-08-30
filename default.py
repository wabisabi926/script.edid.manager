import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'lib'))

import xbmcaddon  # noqa: E402

ADDON = xbmcaddon.Addon()


def run():
    pass


if __name__ == '__main__':
    run()
