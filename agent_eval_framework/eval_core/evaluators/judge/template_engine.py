"""Judge 模板引擎：模板查找 -> 变量填充。

对应《LLM-as-Judge评测执行流程》第 3 步（组装 Judge Prompt）。

固定变量（框架自动填充）：
- {conversation_history}  所有轮次的用户输入，格式化为自然语言
- {actual_reply}          Agent 最后一轮的实际回复
- {golden_reference}      参考答案，golden.expected_answer 或 "无参考答案"
- {case.*}                evaluation.params 中的任意字段
"""
import re

from eval_core.models import AgentCase


class TemplateError(Exception):
    """模板查找或变量填充失败"""


class TemplateEngine:
    """Judge prompt 模板引擎"""

    def __init__(self, templates: dict[str, str]):
        """templates: 模板名 -> 模板文本（来自 config/prompt_templates.json）"""
        self.templates = templates

    def build_prompt(self, case: AgentCase) -> str:
        """查找模板并填充变量，返回完整 prompt。

        Raises:
            TemplateError: 模板未定义 或 模板变量未提供
        """
        template_name = case.evaluation.template
        template = self.templates.get(template_name)
        if template is None:
            known = ", ".join(sorted(self.templates.keys()))
            raise TemplateError(
                f"模板 '{template_name}' 未定义（已注册: {known}）"
            )

        variables = self._build_variables(case)
        return self._fill(template, variables)

    @staticmethod
    def _build_variables(case: AgentCase) -> dict:
        """构建模板变量（固定变量 + evaluation.params）。"""
        variables: dict = {}

        # conversation_history：所有轮次的用户输入（按 LLM-as-Judge 文档第 3 步格式）
        history_lines = []
        for turn in case.conversation:
            history_lines.append(f"第{turn.turn}轮 用户：{turn.user}")
        variables["conversation_history"] = "\n".join(history_lines)

        # actual_reply：Agent 最后一轮的实际回复
        variables["actual_reply"] = case.result.actual_reply or "（无回复）"

        # golden_reference：参考答案（golden 可选，无则填"无参考答案"）
        if case.golden and case.golden.expected_answer:
            variables["golden_reference"] = case.golden.expected_answer
        else:
            variables["golden_reference"] = "无参考答案"

        # 合并 evaluation.params
        variables.update(case.evaluation.params)

        return variables

    @staticmethod
    def _fill(template: str, variables: dict) -> str:
        """单趟填充模板变量。缺失变量报错，列表值用顿号连接。"""
        def repl(m: re.Match) -> str:
            var = m.group(1)
            if var not in variables:
                raise TemplateError(f"模板变量 '{var}' 未提供")
            value = variables[var]
            if isinstance(value, list):
                return "、".join(str(v) for v in value)
            return str(value)

        return re.sub(r"\{(\w+)\}", repl, template)
