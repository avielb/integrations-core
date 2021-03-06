# (C) Datadog, Inc. 2019-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
from .__about__ import __version__
from .cilium import CiliumCheck

__all__ = ['__version__', 'CiliumCheck']
