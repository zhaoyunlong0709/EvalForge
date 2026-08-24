"""数据源适配器。未来接入真实数据源时在此注册。"""
from .base import BaseCaseSource, ManualSource, RawCase

__all__ = ["BaseCaseSource", "ManualSource", "RawCase"]
