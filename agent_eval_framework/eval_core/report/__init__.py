"""报告生成包：Console + JSON + Markdown + Builder。"""
from .console import ConsoleReporter
from .json_reporter import JSONReporter
from .markdown_reporter import MarkdownReporter
from .builder import ReportBuilder

__all__ = ["ConsoleReporter", "JSONReporter", "MarkdownReporter", "ReportBuilder"]
