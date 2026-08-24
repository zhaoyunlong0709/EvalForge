.PHONY: install dev-install test lint clean run-smoke run-regression

# 安装依赖
install:
	pip install -e .

# 安装开发依赖（含 pytest-asyncio）
dev-install:
	pip install -e ".[dev]"

# 运行单元测试
test:
	pytest tests/ -v

# L1 冒烟（tags=smoke）
run-smoke:
	eval-forge run --tags smoke

# L2 回归（P0+P1）
run-regression:
	eval-forge run --priority P0 --priority P1

# 清理生成物
clean:
	rm -rf reports/*.json reports/*.md
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
