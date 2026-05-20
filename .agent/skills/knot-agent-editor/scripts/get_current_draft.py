#!/usr/bin/env python3
"""
功能1：查看当前对话智能体的草稿配置
用法：python get_current_draft.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))
from common import BASE_URL, get_env, get_auth_headers, parse_current_agent_id, api_post


def main():
    jwt_token, username = get_env()
    headers = get_auth_headers(jwt_token, username)

    # 从 JWT 解析 scene，并通过 agents/list 获取真实 agent_id
    agent_id = parse_current_agent_id(jwt_token, headers)

    # 调用 get_draft
    result = api_post(
        f"{BASE_URL}/openapi/v1/agents/get_draft",
        headers,
        {"agent_id": agent_id},
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
