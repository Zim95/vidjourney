from __future__ import annotations

import argparse

from renderer import RendererType, run_scene, set_renderer

# Sample Input (SERVER CACHE ARROW test case)
# -------------------------------------------
# This is the logical input we want to render through the adaptor layer:
#
# elements = [
#     {
#         "name": "SERVER",
#         "shape": "square",
#         "position": (2.5, 0.0),
#         "size": 1.6,
#         "text": "SERVER",
#         "spawn_animation": "shape_popup",
#         "remove_animation": "shape_popout",
#     },
#     {
#         "name": "CACHE",
#         "shape": "square",
#         "position": (-2.5, 0.0),
#         "size": 1.6,
#         "text": "CACHE",
#         "spawn_animation": "shape_popup",
#         "remove_animation": "shape_popout",
#     },
#     {
#         "name": "CACHE_TO_SERVER",
#         "type": "unidirectional_dotted_arrow",
#         "from": (-1.5, 0.0, 0.0),
#         "to": (1.5, 0.0, 0.0),
#         "spawn_animation": "unidirectional_dotted_spawn",
#         "idle_animation": "dotted_unidirectional_idle",
#         "remove_animation": "unidirectional_dotted_remove",
#     },
# ]
#
# sequence = [
#     ("spawn", "SERVER"),
#.    ("wait", 3),
#     ("spawn", "CACHE"),
#     ("wait", 3),
#     ("spawn", "CACHE_TO_SERVER"),
#     ("wait", 3),
#     ("close", "CACHE_TO_SERVER"),
#     ("wait", 0.1),
#     ("close", "CACHE"),
#     ("wait", 0.1),
#     ("close", "SERVER"),
# ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Renderer scene runner")
    parser.add_argument("--renderer", default="manim", choices=[renderer.value for renderer in RendererType])
    parser.add_argument("--scene-file", default="main.py")
    parser.add_argument("--scene", default="ServerScene")
    parser.add_argument("--quality", default="ql", help="Manim quality flag suffix, e.g. ql, qm, qh")
    parser.add_argument("--no-preview", action="store_true", help="Disable preview when running manim")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    set_renderer(RendererType(args.renderer))
    run_scene(
        scene_file=args.scene_file,
        scene_class=args.scene,
        quality=args.quality,
        preview=not args.no_preview,
    )


if __name__ == "__main__":
    main()
