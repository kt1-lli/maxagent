#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxagent/pack.py 导入导出功能的端到端测试。

覆盖：
- export_pack 写出合法 zip + manifest.json
- parse_pack 能识别新增 / 已存在 / 非法
- import_pack 同名跳过 / 覆盖路径
- 安全：拒绝路径穿越、空选择、坏 zip
- 不会泄露 Profile / API Key（不读取 config.json）
"""

from __future__ import absolute_import
from __future__ import print_function

import io
import json
import os
import zipfile

import pytest


@pytest.fixture(autouse=True)
def _isolate_dirs(tmp_path, monkeypatch):
    """把工具 / 技能 / 规则 / config 目录全部隔离到 tmp_path，
    防止测试污染本机真实数据。"""
    # 工具与规则有 set_xxx_override 接口
    from maxagent import user_tools_loader as utl
    from maxagent import user_rules_loader as url_mod
    tools_dir = tmp_path / 'user_tools'
    rules_dir = tmp_path / 'user_rules'
    tools_dir.mkdir()
    rules_dir.mkdir()
    utl.set_user_tools_dir_override(str(tools_dir))
    url_mod.set_user_rules_dir_override(str(rules_dir))

    # 技能没有 override，但 SkillManager 接受 base_dir 构造参数；
    # pack 模块直接 SkillManager() 走默认路径——这里通过 monkeypatch
    # get_config_dir 重定向。
    from maxagent import skills as skills_mod
    skills_root = tmp_path / 'cfg'
    skills_root.mkdir()
    (skills_root / 'skills').mkdir()
    monkeypatch.setattr(skills_mod, 'get_config_dir', lambda: str(skills_root))

    yield

    utl.set_user_tools_dir_override(None)
    url_mod.set_user_rules_dir_override(None)


def _seed_tool(name='greet_user'):
    from maxagent import user_tools_loader as utl
    code = (
        'from maxagent.tools.registry import tool\n'
        '\n'
        '@tool(name="{}", description="say hi")\n'
        'def _impl():\n'
        '    return "hi"\n'
    ).format(name)
    utl.write_tool(name, code, {'description': 'say hi'})
    return name


def _seed_skill(name='标准导出'):
    from maxagent.skills import Skill, SkillManager
    sk = Skill(
        name=name,
        description='把当前选中物体导出为 FBX',
        trigger_keywords=['导出', 'fbx'],
        instructions='1. 检查选中\n2. 调 export\n',
    )
    SkillManager().save(sk, overwrite=True)
    return name


def _seed_rule(rule_id='r_color_uppercase'):
    from maxagent import user_rules_loader as url_mod
    url_mod.write_rule(rule_id, {
        'title': 'rt.Color 必须大写',
        'content': 'pymxs 颜色构造器必须大写: rt.Color(...)',
        'tags': ['material'],
    })
    return rule_id


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
class TestExport:
    def test_export_empty_raises(self, tmp_path):
        from maxagent import pack
        out = tmp_path / 'empty.maxagent-pack'
        with pytest.raises(pack.PackError):
            pack.export_pack(str(out), tool_names=[], skill_names=[],
                             rule_ids=[])
        assert not out.exists()

    def test_export_creates_valid_zip(self, tmp_path):
        from maxagent import pack
        _seed_tool('t_export_demo')
        _seed_skill('技能 A')
        _seed_rule('r_export_demo')
        out = tmp_path / 'a.maxagent-pack'
        res = pack.export_pack(
            str(out),
            tool_names=['t_export_demo'],
            skill_names=['技能 A'],
            rule_ids=['r_export_demo'],
            pack_name='测试包',
            author='alice',
            description='hello',
        )
        assert out.exists()
        # 必须是合法 zip
        assert zipfile.is_zipfile(str(out))
        # manifest 内容正确
        with zipfile.ZipFile(str(out), 'r') as zf:
            names = zf.namelist()
            assert 'manifest.json' in names
            mani = json.loads(zf.read('manifest.json').decode('utf-8'))
            assert mani['name'] == '测试包'
            assert mani['author'] == 'alice'
            assert mani['description'] == 'hello'
            assert 't_export_demo' in mani['tools']
            assert '技能 A' in mani['skills']
            assert 'r_export_demo' in mani['rules']
            # 工具 .py 与 .meta.json 都进了包
            assert 'user_tools/t_export_demo.py' in names
            assert 'user_tools/t_export_demo.meta.json' in names
            # 规则 JSON
            assert 'user_rules/r_export_demo.json' in names
            # 技能 JSON：文件名经过 _safe_filename 清洗
            assert any(n.startswith('skills/') and n.endswith('.json')
                       for n in names)

        # 返回值合理
        assert res['tools'] == ['t_export_demo']
        assert res['rules'] == ['r_export_demo']
        assert res['size'] > 0

    def test_export_does_not_include_api_key(self, tmp_path):
        """安全断言：包里绝对不能出现任何 'api_key' / 'authorization'
        / 'profile' 字段，避免误把敏感信息打包带走。"""
        from maxagent import pack
        _seed_tool('t_safe')
        _seed_rule('r_safe')
        out = tmp_path / 'safe.maxagent-pack'
        pack.export_pack(
            str(out),
            tool_names=['t_safe'],
            skill_names=[],
            rule_ids=['r_safe'],
        )
        with zipfile.ZipFile(str(out), 'r') as zf:
            for nm in zf.namelist():
                content = zf.read(nm).decode('utf-8', errors='replace').lower()
                # 工具示例里没有这些关键字
                assert 'api_key' not in content
                assert 'authorization' not in content
                assert 'profile' not in content


# ---------------------------------------------------------------------- #
# 解析（parse_pack）
# ---------------------------------------------------------------------- #
class TestParse:
    def _round_trip_export(self, tmp_path, tool='t_p', skill='S1', rule='r_p'):
        from maxagent import pack
        _seed_tool(tool)
        _seed_skill(skill)
        _seed_rule(rule)
        out = tmp_path / 'p.maxagent-pack'
        pack.export_pack(
            str(out),
            tool_names=[tool],
            skill_names=[skill],
            rule_ids=[rule],
        )
        return str(out)

    def test_parse_basic(self, tmp_path):
        from maxagent import pack
        path = self._round_trip_export(tmp_path)
        parsed = pack.parse_pack(path)
        assert isinstance(parsed['manifest'], dict)
        assert len(parsed['tools']) == 1
        assert len(parsed['skills']) == 1
        assert len(parsed['rules']) == 1
        # 同名时本机已有 → existing
        assert parsed['tools'][0]['status'] == 'existing'
        assert parsed['skills'][0]['status'] == 'existing'
        assert parsed['rules'][0]['status'] == 'existing'

    def test_parse_new_when_local_empty(self, tmp_path):
        from maxagent import pack
        from maxagent import user_tools_loader as utl
        from maxagent import user_rules_loader as url_mod
        from maxagent.skills import SkillManager
        # 先导出
        path = self._round_trip_export(tmp_path, 'tparse', 'sparse', 'rparse')
        # 把本机数据全删掉，再 parse → 状态应该都是 new
        utl.delete_user_tool('tparse')
        url_mod.delete_rule('rparse')
        sk = SkillManager().get('sparse')
        if sk is not None:
            SkillManager().delete(sk.name)
        parsed = pack.parse_pack(path)
        assert parsed['tools'][0]['status'] == 'new'
        assert parsed['skills'][0]['status'] == 'new'
        assert parsed['rules'][0]['status'] == 'new'

    def test_parse_rejects_path_traversal(self, tmp_path):
        from maxagent import pack
        bad = tmp_path / 'evil.maxagent-pack'
        with zipfile.ZipFile(str(bad), 'w') as zf:
            zf.writestr('manifest.json', json.dumps({
                'schema_version': 1,
                'name': 'evil',
            }))
            zf.writestr('../escape.py', 'print("pwn")')
        with pytest.raises(pack.PackError):
            pack.parse_pack(str(bad))

    def test_parse_rejects_missing_manifest(self, tmp_path):
        from maxagent import pack
        bad = tmp_path / 'no_manifest.maxagent-pack'
        with zipfile.ZipFile(str(bad), 'w') as zf:
            zf.writestr('user_tools/foo.py', 'x = 1')
        with pytest.raises(pack.PackError):
            pack.parse_pack(str(bad))

    def test_parse_rejects_bad_zip(self, tmp_path):
        from maxagent import pack
        bad = tmp_path / 'broken.maxagent-pack'
        bad.write_bytes(b'not a zip at all')
        with pytest.raises(pack.PackError):
            pack.parse_pack(str(bad))


# ---------------------------------------------------------------------- #
# 导入
# ---------------------------------------------------------------------- #
class TestImport:
    def test_import_skipped_when_exists(self, tmp_path):
        from maxagent import pack
        _seed_tool('t_imp')
        _seed_rule('r_imp')
        out = tmp_path / 'i.maxagent-pack'
        pack.export_pack(
            str(out),
            tool_names=['t_imp'],
            skill_names=[],
            rule_ids=['r_imp'],
        )
        # 默认不覆盖 → 全部跳过
        summary = pack.import_pack(str(out), overwrite=False)
        assert 't_imp' in summary['tools']['skipped']
        assert 'r_imp' in summary['rules']['skipped']

    def test_import_overwrite_replaces(self, tmp_path):
        from maxagent import pack
        from maxagent import user_rules_loader as url_mod
        _seed_rule('r_over')
        out = tmp_path / 'o.maxagent-pack'
        pack.export_pack(str(out), rule_ids=['r_over'])
        # 修改本机规则的 title
        url_mod.write_rule('r_over', {
            'title': '改过的标题', 'content': '原内容',
        })
        summary = pack.import_pack(str(out), overwrite=True)
        assert 'r_over' in summary['rules']['overwritten']
        # 导入后 title 被复原（包里的版本覆盖本机）
        rule = url_mod.get_rule('r_over')
        assert rule['title'] == 'rt.Color 必须大写'

    def test_import_new_to_empty_local(self, tmp_path):
        from maxagent import pack
        from maxagent import user_tools_loader as utl
        from maxagent import user_rules_loader as url_mod
        # 在 A 机器导出
        _seed_tool('t_newimp')
        _seed_rule('r_newimp')
        out = tmp_path / 'n.maxagent-pack'
        pack.export_pack(
            str(out),
            tool_names=['t_newimp'],
            rule_ids=['r_newimp'],
        )
        # 把本机数据全删掉模拟"换了台电脑"
        utl.delete_user_tool('t_newimp')
        url_mod.delete_rule('r_newimp')
        # 导入
        summary = pack.import_pack(str(out))
        assert 't_newimp' in summary['tools']['imported']
        assert 'r_newimp' in summary['rules']['imported']
        # 本机能再次列出
        names = [it['name'] for it in utl.list_user_tools(include_meta=False)]
        assert 't_newimp' in names
        assert url_mod.get_rule('r_newimp') is not None

    def test_import_selected_subset(self, tmp_path):
        """selected_xxx=[] 应一个都不导入，None 应全部导入。"""
        from maxagent import pack
        from maxagent import user_tools_loader as utl
        from maxagent import user_rules_loader as url_mod
        _seed_tool('t_a')
        _seed_rule('r_a')
        out = tmp_path / 'sub.maxagent-pack'
        pack.export_pack(str(out), tool_names=['t_a'], rule_ids=['r_a'])

        # 全删除
        utl.delete_user_tool('t_a')
        url_mod.delete_rule('r_a')

        # 只选规则不选工具
        summary = pack.import_pack(
            str(out),
            selected_tools=[],
            selected_rules=['r_a'],
        )
        assert summary['tools']['imported'] == []
        assert 'r_a' in summary['rules']['imported']
        # 工具确实没被导入
        names = [it['name'] for it in utl.list_user_tools(include_meta=False)]
        assert 't_a' not in names
