"""Проверка перевода: все строки интерфейса из кода и шаблонов есть в i18n_en.py.

Строки ищутся по AST: аргументы t()/tr(), шаблоны журнала (record/log), тексты HubError/LoginError,
слова для pl() и русские строки в таблицах-константах. Логи (log.*) и докстринги не считаются.
Запуск из папки hub:  python tests/i18n_check.py        (код выхода 1 — есть непереведённое)
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from i18n_en import EN, EN_PL  # noqa: E402

CYR = re.compile(r"[а-яА-ЯёЁ]")
SKIP_CALLS = {"debug", "info", "warning", "error", "exception"}   # log.*
FILES = [p for p in ROOT.glob("*.py") if p.name not in ("i18n.py", "i18n_en.py", "config.py")] + list((ROOT / "screens").glob("*.py"))


def strings(node) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str) and CYR.search(n.value)]


def collect() -> tuple[set[str], set[tuple[str, str, str]], list[str]]:
    keys: set[str] = set()
    plurals: set[tuple[str, str, str]] = set()
    fstrings: list[str] = []
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()
        for node in ast.walk(tree):
            # докстринги
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    skip.add(id(first.value))
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name in SKIP_CALLS or (isinstance(node.func, ast.Attribute) and name == "log" and False):
                    for n in ast.walk(node):
                        skip.add(id(n))
                if name == "pl":
                    args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                    if len(args) == 3:
                        plurals.add(tuple(args))
                        for a in node.args:
                            skip.add(id(a))
            if isinstance(node, ast.JoinedStr):
                for part in node.values:
                    if isinstance(part, ast.Constant):
                        skip.add(id(part))
                        if CYR.search(part.value):
                            fstrings.append(f"{path.name}:{node.lineno}: {part.value!r}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and CYR.search(node.value) and id(node) not in skip \
                    and "CREATE TABLE" not in node.value:
                keys.add(node.value)
    for tpl in (ROOT / "templates").glob("*.html"):
        for m in re.finditer(r"""\bt\((["'])(.+?)\1""", tpl.read_text(encoding="utf-8"), re.S):
            keys.add(m.group(2))
    return keys, plurals, fstrings


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    keys, plurals, fstrings = collect()
    missing = sorted(k for k in keys if k not in EN)
    missing_pl = sorted({p for p in plurals if p[2] not in EN_PL})
    for k in missing:
        print("KEY", repr(k))
    for p in missing_pl:
        print("PL ", repr(p))
    if "-v" in sys.argv:
        for f in fstrings:
            print("FSTR", f)
    print(f"строк: {len(keys)}, множественных: {len(plurals)} · без перевода: {len(missing)} + {len(missing_pl)}")
    return 1 if missing or missing_pl else 0


if __name__ == "__main__":
    sys.exit(main())
