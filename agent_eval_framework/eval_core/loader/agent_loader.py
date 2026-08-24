"""AgentCaseLoader（含 ProfileResolver）：读取 JSON -> 解析 profile 引用 -> Pydantic 校验。

对应《Agent评测框架设计方案》4.2 用例加载模块。
加载流程：
  1. 读取 JSON
  2. 如果有 "profile" 字段 -> ProfileResolver 查找 profiles/ 目录并合并 setup 数据
  3. golden 等可选 section 已在单文件内，直接读取
  4. Pydantic 校验
"""
import json
from pathlib import Path

from eval_core.models import AgentCase, AgentSetup


class ProfileResolver:
    """根据 profile 引用名查找 profiles/ 目录，加载并合并 setup 数据。

    查找规则：在 profiles_dir 下递归搜索 "<name>.json"
    （profiles 按 Agent 类型组织为子目录，如 profiles/companion/xxx.json）
    同名冲突：若多个子目录存在同名文件，报错。
    只映射 AgentSetup 的已知字段，profile_id / description 等元数据字段被忽略。
    """

    def __init__(self, profiles_dir: Path):
        self.profiles_dir = profiles_dir

    def resolve(self, data: dict) -> dict:
        """解析 data["profile"] 引用，将结果写入 data["setup"]。

        case 内若已有 setup（不应出现，case 文件中不直接填写 setup），
        profile 解析结果仍以 case 文件约定为准，此处直接覆盖。
        """
        profile_name = data.get("profile", "")
        if not profile_name:
            return data

        profile = self._load_profile(profile_name)
        data["setup"] = self._map_to_setup(profile)
        return data

    def _load_profile(self, name: str) -> dict:
        matches = list(self.profiles_dir.rglob(f"{name}.json"))
        if not matches:
            raise FileNotFoundError(
                f"用例引用的 profile 不存在: '{name}' "
                f"(搜索目录: {self.profiles_dir})"
            )
        if len(matches) > 1:
            found = ", ".join(str(m) for m in matches)
            raise ValueError(f"profile 名称冲突 '{name}'，存在多个匹配: {found}")
        return json.loads(matches[0].read_text(encoding="utf-8"))

    @staticmethod
    def _map_to_setup(profile: dict) -> dict:
        """将 profile 文件数据映射为 AgentSetup 的已知字段。

        只映射 AgentSetup 已定义的字段，忽略 profile_id / description 等元数据。
        """
        setup_fields = set(AgentSetup.model_fields.keys())
        return {k: v for k, v in profile.items() if k in setup_fields}


class AgentCaseLoader:
    """Agent 用例加载器"""

    def __init__(self, profiles_dir: Path):
        self.profile_resolver = ProfileResolver(profiles_dir)

    def load(self, data: dict) -> AgentCase:
        """加载并校验单个 Agent 用例。

        Args:
            data: 已解析的 JSON 字典（由 LoaderRegistry 传入，避免重复 IO）

        Raises:
            FileNotFoundError: Profile 文件不存在
            ValueError: Profile 格式错误
            pydantic.ValidationError: 字段校验失败
        """
        # 第 1 步：解析 profile 引用 -> 填充 setup
        data = self.profile_resolver.resolve(data)

        # 第 2 步：Pydantic 校验（golden / trace 等已在单文件内）
        return AgentCase.model_validate(data)
