"""생성된 코드 검증."""

import ast
import subprocess
from pathlib import Path
from typing import Any


class CodeValidator:
    """코드 검증기."""

    ALLOWED_IMPORTS = {
        "llmtrader.strategy.base",
        "llmtrader.strategy.context",
        "typing",
    }

    FORBIDDEN_MODULES = {
        "os",
        "subprocess",
        "sys",
        "requests",
        "urllib",
        "socket",
        "eval",
        "exec",
        "__import__",
    }

    def validate(self, code: str) -> tuple[bool, list[str]]:
        """코드 검증.

        Args:
            code: 검증할 Python 코드

        Returns:
            (성공 여부, 오류 메시지 리스트)
        """
        errors: list[str] = []

        # 1. 구문 검증 (AST 파싱)
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(f"Syntax error: {e}")
            return False, errors

        # 2. import 검증
        import_errors = self._check_imports(tree)
        if import_errors:
            errors.extend(import_errors)

        # 3. 위험 함수 검증
        danger_errors = self._check_dangerous_calls(tree)
        if danger_errors:
            errors.extend(danger_errors)

        # 4. Strategy 클래스 존재 검증
        if not self._has_strategy_class(tree):
            errors.append("No Strategy subclass found")

        if errors:
            return False, errors

        return True, []

    def _check_imports(self, tree: ast.AST) -> list[str]:
        """import 문 검증.

        Args:
            tree: AST 트리

        Returns:
            오류 메시지 리스트
        """
        errors: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.FORBIDDEN_MODULES:
                        errors.append(f"Forbidden import: {alias.name}")
                    elif alias.name not in self.ALLOWED_IMPORTS:
                        # 경고만 (허용 목록 외는 차단하지 않음)
                        pass

            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in self.FORBIDDEN_MODULES:
                    errors.append(f"Forbidden import from: {node.module}")

        return errors

    def _check_dangerous_calls(self, tree: ast.AST) -> list[str]:
        """위험 함수 호출 검증.

        Args:
            tree: AST 트리

        Returns:
            오류 메시지 리스트
        """
        errors: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in {"eval", "exec", "__import__"}:
                        errors.append(f"Forbidden function call: {node.func.id}")

        return errors

    def _has_strategy_class(self, tree: ast.AST) -> bool:
        """Strategy 상속 클래스 존재 여부 확인.

        Args:
            tree: AST 트리

        Returns:
            Strategy 클래스가 있으면 True
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "Strategy":
                        return True
        return False

    def format_code(self, code: str, temp_path: Path) -> tuple[bool, str]:
        """black으로 코드 포맷팅.

        Args:
            code: 원본 코드
            temp_path: 임시 파일 경로

        Returns:
            (성공 여부, 포맷된 코드 또는 오류 메시지)
        """
        temp_path.write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                ["black", "--quiet", str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                formatted_code = temp_path.read_text(encoding="utf-8")
                return True, formatted_code
            return False, result.stderr

        except subprocess.TimeoutExpired:
            return False, "black formatting timed out"
        except FileNotFoundError:
            # black이 없으면 원본 코드 반환
            return True, code
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def lint_code(self, code: str, temp_path: Path) -> tuple[bool, list[str]]:
        """ruff로 코드 린트.

        Args:
            code: 검증할 코드
            temp_path: 임시 파일 경로

        Returns:
            (성공 여부, 경고/오류 메시지 리스트)
        """
        temp_path.write_text(code, encoding="utf-8")

        try:
            result = subprocess.run(
                ["ruff", "check", str(temp_path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            messages = result.stdout.strip().split("\n") if result.stdout else []
            # ruff는 0=문제없음, 1=문제있음
            success = result.returncode == 0
            return success, messages

        except subprocess.TimeoutExpired:
            return False, ["ruff linting timed out"]
        except FileNotFoundError:
            # ruff가 없으면 스킵
            return True, []
        finally:
            if temp_path.exists():
                temp_path.unlink()




