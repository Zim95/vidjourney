from __future__ import annotations

from pathlib import Path

from src.config.constants import DSL_GRAMMAR_FILE, DSL_SCENE_FILE
from lark import Lark, Tree


def build_parser(grammar_path: Path = DSL_GRAMMAR_FILE) -> Lark:
    grammar = grammar_path.read_text(encoding="utf-8")
    return Lark(grammar, parser="lalr", start="start")


def parse_scene(scene_path: Path = DSL_SCENE_FILE, grammar_path: Path = DSL_GRAMMAR_FILE) -> Tree:
    parser = build_parser(grammar_path)
    scene_source = scene_path.read_text(encoding="utf-8")
    return parser.parse(scene_source)
