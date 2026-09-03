#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Maya 绑定类工具子包。

为保持外部导入路径不变（maxagent.tools.maya.rigging），
rigging.py 依旧存在，会 re-export 本包内的全部工具。
"""

from __future__ import absolute_import

from .joints import (
    create_joint,
    create_child_joint,
    set_joint_radius,
    set_preferred_angle,
    orient_joint,
    mirror_joint_chain,
)
from .ik import (
    create_ik_handle,
    connect_fk_ik_chains,
    create_pole_vector,
    create_ikfk_switch,
)
from .controllers import (
    create_controller,
    parent_controller_hierarchy,
    create_offset_group,
    create_locators_along_chain,
    create_aligned_groups,
)
from .skin import (
    bind_skin,
    set_skin_weight,
)
from .constraints import (
    create_constraint,
    create_set_driven_key,
)
from .blendshape import (
    create_maya_blendshape,
    add_blendshape_target,
)

__all__ = [
    'create_joint',
    'create_child_joint',
    'set_joint_radius',
    'set_preferred_angle',
    'orient_joint',
    'mirror_joint_chain',
    'create_ik_handle',
    'connect_fk_ik_chains',
    'create_pole_vector',
    'create_ikfk_switch',
    'create_controller',
    'parent_controller_hierarchy',
    'create_offset_group',
    'create_locators_along_chain',
    'create_aligned_groups',
    'bind_skin',
    'set_skin_weight',
    'create_constraint',
    'create_set_driven_key',
    'create_maya_blendshape',
    'add_blendshape_target',
]
