"""技能导入工具 (v2 — 移除 npx openskills 依赖)

v2 改进：
  - 移除 clawhub install 调用（网络不稳定）
  - 移除 npx openskills update 调用（不再需要 OpenSkill Node 生态）
  - 统一走本地 ZIP 导入 + REST API 注册
"""

import os
import sys
import shutil
import time
import zipfile


def import_clawhub_skill(skill_name: str):
    """（已废弃）从 ClawHub 网络导入技能

    v2 中不再依赖 clawhub CLI。请使用 REST API 上传技能 ZIP 包。
    """
    print("❌ clawhub 网络导入已废弃。")
    print("请通过以下方式导入自定义技能：")
    print(f"  1. 准备技能 ZIP 包（包含 SKILL.md + execution.yaml + scripts/）")
    print(f"  2. 调用 POST /api/v1/skills/upload 上传")
    print(f"  3. 技能将由 DynamicSkillRegistry 按需加载")
    sys.exit(1)


def import_local_skill_from_zip(
    zip_path: str, skill_name: str = None, prefix: str = "custom_"
):
    """从本地 ZIP 文件导入技能到 user_skills/{user_id}/ 目录

    Args:
        zip_path: ZIP 文件的完整路径
        skill_name: 技能名称（可选），不提供则从 ZIP 文件名推断
        prefix: 技能前缀（默认 "custom_"）
    """
    base_dir = "./.claude/skills"

    if not os.path.exists(zip_path):
        print(f"❌ ZIP 文件不存在: {zip_path}")
        sys.exit(1)

    if not zipfile.is_zipfile(zip_path):
        print(f"❌ 文件不是有效的 ZIP 格式: {zip_path}")
        sys.exit(1)

    if not skill_name:
        skill_name = os.path.splitext(os.path.basename(zip_path))[0]

    print(f"\n🚀 开始导入本地技能: {skill_name}...")

    temp_extract_dir = os.path.join(base_dir, "_temp_extract_" + skill_name)

    try:
        print("▶ 正在解压文件到临时目录...")

        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(temp_extract_dir)

        print("✅ 解压完成")
    except Exception as e:
        print(f"❌ 解压失败: {e}")
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)
        sys.exit(1)

    extracted_items = os.listdir(temp_extract_dir)

    if len(extracted_items) == 1 and os.path.isdir(
        os.path.join(temp_extract_dir, extracted_items[0])
    ):
        skill_content_dir = os.path.join(temp_extract_dir, extracted_items[0])
        print(f"▶ 检测到嵌套目录: {extracted_items[0]}")
    else:
        skill_content_dir = temp_extract_dir

    skill_md_path = os.path.join(skill_content_dir, "SKILL.md")
    if not os.path.exists(skill_md_path):
        print("⚠️ 警告: SKILL.md 未找到，技能可能无法正常使用")

    new_skill_name = f"{prefix}{skill_name}"
    final_skill_path = os.path.join(base_dir, new_skill_name)

    print(f"▶ 移动技能到: {new_skill_name}")

    try:
        time.sleep(1)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                if os.path.exists(final_skill_path):
                    shutil.rmtree(final_skill_path)

                shutil.move(skill_content_dir, final_skill_path)
                print(f"✅ 技能已安装: {new_skill_name}")
                break

            except PermissionError:
                if attempt < max_retries - 1:
                    print(
                        f"⚠️ 文件被占用，正在重试 ({attempt + 1}/{max_retries})..."
                    )
                    time.sleep(2)
                else:
                    print("❌ 安装失败：文件被死锁")
                    sys.exit(1)
    except Exception as e:
        print(f"❌ 移动文件失败: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

    # v2: 不再需要 npx openskills update
    print(f"✅ 技能已安装到 {final_skill_path}")
    print(f"\n🎉 导入完毕！技能将在下次调用时由 DynamicSkillRegistry 自动加载。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python import_skill.py --upload <zip_file_path> [skill_name]")
        print("      python import_skill.py <skill_name>  # 从 ClawHub 导入（已废弃）")
        sys.exit(1)

    if sys.argv[1] == "--upload":
        if len(sys.argv) < 3:
            print(
                "用法: python import_skill.py --upload <zip_file_path> [skill_name]"
            )
            sys.exit(1)

        zip_file_path = sys.argv[2]
        skill_name_arg = sys.argv[3] if len(sys.argv) > 3 else None
        import_local_skill_from_zip(zip_file_path, skill_name_arg)
    else:
        target_skill = sys.argv[1]
        import_clawhub_skill(target_skill)
