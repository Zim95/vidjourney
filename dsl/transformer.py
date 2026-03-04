from __future__ import annotations

from dataclasses import dataclass

from lark import Token, Tree

from .scene_model import ElementModel, MovementModel, SceneModel, SequenceStepModel


@dataclass
class SceneModelTransformer:
    def transform(self, ast: Tree) -> SceneModel:
        element_blocks = [child for child in ast.children if isinstance(child, Tree) and child.data == "element_block"]
        sequence_blocks = [child for child in ast.children if isinstance(child, Tree) and child.data == "sequence_block"]

        elements = [self._transform_element_block(block) for block in element_blocks]
        sequence = sequence_blocks and self._transform_sequence_block(sequence_blocks[0]) or []
        return SceneModel(elements=elements, sequence=sequence)

    def _transform_element_block(self, block: Tree) -> ElementModel:
        name = self._token_value(block.children[1])
        element_type = self._extract_element_type(block)

        model = ElementModel(name=name, type=element_type)

        statement_trees = [child for child in block.children if isinstance(child, Tree) and child.data == "element_stmt"]
        for stmt_wrapper in statement_trees:
            statement = stmt_wrapper.children[0]
            self._apply_statement(model, statement)

        return model

    def _apply_statement(self, model: ElementModel, statement: Tree) -> None:
        handlers = {
            "label_stmt": lambda stmt: setattr(model, "text", self._unquote(self._token_value(stmt.children[1]))),
            "position_stmt": lambda stmt: setattr(model, "position", self._point_from_tree(stmt.children[1])),
            "size_stmt": lambda stmt: setattr(model, "size", self._number_token(stmt.children[1])),
            "shape_stmt": lambda stmt: setattr(model, "shape", self._token_value(stmt.children[1]).lower()),
            "url_stmt": lambda stmt: setattr(model, "url", self._unquote(self._token_value(stmt.children[1]))),
            "fill_stmt": lambda stmt: setattr(model, "fill_color", self._color_from_node(stmt.children[1])),
            "border_stmt": lambda stmt: setattr(model, "border_color", self._color_from_node(stmt.children[1])),
            "spawn_stmt": lambda stmt: setattr(model, "spawn_animation", self._animation_from_stmt(stmt)),
            "idle_stmt": lambda stmt: setattr(model, "idle_animation", self._animation_from_stmt(stmt)),
            "remove_stmt": lambda stmt: setattr(model, "remove_animation", self._animation_from_stmt(stmt)),
            "move_stmt": lambda stmt: self._apply_move_statement(model, stmt),
        }
        handlers.get(statement.data, lambda _stmt: None)(statement)

    def _apply_move_statement(self, model: ElementModel, stmt: Tree) -> None:
        if model.type == "arrow":
            self._apply_arrow_move_statement(model, stmt)
            return
        model.movement = self._movement_from_stmt(stmt, model.position)

    def _apply_arrow_move_statement(self, model: ElementModel, stmt: Tree) -> None:
        path = self._path_from_move_tree(stmt.children[2], model.position)
        if len(path) < 2:
            return
        model.from_position = path[0]
        model.to_position = path[-1]
        interior_points = path[1:-1]
        model.path_points = interior_points if interior_points else None

    def _transform_sequence_block(self, block: Tree) -> list[SequenceStepModel]:
        statements = [child for child in block.children if isinstance(child, Tree) and child.data == "sequence_stmt"]
        return [self._sequence_from_stmt(statement) for statement in statements]

    def _sequence_from_stmt(self, statement: Tree) -> SequenceStepModel:
        action_token = statement.children[0]
        action = self._token_value(action_token).lower()
        wait_handler = {
            True: lambda: SequenceStepModel(action=action, duration=self._number_token(statement.children[1])),
            False: lambda: SequenceStepModel(action=action, target=self._token_value(statement.children[1])),
        }
        return wait_handler[action == "wait"]()

    def _movement_from_stmt(self, stmt: Tree, current_position: list[float] | None) -> MovementModel:
        movement_type = self._token_value(stmt.children[1]).lower()
        path = self._path_from_move_tree(stmt.children[2], current_position)
        duration = self._number_token(stmt.children[4])

        optional_nodes = [child for child in stmt.children[5:] if isinstance(child, Tree)]
        repeat = next((self._int_token(node.children[1]) for node in optional_nodes if node.data == "repeat_clause"), 1)
        reverse = any(node.data == "reverse_clause" for node in optional_nodes)

        return MovementModel(type=movement_type, path=path, duration=duration, repeat=repeat, reverse=reverse)

    def _path_from_move_tree(self, move_path: Tree, current_position: list[float] | None) -> list[list[float]]:
        mode = self._token_value(move_path.children[0]).lower()
        path_resolvers = {
            "path": lambda: self._points_from_list_tree(move_path.children[1]),
            "to": lambda: self._path_from_to_point(move_path.children[1], current_position),
        }
        return path_resolvers.get(mode, lambda: [])()

    def _path_from_to_point(self, point_tree: Tree, current_position: list[float] | None) -> list[list[float]]:
        target = self._point_from_tree(point_tree)
        start = current_position if current_position is not None else [0.0, 0.0]
        return [start, target]

    @staticmethod
    def _points_from_list_tree(point_list_tree: Tree) -> list[list[float]]:
        points = [child for child in point_list_tree.children if isinstance(child, Tree) and child.data == "point"]
        return [SceneModelTransformer._point_from_tree(point) for point in points]

    @staticmethod
    def _point_from_tree(point_tree: Tree) -> list[float]:
        return [
            SceneModelTransformer._number_token(point_tree.children[0]),
            SceneModelTransformer._number_token(point_tree.children[1]),
        ]

    @staticmethod
    def _animation_from_stmt(stmt: Tree) -> dict[str, float | str]:
        return {
            "type": SceneModelTransformer._token_value(stmt.children[1]).lower(),
            "duration": SceneModelTransformer._number_token(stmt.children[2]),
        }

    @staticmethod
    def _color_from_node(node: Tree | Token) -> str:
        raw = SceneModelTransformer._token_value(node)
        return SceneModelTransformer._unquote(raw)

    @staticmethod
    def _extract_element_type(block: Tree) -> str:
        type_tree = next(child for child in block.children if isinstance(child, Tree) and child.data == "element_type")
        return SceneModelTransformer._token_value(type_tree.children[0]).lower()

    @staticmethod
    def _token_value(node: Tree | Token) -> str:
        token = node if isinstance(node, Token) else node.children[0]
        return str(token)

    @staticmethod
    def _unquote(value: str) -> str:
        quoted = len(value) >= 2 and value[0] == '"' and value[-1] == '"'
        return {True: value[1:-1], False: value}[quoted]

    @staticmethod
    def _number_token(token: Tree | Token) -> float:
        value = SceneModelTransformer._token_value(token)
        return float(value)

    @staticmethod
    def _int_token(token: Tree | Token) -> int:
        value = SceneModelTransformer._token_value(token)
        return int(value)
