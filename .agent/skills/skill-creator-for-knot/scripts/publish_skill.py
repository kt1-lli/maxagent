#!/usr/bin/env python3
"""
Skill publisher - Upload or update a skill on the Knot marketplace.

Usage:
    python scripts/publish_skill.py <skill_folder_path> [--skill-id <ID>]

Examples:
    python scripts/publish_skill.py .agent/skills/my-skill
    python scripts/publish_skill.py .agent/skills/my-skill --skill-id 3562

Workflow:
    1. Parse SKILL.md frontmatter (name, description)
    2. Validate the skill via quick_validate
    3. Auto-fetch Knot API Token via HTTP (using KNOT_JWT_TOKEN + KNOT_USERNAME env vars)
    4. Package skill folder into a zip (in-memory)
    5. If --skill-id is provided, update that skill directly
    6. Otherwise, search managed skills by display_name:
       - Found exactly one match → update it
       - No match → create a new skill, then upload the file
       - Multiple matches → abort and ask user to specify --skill-id

Token is automatically obtained internally via the Knot config API.
No need to pass a token manually.
"""

import sys
import os
import re
import json
import base64
import zipfile
import tempfile
import argparse
import subprocess
from pathlib import Path

# Allow importing quick_validate from the same directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quick_validate import validate_skill


KNOT_HOST = "knot.woa.com"
BASE_URL = f"https://{KNOT_HOST}/apigw"
GET_CONFIG_URL = f"https://{KNOT_HOST}/apigw/api/v1/mcpport/get_config"
GET_TAGS_URL = f"https://{KNOT_HOST}/apigw/openapi/v1/skills/get_skill_tags"


def fetch_knot_api_token() -> str:
    """
    通过 curl 命令从 Knot 平台获取 API Token。
    使用 KNOT_JWT_TOKEN 和 KNOT_USERNAME 环境变量进行鉴权。
    """
    cmd = [
        "curl", "-s", "-k",  # -k 忽略 SSL 证书验证，解决企业内网证书链无法验证的问题
        GET_CONFIG_URL,
        "--header", f"X-Username: {os.environ.get('KNOT_USERNAME', '')}",
        "--header", "Content-Type: application/json",
        "-d", json.dumps({
            "jwt_token": os.environ.get("KNOT_JWT_TOKEN", ""),
            "for_knot_api_token": True,
        }),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        print("❌ Error: curl not found. Please ensure curl is installed.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: Failed to fetch token via curl: {e}", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"❌ Error: curl exited with code {result.returncode}: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"❌ Error: Failed to parse token response: {e}\nRaw output:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)

    # 响应结构：{"code": 0, "data": {"knot_api_token": "..."}}
    if data.get("code") != 0:
        print(f"❌ Error: Token fetch failed: code={data.get('code')}, msg={data.get('msg')}", file=sys.stderr)
        sys.exit(1)

    token = data.get("data", {}).get("knot_api_token", "")
    if not token:
        print(f"❌ Error: knot_api_token not found in response:\n{json.dumps(data, indent=2)}", file=sys.stderr)
        sys.exit(1)

    return token


def fetch_skill_tags(token: str) -> list:
    """
    获取所有可用的技能标签列表。

    Args:
        token: Knot API Token

    Returns:
        标签列表，每项包含 id、tag_name、display_name 等字段
    """
    import urllib.request
    import urllib.error
    import ssl

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        GET_TAGS_URL,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-knot-api-token": token,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP {e.code}: {body}", file=sys.stderr)
        return []
    except urllib.error.URLError as e:
        print(f"❌ Network error fetching tags: {e.reason}", file=sys.stderr)
        return []

    if result.get("code") != 0:
        print(f"❌ Failed to fetch tags: code={result.get('code')}, msg={result.get('msg')}", file=sys.stderr)
        return []

    return result.get("data", [])


def validate_tag_ids(tag_ids: list, token: str) -> list:
    """
    验证 tag_ids 是否合法，返回合法的 tag_ids 列表。
    若存在非法 ID，打印警告并跳过。

    Args:
        tag_ids: 用户传入的标签 ID 字符串列表
        token:   Knot API Token

    Returns:
        验证后合法的 tag_ids 列表
    """
    if not tag_ids:
        return []

    print("🏷️  Fetching available skill tags...")
    tags = fetch_skill_tags(token)
    if not tags:
        print("⚠️  Could not fetch tags, skipping tag validation.")
        return tag_ids

    valid_ids = {str(t.get("id")) for t in tags}
    id_to_name = {str(t.get("id")): t.get("display_name") or t.get("tag_name", "") for t in tags}

    result = []
    for tid in tag_ids:
        if tid in valid_ids:
            result.append(tid)
            print(f"   ✅ Tag {tid} ({id_to_name[tid]})")
        else:
            print(f"   ⚠️  Tag ID '{tid}' is not valid, skipping.")

    if not result:
        print("⚠️  No valid tag IDs provided, tags will not be set.")

    return result


def list_available_tags(token: str):
    """打印所有可用标签列表。"""
    tags = fetch_skill_tags(token)
    if not tags:
        print("暂无可用标签。")
        return

    print(f"共 {len(tags)} 个可用标签：\n")
    print(f"{'ID':<6} {'英文标识':<20} {'显示名称'}")
    print("-" * 50)
    for tag in tags:
        tag_id = tag.get("id", "-")
        tag_name = tag.get("tag_name", "-")
        display_name = tag.get("display_name", "-")
        print(f"{tag_id:<6} {tag_name:<20} {display_name}")


def parse_frontmatter(skill_path):
    """
    Extract name and description from SKILL.md YAML frontmatter.

    Supports all YAML scalar styles including multi-line folded (>) and
    literal (|) blocks.

    Returns:
        (name, description) tuple, or (None, None) on failure
    """
    try:
        import yaml
    except ImportError:
        yaml = None

    skill_md = Path(skill_path) / "SKILL.md"
    if not skill_md.exists():
        print(f"❌ Error: SKILL.md not found in {skill_path}")
        return None, None

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        print("❌ Error: SKILL.md missing YAML frontmatter")
        return None, None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        print("❌ Error: Invalid frontmatter format")
        return None, None

    frontmatter_str = match.group(1)

    # 优先使用 PyYAML 解析，完整支持多行折叠语法（> 和 |）
    if yaml is not None:
        try:
            data = yaml.safe_load(frontmatter_str)
            if isinstance(data, dict):
                name = data.get("name", "").strip() if data.get("name") else None
                desc = data.get("description", "") or ""
                # 折叠/字面量块会引入换行，统一折叠为单行
                desc = " ".join(desc.split())
                if not name:
                    print("❌ Error: 'name' not found in frontmatter")
                    return None, None
                return name, desc
        except yaml.YAMLError as e:
            print(f"⚠️  YAML parse warning: {e}, falling back to line-by-line parser")

    # 降级：逐行解析（仅支持单行 value）
    name = desc = None
    for line in frontmatter_str.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "name":
                name = v
            elif k == "description" and v not in (">", "|", ">-", "|-", ">+", "|+"):
                desc = v

    if not name:
        print("❌ Error: 'name' not found in frontmatter")
        return None, None

    return name, desc or ""


def build_zip_base64(skill_path, skill_name):
    """
    Package the skill directory into a zip file and return its base64-encoded content.

    The zip structure is: <skill_name>/<files...>
    Skips __pycache__ directories and .pyc files.
    """
    skill_path = Path(skill_path).resolve()
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()

    try:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(skill_path):
                # 跳过 __pycache__
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".pyc"):
                        continue
                    fp = Path(root) / f
                    arcname = os.path.join(skill_name, fp.relative_to(skill_path))
                    zf.write(fp, arcname)

        zip_size = os.path.getsize(tmp.name)
        zip_size_mb = zip_size / (1024 * 1024)
        if zip_size_mb > 10:
            print(f"❌ Error: Zip file size ({zip_size_mb:.1f}MB) exceeds 10MB limit")
            return None

        print(f"  打包完成，zip 大小: {zip_size / 1024:.1f} KB")

        with open(tmp.name, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        return b64
    finally:
        os.remove(tmp.name)


def api_request(base_url, endpoint, payload, token):
    """
    Send a POST request to the Knot API.

    Returns:
        Parsed JSON response dict, or None on failure
    """
    import urllib.request
    import urllib.error
    import ssl

    url = f"{base_url}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-knot-api-token": token,
        },
        method="POST",
    )

    # 创建忽略 SSL 证书验证的上下文，解决企业内网证书链无法验证的问题
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"❌ HTTP {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        print(f"❌ Network error: {e.reason}")
        return None


def get_managed_skills(token, keyword=None):
    """Fetch the list of managed skills from Knot, optionally filtered by keyword."""
    payload = {"category": "managed"}
    if keyword:
        payload["keyword"] = keyword
    resp = api_request(BASE_URL, "/openapi/v1/skills/get", payload, token)
    if resp and resp.get("code") == 0:
        data = resp.get("data", {})
        # 响应格式为 {"list": [...], "total_count": N}
        if isinstance(data, dict):
            return data.get("list", [])
        # 兼容旧格式：data 直接为列表
        return data if isinstance(data, list) else []
    print(f"❌ Failed to fetch managed skills: {resp}")
    return None


def find_skill_by_name(skills, display_name):
    """Find skills matching the given display_name. Returns a list of matches."""
    return [s for s in skills if s.get("display_name") == display_name]


def create_skill(name, description, token, tag_ids=None):
    """Create a new skill (metadata only) and return the new skill ID."""
    payload = {"display_name": name, "description": description}
    if tag_ids:
        payload["tag_ids"] = tag_ids
    resp = api_request(
        BASE_URL,
        "/openapi/v1/skills/add_without_file",
        payload,
        token,
    )
    if resp and resp.get("code") == 0:
        new_id = resp.get("data", {}).get("id")
        return str(new_id) if new_id else None
    print(f"❌ Failed to create skill: {resp}")
    return None


def update_skill_file(skill_id, file_data_b64, file_name, token):
    """Upload/update the skill zip file."""
    resp = api_request(
        BASE_URL,
        "/openapi/v1/skills/update_file",
        {"id": str(skill_id), "file_data": file_data_b64, "file_name": file_name},
        token,
    )
    if resp and resp.get("code") == 0:
        return True
    print(f"❌ Failed to update skill file: {resp}")
    return False


def publish_skill(skill_path, token, skill_id=None, tag_ids=None):
    """
    Main publish workflow:
    1. Validate the skill
    2. Parse frontmatter for name/description
    3. Package into zip (base64)
    4. Create or update the skill on Knot

    Args:
        skill_path: Path to skill directory
        token:      Knot API token
        skill_id:   Optional explicit skill ID (skip auto-detection)
        tag_ids:    Optional list of tag ID strings to set on the skill

    Returns:
        skill_id (str) on success, None on failure
    """
    skill_path = Path(skill_path).resolve()

    # Step 1: Validate
    print("🔍 Validating skill...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"❌ Validation failed: {message}")
        return None
    print(f"✅ {message}\n")

    # Step 2: Parse frontmatter
    name, description = parse_frontmatter(skill_path)
    if not name:
        return None
    print(f"📋 Skill: {name}")
    print(f"   Description: {description[:80]}{'...' if len(description) > 80 else ''}\n")

    # Step 3: Package
    print("📦 Packaging skill...")
    b64 = build_zip_base64(skill_path, name)
    if not b64:
        return None
    print("✅ Skill packaged successfully\n")

    file_name = f"{name}.zip"

    # Step 4: Upload
    if skill_id:
        # Explicit skill ID provided - update directly (tag_ids not applicable for updates)
        print(f"🚀 Updating skill (ID: {skill_id})...")
        if update_skill_file(skill_id, b64, file_name, token):
            print(f"✅ Skill updated successfully!")
            print(f"   View at: https://{KNOT_HOST}/skills/detail/{skill_id}")
            return str(skill_id)
        return None

    # No skill ID - auto-detect by name
    print("🔍 Searching for existing skill by name...")
    skills = get_managed_skills(token, keyword=name)
    if skills is None:
        return None

    matches = find_skill_by_name(skills, name)

    if len(matches) == 1:
        # Found exactly one match - update it (tag_ids not applicable for updates)
        sid = str(matches[0]["id"])
        print(f"   Found existing skill (ID: {sid})")
        print(f"🚀 Updating skill...")
        if update_skill_file(sid, b64, file_name, token):
            print(f"✅ Skill updated successfully!")
            print(f"   View at: https://{KNOT_HOST}/skills/detail/{sid}")
            return sid
        return None

    elif len(matches) == 0:
        # No match - create new skill
        print("   No existing skill found, creating new one...")
        # 首次创建时才校验并使用 tag_ids
        if not tag_ids:
            print("❌ Error: --tag-ids is required when creating a new skill.")
            print("   Use --list-tags to see available tag IDs, then re-run with --tag-ids <ID1,ID2,...>")
            return None
        validated_tag_ids = validate_tag_ids(tag_ids, token)
        if not validated_tag_ids:
            print("❌ Error: No valid tag IDs provided. Use --list-tags to see available tags.")
            return None
        new_id = create_skill(name, description, token, tag_ids=validated_tag_ids)
        if not new_id:
            return None
        print(f"   Created skill (ID: {new_id})")
        print(f"🚀 Uploading skill file...")
        if update_skill_file(new_id, b64, file_name, token):
            print(f"✅ Skill created and uploaded successfully!")
            print(f"   View at: https://{KNOT_HOST}/skills/detail/{new_id}")
            return new_id
        return None

    else:
        # Multiple matches - ambiguous
        print(f"⚠️  Found {len(matches)} skills with name '{name}':")
        for s in matches:
            print(f"     ID={s['id']}  created={s.get('created_at', 'N/A')}")
        print("   Please specify --skill-id to resolve ambiguity.")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Publish a skill to the Knot marketplace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s .agent/skills/my-skill
  %(prog)s .agent/skills/my-skill --skill-id 3562
  %(prog)s .agent/skills/my-skill --tag-ids 1,2,3
  %(prog)s --list-tags
        """,
    )
    parser.add_argument("skill_path", nargs="?", help="Path to the skill directory")
    parser.add_argument("--skill-id", help="Explicit skill ID to update (skip auto-detection)")
    parser.add_argument(
        "--tag-ids",
        default="",
        help="Comma-separated tag IDs to set on the skill, e.g. --tag-ids 1,2,3. "
             "Use --list-tags to see available tags.",
    )
    parser.add_argument(
        "--list-tags",
        action="store_true",
        help="List all available skill tags (ID + name) and exit",
    )

    args = parser.parse_args()

    # 自动从 Knot 平台获取 Token，无需用户传入
    print("🔑 Fetching Knot API token...")
    token = fetch_knot_api_token()
    print("✅ Token fetched successfully\n")

    # 若只是列出标签，则获取并打印后退出
    if args.list_tags:
        list_available_tags(token)
        return

    if not args.skill_path:
        parser.error("skill_path is required unless --list-tags is specified")

    # 解析标签 ID 列表
    tag_ids = None
    if args.tag_ids:
        tag_ids = [t.strip() for t in args.tag_ids.split(",") if t.strip()]

    print(f"🚀 Publishing skill: {args.skill_path}")
    if args.skill_id:
        print(f"   Target skill ID: {args.skill_id}")
    if tag_ids:
        print(f"   Tag IDs: {tag_ids}")
    print()

    result = publish_skill(args.skill_path, token, args.skill_id, tag_ids=tag_ids)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
